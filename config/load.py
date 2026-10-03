#!/usr/bin/env python3
"""SBC-GS layered configuration loader (Python, stdlib only). Same semantics as config/load.sh; design: docs/CONFIG.md.

Precedence (high to low): CLI flag > environment > extra file > per-host file > profile file > built-in default.
Files are parsed as DATA (strict KEY=VALUE, quotes only), never evaluated. Every value is validated against
config/registry.tsv; unknown keys are rejected. SAFETY keys have hard bounds that only SBC_GS_I_KNOW=1 or --i-know relaxes.

    cfg = load.load(["tx12"], argv=sys.argv[1:])      # exits with status 2 and a message on a configuration error
    cfg["TX12_DEADMAN_MS"]                            # typed value (int / float / str)
    cfg.src["TX12_DEADMAN_MS"]                        # "default", "profile:x", "host:/path", "file:/path", "env:NAME"
    cfg.check_cli("TX12_DEADMAN_MS", 20)              # validate a value that came from a CLI flag (raises ConfigError)

CLI:  python3 config/load.py show [--defaults] [--owner O]... | get KEY | check FILE [OWNER] | keys
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "registry.tsv")

_LINE = re.compile(r"""^[ \t]*([A-Z][A-Z0-9_]*)=('([^'$`\\]*)'|"([^"$`\\]*)"|([A-Za-z0-9._:/@%+,-]*))([ \t]+#.*)?[ \t]*$""")
_BLANK = re.compile(r"^[ \t]*(#.*)?$")


class ConfigError(Exception):
    pass


class Row:
    __slots__ = ("key", "default", "type", "min", "max", "unit", "safe", "owners", "env", "desc")


def registry(path=None):
    rows = {}
    with open(path or os.environ.get("SBC_CFG_REGISTRY") or REGISTRY, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) != 10 or not p[9]:
                raise ConfigError(f"registry row for '{p[0]}' has fewer than 10 fields")
            r = Row()
            r.key, r.default, r.type, r.min, r.max, r.unit, r.safe, own, r.env, r.desc = p
            if r.key in rows:
                raise ConfigError(f"registry key '{r.key}' is defined twice")
            r.default = "" if r.default == "~" else r.default
            r.safe = r.safe == "y"
            r.owners = own.split(",")
            r.env = f"SBC_GS_{r.key}" if r.env == "-" else r.env
            rows[r.key] = r
    if not rows:
        raise ConfigError("registry is empty")
    return rows


def parse_file(path, rows, owner=None):
    """Strict data parser. Returns {KEY: value}. Raises ConfigError with file:line."""
    try:
        with open(path, "rb") as f:
            raw = f.read().decode("utf-8")
    except OSError:
        raise ConfigError(f"cannot read {path}")
    except UnicodeDecodeError:
        raise ConfigError(f"{path}: not valid UTF-8")
    if raw.startswith("﻿"):
        raw = raw[1:]
    out = {}
    for n, line in enumerate(raw.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        if n == len(raw.split("\n")) and line == "":
            continue
        if _BLANK.match(line):
            continue
        if re.search(r"[\x00-\x08\x0a-\x1f\x7f]", line):
            raise ConfigError(f"{path}:{n}: control character in line")
        m = _LINE.match(line)
        if not m:
            raise ConfigError(f"{path}:{n}: not a KEY=VALUE line (only KEY=VALUE, KEY='value' or KEY=\"value\"; no $, backticks, "
                              f"backslashes, ';', '(' or other shell syntax): {line[:60]}")
        key = m.group(1)
        q = m.group(2)[:1]
        val = m.group(3) if q == "'" else m.group(4) if q == '"' else m.group(5)
        if key not in rows:
            raise ConfigError(f"{path}:{n}: unknown key '{key}' (not in registry.tsv)")
        if owner and owner not in rows[key].owners:
            raise ConfigError(f"{path}:{n}: key '{key}' does not belong to {owner}")
        if key in out:
            raise ConfigError(f"{path}:{n}: key '{key}' is set twice")
        out[key] = val
    return out


def _num(s):
    return float(s)


def check_value(r, v):
    """Returns (typed value, None) | raises ConfigError(msg) | returns (value, bound_msg) when only the hard bounds of a
    safety key are exceeded (the caller decides on --i-know)."""
    t = r.type
    empty_ok = t.endswith("?")
    if empty_ok:
        t = t[:-1]
    if v == "":
        if empty_ok or t == "str":
            return "", None
        raise ConfigError(f"{r.key} is empty but type {t} needs a value")
    if t in ("int", "port"):
        if not re.fullmatch(r"-?[0-9]{1,15}", v):
            raise ConfigError(f"{r.key}='{v}' is not an integer")
        val = int(v)
        if t == "port" and not 1 <= val <= 65535:
            raise ConfigError(f"{r.key}='{v}' is not a port in 1..65535")
    elif t == "float":
        if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", v) or len(v) > 20:
            raise ConfigError(f"{r.key}='{v}' is not a decimal number")
        val = float(v)
    elif t == "ip":
        m = re.fullmatch(r"([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})", v)
        if not m or any(int(x) > 255 for x in m.groups()):
            raise ConfigError(f"{r.key}='{v}' is not an IPv4 address")
        return v, None
    elif t == "path":
        if not re.fullmatch(r"/[A-Za-z0-9._/%:+@,-]*", v) or "/../" in v or v.endswith("/.."):
            raise ConfigError(f"{r.key}='{v}' is not a safe absolute path")
        return v, None
    elif t == "bool":
        if v not in ("0", "1"):
            raise ConfigError(f"{r.key}='{v}' must be 0 or 1")
        return int(v), None
    elif t.startswith("enum:"):
        if v not in t[5:].split("|"):
            raise ConfigError(f"{r.key}='{v}' must be one of {t[5:]}")
        return v, None
    elif t == "str":
        return v, None
    else:
        raise ConfigError(f"registry type '{t}' of {r.key} is unknown")
    bound = None
    if r.min != "-" and not val >= _num(r.min):
        bound = f"{r.key}={v} is below the minimum {r.min}"
    elif r.max != "-" and not val <= _num(r.max):
        bound = f"{r.key}={v} is above the maximum {r.max}"
    if bound and not r.safe:
        raise ConfigError(bound)
    return val, bound


class Config(dict):
    def __init__(self):
        super().__init__()
        self.src = {}
        self.raw = {}
        self.rows = {}
        self.i_know = False

    def check_cli(self, key, value):
        """Validate a CLI-supplied value like any other layer (hard bounds of safety keys included)."""
        r = self.rows[key]
        try:
            _, bound = check_value(r, str(value) if not isinstance(value, float) else repr(value))
        except ConfigError as e:
            raise ConfigError(f"{e} [cli]")
        if bound:
            _bound_violation(r, bound, "cli", self.i_know)


def _bound_violation(r, bound, layer, i_know):
    if i_know:
        print(f"sbc-gs-config: WARNING: SAFETY OVERRIDE (SBC_GS_I_KNOW/--i-know): {bound} (hard bounds {r.min}..{r.max}, from {layer}); "
              "accepted, YOU own the consequences", file=sys.stderr)
        return
    raise ConfigError(f"{bound} (safety key, hard bounds {r.min}..{r.max}; only SBC_GS_I_KNOW=1 or --i-know relaxes this) [{layer}]")


def i_know_from(argv=None, env=None):
    env = os.environ if env is None else env
    return env.get("SBC_GS_I_KNOW", "0") == "1" or "--i-know" in (argv or [])


def resolve(owners=None, argv=None, env=None, extra_file=None, extra_owner=None, defaults_only=False,
            value_check=True, i_know=None, registry_path=None):
    env = os.environ if env is None else env
    rows = registry(registry_path)
    cfg = Config()
    cfg.rows = rows
    cfg.i_know = i_know_from(argv, env) if i_know is None else (i_know or env.get("SBC_GS_I_KNOW") == "1")
    prof = host = ext = {}
    plab = hlab = elab = ""
    if not defaults_only:
        name = env.get("SBC_GS_PROFILE", "")
        if name:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ConfigError(f"SBC_GS_PROFILE='{name}' must match [A-Za-z0-9_-]+")
            pfile = os.path.join(env.get("SBC_GS_PROFILE_DIR") or os.path.join(HERE, "profiles"), name + ".env")
            if not os.path.isfile(pfile):
                raise ConfigError(f"profile '{name}' not found ({pfile})")
            prof, plab = parse_file(pfile, rows), f"profile:{name}"
        hfile = env.get("SBC_GS_CONFIG") or "/config/sbc-gs.env"
        if os.path.isfile(hfile):
            host, hlab = parse_file(hfile, rows), f"host:{hfile}"
        elif os.path.exists(hfile):
            raise ConfigError(f"cannot read {hfile}")
        if extra_file and os.path.isfile(extra_file):
            ext, elab = parse_file(extra_file, rows, extra_owner), f"file:{extra_file}"
        elif extra_file and os.path.exists(extra_file):
            raise ConfigError(f"cannot read {extra_file}")
    for k, r in rows.items():
        if owners and not any(o in r.owners for o in owners):
            continue
        v, layer = r.default, "default"
        if not defaults_only:
            if k in prof:
                v, layer = prof[k], plab
            if k in host:
                v, layer = host[k], hlab
            if k in ext:
                v, layer = ext[k], elab
            if env.get(r.env):
                v, layer = env[r.env], f"env:{r.env}"
        val = v
        if value_check or layer == "default":
            try:
                val, bound = check_value(r, v)
            except ConfigError as e:
                raise ConfigError(f"{e} [{layer}]")
            if bound:
                _bound_violation(r, bound, layer, cfg.i_know)
        cfg[k] = val
        cfg.src[k] = layer
        cfg.raw[k] = v
    return cfg


def load(owners=None, **kw):
    """resolve() that prints the error and exits with status 2 (fail-closed, like the shell loader)."""
    try:
        return resolve(owners, **kw)
    except ConfigError as e:
        print(f"sbc-gs-config: error: {e}", file=sys.stderr)
        sys.exit(2)


def show(cfg):
    out = []
    for k, v in cfg.items():
        raw = cfg.raw[k]
        out.append(f"{k}={raw}\t# {cfg.src[k]}{' SAFETY' if cfg.rows[k].safe else ''}")
    return "\n".join(out)


def main(argv):
    cmd = argv[0] if argv else ""
    try:
        if cmd == "show":
            owners, dflt, ik = [], False, False
            it = iter(argv[1:])
            for a in it:
                if a == "--defaults":
                    dflt = True
                elif a == "--i-know":
                    ik = True
                elif a == "--owner":
                    owners.append(next(it))
                else:
                    raise SystemExit(__doc__)
            print(show(resolve(owners or None, defaults_only=dflt, i_know=ik)))
        elif cmd == "get" and len(argv) == 2:
            cfg = resolve()
            if argv[1] not in cfg:
                raise ConfigError(f"unknown key '{argv[1]}'")
            print(cfg.raw[argv[1]])
        elif cmd == "check" and len(argv) >= 2:
            rows = registry()
            d = parse_file(argv[1], rows, argv[2] if len(argv) > 2 else None)
            for k, v in d.items():
                check_value(rows[k], v)
            print(f"ok: {argv[1]} ({len(d)} keys)")
        elif cmd == "keys":
            print("\n".join(registry()))
        else:
            raise SystemExit(__doc__)
    except ConfigError as e:
        print(f"sbc-gs-config: error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
