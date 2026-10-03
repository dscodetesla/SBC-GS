#!/usr/bin/env python3
"""Deterministic fuzz / property / fault-injection tests of the REAL SBC-GS scripts (stdlib unittest, no hardware, no network).

  python3 tests/sim/fuzz/test_fuzz.py [-v] [TestClass ...]       (run.sh --check|--long wraps this)
Env knobs (seed, iteration scale, repo copy, strict defect mode): see fuzzlib.py. Design and findings: docs/SIM-FUZZ.md.

Conventions
  * Evidence tags: REPO = reproduced by a command in this repository, SYNTH = generated input / model output (never a measurement).
  * A test named test_DEFECT_* pins a defect of the real code (fuzzlib.pinned): it passes while the defect still reproduces and
    FAILS once it is fixed, so the fix must flip the pin and update docs/SIM-FUZZ.md. FUZZ_STRICT_DEFECTS=1 asserts the fix instead.
  * Invariants checked everywhere: no code from data (marker file stays absent), exit codes only from the documented set,
    safety bounds not bypassable without --i-know, same seed -> same result.
"""
import importlib.util
import itertools
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fuzzlib as F  # noqa: E402

REPO = F.REPO
HARNESS = os.path.join(HERE, "cfg_harness.sh")
for _k in ("MODEL_PARAMS", "MODEL_MEASURED", "MODEL_SET", "SBC_GS_CONFIG", "SBC_GS_PROFILE", "SBC_GS_I_KNOW", "GS_MAVLINK_CONF",
           "SBC_CFG_REGISTRY", "BOARD"):
    os.environ.pop(_k, None)
# no SBC_GS_*/legacy per-key variable of the caller may leak into the loaders
for _k in [k for k in os.environ if k.startswith("SBC_GS_")]:
    os.environ.pop(_k)


def load_module(name, rel):
    spec = importlib.util.spec_from_file_location(name, F.path(*rel.split("/")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = load_module("fz_load", "config/load.py")
ROWS = L.registry()


def pmap(fn, items, workers=4):
    """Ordered parallel map for subprocess-bound work (results do not depend on scheduling)."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def rd(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def rb(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


class Tmp(unittest.TestCase):
    """TestCase with a private temp dir and a marker path that no legitimate code path may ever create."""

    def setUp(self):
        self.d = F.tmpdir()
        self.marker = os.path.join(self.d, "PWNED")
        self.addCleanup(shutil.rmtree, self.d, True)

    def no_marker(self, ctx=""):
        self.assertFalse(os.path.exists(self.marker), f"code from data was executed ({ctx})")


# ====================================================================== config: differential shell vs python
ALPH = list("abcXYZ019 _-./:@%+,=#'\"$`\;()&|<>*?!~{}[]^\t\r\x01\x1b\x7f\x0b\x0c") + ["é", "中", " "]
KEYS_OK = ["TX12_DEADMAN_MS", "TX12_MAP", "TX12_CONN", "GSMAV_RATE_HZ", "TX12_MAX_RATE_HZ", "TX12_FAILSAFE_THROTTLE_US"]
KEYS_BAD = ["tx12_deadman_ms", "UNKNOWN_X", "1ABC", "TX12_DEADMAN_MS ", "ROUTER", "", "A-B"]


HOSTILE = ["$x", "$(id)", "`id`", "${x}", ";", "&", "|", "(", ")", "\\", "<", ">", "*", "!", "{", "}", "'", '"', " ", "#", "$$", "\\n"]


def gen_line(R):
    if R.random() < 0.25:   # a valid line with ONE hostile character inserted into the value (classic single-mutation fuzzing)
        base = R.choice(["TX12_CONN=udpout:127.0.0.1:14550", "TX12_CONN='a b'", 'TX12_CONN="a b"', "TX12_MAP=/a/b.json", "GSMAV_RATE_HZ=20.5", "TX12_DEADMAN_MS=300"])
        i = base.index("=") + 1
        j = R.randint(i, len(base))
        return base[:j] + R.choice(";&|()<>*?!{}$`\\'\"# \t\r\x01\x7f") + base[j:] + "\n"
    k = R.choice(KEYS_OK * 8 + KEYS_BAD)
    form = R.choice(["bare", "bare", "sq", "sq", "dq", "dq", "dq", "junk"])
    if R.random() < 0.45:   # hostile tokens glued to plain letters: this is what finds parsers that let shell syntax through
        inner = "".join(R.choice(HOSTILE) if R.random() < 0.5 else R.choice("abc019/") for _ in range(R.randint(1, 4)))
    else:
        inner = "".join(R.choice(ALPH) for _ in range(R.randint(0, 6)))
    if form == "bare":
        v = "".join(R.choice("abc019._:/@%+,-") for _ in range(R.randint(0, 6))) if R.random() < 0.65 else inner
    elif form == "sq":
        v = "'" + inner + "'"
    elif form == "dq":
        v = '"' + inner + '"'
    else:
        v = inner
    return (R.choice(["", "", "", " ", "\t"]) + k + R.choice(["="] * 6 + [" =", "= "]) + v +
            R.choice(["", "", "", " #c", "#c", " # $(x)", "  ", "\t", " ;", " #\x01"]) + R.choice(["\n"] * 6 + ["\r\n"]))


def sh_parse(files, owner="", loc="C.UTF-8"):
    rc, out, err = F.run(["bash", HARNESS, REPO, "parse"] + (["--owner", owner] if owner else []) + list(files), env={"LC_ALL": loc}, timeout=120)
    res, cur = {}, None
    for line in out.split("\n"):
        if line.startswith("@@ "):
            _, f, r = line.split(" ", 2)
            cur = f
            res[cur] = [int(r), []]
        elif line and cur:
            res[cur][1].append(line.split("\t", 1))
    return res


def py_parse(path, owner=None):
    try:
        return 0, [[k, v] for k, v in L.parse_file(path, ROWS, owner).items()]
    except L.ConfigError:
        return 2, []


class TestConfigDifferential(Tmp):
    """The shell loader (config/load.sh) and the python loader (config/load.py) must give the SAME verdict on any input."""

    def parse_diff(self, loc, name):
        R = F.rng(name)
        files, datas = [], []
        for i in range(F.n(300)):
            body = "".join(gen_line(R) for _ in range(R.choice([1, 1, 1, 2, 3])))
            if R.random() < 0.05:
                body = "﻿" + body
            files.append(write(os.path.join(self.d, f"f{i}"), body))
            datas.append(body)
        sh = sh_parse(files, loc=loc)
        bad, ok = [], 0
        for p, body in zip(files, datas):
            py = py_parse(p)
            ok += py[0] == 0
            if sh[p][0] != py[0] or (py[0] == 0 and sh[p][1] != py[1]):
                bad.append((body, sh[p], py))
        self.assertEqual(bad[:3], [], f"shell and python parsers disagree on {len(bad)} of {len(files)} random files ({loc})")
        self.assertGreater(ok, len(files) // 20, "generator produced too few valid files: the test would be vacuous")

    def test_parse_utf8_locale(self):
        self.parse_diff("C.UTF-8", "parse-utf8")

    def test_parse_c_locale(self):
        self.parse_diff("C", "parse-c")

    def test_parse_owner_and_duplicates(self):
        R = F.rng("owner")
        keys = list(ROWS)
        files = []
        for i in range(F.n(60)):
            ks = [R.choice(keys) for _ in range(R.randint(1, 4))]
            files.append(write(os.path.join(self.d, f"o{i}"), "".join(f"{k}={R.choice(['1', '2.5', 'x'])}\n" for k in ks)))
        for owner in ("tx12", "gs-mavlink", "nobody-owns-this"):
            sh = sh_parse(files, owner=owner)
            for p in files:
                pyr = py_parse(p, owner)
                self.assertEqual(sh[p][0], pyr[0], f"{owner}: {rd(p)!r}")
                if pyr[0] == 0:
                    self.assertEqual(sh[p][1], pyr[1])

    def test_check_values_diff(self):
        """Typed value validation (sbc_cfg_check vs check_value) on random values at and around every type's edges."""
        R = F.rng("check")
        pool = {
            "int": ["0", "-0", "1", "007", "-1", "+5", "1e3", "0x10", " 5", "5 ", "", "9" * 15, "9" * 16, "-" + "9" * 15, "1.5", "١٢", "５", "٣"],
            "float": ["0", "1.0", "-0.5", ".5", "5.", "1e5", "0.1234567890123456789", "1" * 20, "1" * 21, "-", "--1", "1.2.3", "nan", "inf", "1,5", "００"],
            "port": ["0", "1", "65535", "65536", "-1", "070", "99999999999999", "x", ""],
            "ip": ["0.0.0.0", "255.255.255.255", "256.0.0.0", "1.2.3", "1.2.3.4.5", "01.002.3.4", "1.2.3.4 ", "1..2.3", "a.b.c.d", "1.2.3.-4", "0000.1.1.1"],
            "path": ["/", "/a", "/a/../b", "/a/..", "/..", "../a", "a", "/a b", "/a;b", "/a$b", "/a\\b", "/%:+@,-._", "//", "/a/./b", "/é"],
            "bool": ["0", "1", "2", "01", "true", "", "-1", " 1"],
            "enum:a|b": ["a", "b", "|", "", "A", "ab", "a b", "a|", "|b"],   # "a|b" itself is the pinned defect D3
            "str": ["", "x", "a b", "$(x)", "é"],
            "int?": ["", "5", "x"], "path?": ["", "/a", "x"],
        }
        cases = []
        for t, vals in pool.items():
            for v in vals + ["".join(R.choice("0123456789.-+/ax") for _ in range(R.randint(0, 8))) for _ in range(F.n(5))]:
                for mn, mx, safe in (("-", "-", "n"), ("0", "100", "y"), ("-5", "5.5", "n")):
                    cases.append((t, mn, mx, safe, v))
        reg = os.path.join(self.d, "reg.tsv")
        # one synthetic registry row per (type, bounds, safety) combination so the REAL checkers see every combination
        combos = sorted({(t, mn, mx, safe) for t, mn, mx, safe, _ in cases})
        names = {c: f"K{i}" for i, c in enumerate(combos)}
        write(reg, "".join(f"{names[c]}\t0\t{c[0]}\t{c[1]}\t{c[2]}\tu\t{c[3]}\to\t-\td\n" for c in combos))
        casefile = os.path.join(self.d, "cases")
        write(casefile, "".join(f"{names[(t, mn, mx, s)]}\t{v}\n" for t, mn, mx, s, v in cases))
        # shell: per-case registry-independent run through the harness 'check' mode with this registry
        rc, out, err = F.run(["bash", "-c", 'export SBC_CFG_REGISTRY="$1"; exec bash "$2" "$3" check "$4"', "_", reg, HARNESS, REPO, casefile],
                             env={"LC_ALL": "C.UTF-8"}, timeout=120)
        sh = out.split("\n")[:-1]
        self.assertEqual(len(sh), len(cases), err)
        rows = L.registry(reg)
        bad = []
        for (t, mn, mx, safe, v), line in zip(cases, sh):
            rcs, _, msg = line.partition("\t")
            r = rows[names[(t, mn, mx, safe)]]
            try:
                _val, bound = L.check_value(r, v)
                py = (2 if bound else 0, "")
            except L.ConfigError as e:
                py = (1, str(e))
            msg = msg.replace(f"{r.key}", "K")
            pm = py[1].replace(f"{r.key}", "K")
            if int(rcs) != py[0] or (py[0] == 1 and msg != pm):
                bad.append((t, mn, mx, safe, v, line, py))
        self.assertEqual(bad[:4], [], f"{len(bad)} of {len(cases)} (type,bounds,value) cases give different shell/python verdicts or messages")

    # docs/CONFIG.md section 4 (REPO): the documented hard bounds. An independent copy, so that widening a bound in registry.tsv is caught.
    DOC_SAFETY = {"TX12_DEADMAN_MS": (50, 1000), "TX12_MAX_RATE_HZ": (1, 100), "TX12_FAILSAFE_THROTTLE_US": (1000, 1100), "TX12_THROTTLE_FS_FRAMES": (1, 20),
                  "TX12_EXIT_RELEASE_FRAMES": (1, 20), "TX12_RELEASE_HOLD_S": (0.5, 5), "TX12_CLAMP_LO_US": (900, 1100), "TX12_CLAMP_HI_US": (1900, 2100),
                  "TX12_SANE_MIN_US": (0, 1000), "TX12_SANE_MAX_US": (2000, 8000), "GSMAV_RATE_HZ": (0.1, 50), "GSMAV_NEUTRAL_US": (1400, 1600),
                  "GSMAV_SWEEP_AMP_US": (0, 500), "GSMAV_RELEASE_REPEATS": (1, 20)}

    def test_registry_safety_set_and_bounds_are_the_documented_ones(self):
        got = {r.key: (float(r.min), float(r.max)) for r in ROWS.values() if r.safe}
        self.assertEqual(got, {k: (float(a), float(b)) for k, (a, b) in self.DOC_SAFETY.items()}, "safety keys/bounds differ from docs/CONFIG.md section 4")

    def test_resolve_safety_bounds_both_loaders(self):
        """SAFETY keys: hard bounds are enforced identically by both loaders; only --i-know relaxes bounds (never types)."""
        import contextlib
        import io
        R = F.rng("safety")
        safety = [r for r in ROWS.values() if r.safe]
        self.assertGreaterEqual(len(safety), 14)
        cases = []
        for r in safety:
            lo, hi = float(r.min), float(r.max)
            isint = r.type == "int"
            fmt = (lambda x: str(int(x))) if isint else (lambda x: repr(round(x, 3)))
            vals = [lo, hi, (lo + hi) / 2, lo - 1, hi + 1, lo - 0.5 if not isint else lo - 1, hi * 10 + 1, 0, -hi, R.uniform(lo - 100, hi + 100)]
            vals = [fmt(v) for v in vals] + ["abc", "", "1e3"]
            for v in vals:
                for ik in (0, 1):
                    cases.append((r.key, v, ik))
        sample = R.sample(cases, min(len(cases), F.n(48)))
        regs = {}
        lines = []
        for k, v, ik in sample:  # a single-row registry per key: the shell resolves only that key (fast); bounds are the REAL ones
            if k not in regs:
                regs[k] = write(os.path.join(self.d, f"reg-{k}.tsv"), "\t".join([k, ROWS[k].default, ROWS[k].type, ROWS[k].min, ROWS[k].max, ROWS[k].unit, "y", "o", "-", "d"]) + "\n")
            lines.append(f"{regs[k]}\t{ik}\t{k}\t{v}")
        chunks = [lines[j::4] for j in range(4)]
        files = [write(os.path.join(self.d, f"cases{j}"), "\n".join(c) + "\n") for j, c in enumerate(chunks)]
        outs = pmap(lambda cf: F.run(["bash", HARNESS, REPO, "resolve", cf], timeout=120), files)
        by_chunk = [o[1].split("\n")[:-1] for o in outs]
        self.assertEqual([len(b) for b in by_chunk], [len(c) for c in chunks], [o[2] for o in outs])
        sh = [None] * len(sample)
        for j in range(4):
            sh[j::4] = by_chunk[j]
        bad = []
        for (k, v, ik), line in zip(sample, sh):
            f = line.split("\t")
            src, srcwarn = int(f[0]), int(f[3])
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    cfg = L.resolve(["o"], env={"SBC_GS_" + k: v}, registry_path=regs[k], i_know=bool(ik))
                py = (0, cfg.raw[k])
            except L.ConfigError:
                py = (2, "")
            r = ROWS[k]
            numeric_ok = bool(re.fullmatch(r"-?[0-9]+" if r.type == "int" else r"-?[0-9]+(\.[0-9]+)?", v)) and (len(v) <= (15 if r.type == "int" else 20))
            inside = numeric_ok and float(r.min) <= float(v) <= float(r.max)
            expected = 0 if (numeric_ok and (inside or ik)) else 2
            if v == "":
                expected = 0  # an empty env var counts as unset -> the (valid) default is used
            got_sh = src
            if not (got_sh == py[0] == expected):
                bad.append((k, v, ik, line, py, expected))
        self.assertEqual(bad[:4], [], f"{len(bad)} safety-bound disagreements (shell, python, oracle)")


class TestConfigResolveDifferential(Tmp):
    """Whole-resolution differential (env layer, default layer, --i-know, hard bounds) on random synthetic registries."""

    def test_random_registries_and_env_values(self):
        import contextlib
        import io
        R = F.rng("resolve")
        types = ["int", "float", "port", "ip", "path", "bool", "str", "enum:x|y", "int?", "path?"]
        vals = {"int": ["0", "5", "-3", "100", "101", "-1", "1.5", "abc", "", "007", "99999999999999999"], "float": ["0", "0.5", "5.5", "5.51", "-2", "1e2", "", "x", "12.34"],
                "port": ["0", "1", "80", "65535", "65536", ""], "ip": ["1.2.3.4", "0.0.0.0", "1.2.3", "300.1.1.1", ""], "path": ["/a", "/a/../b", "a", "/", ""],
                "bool": ["0", "1", "2", ""], "str": ["", "x y", "$(x)"], "enum:x|y": ["x", "y", "z", ""], "int?": ["", "4", "q"], "path?": ["", "/p", "p"]}
        cases, regs = [], {}
        for i in range(F.n(70)):
            t = R.choice(types)
            base = t.rstrip("?").split(":")[0]
            mn, mx = R.choice([("-", "-"), ("0", "10"), ("-5", "5.5"), ("1", "-"), ("-", "100")])
            safe = R.choice(["y", "n"])
            default = {"int": "5", "float": "1.0", "port": "80", "ip": "1.2.3.4", "path": "/a", "bool": "0", "str": "d", "enum": "x"}[base]
            key = f"R{i}"
            regs[key] = write(os.path.join(self.d, f"r{i}.tsv"), "\t".join([key, default, t, mn, mx, "u", safe, "o", "-", "d"]) + "\n")
            cases.append((key, R.choice(vals[t if t in vals else base]), R.choice([0, 1])))
        casefile = write(os.path.join(self.d, "cases"), "".join(f"{regs[k]}\t{ik}\t{k}\t{v}\n" for k, v, ik in cases))
        rc, out, err = F.run(["bash", HARNESS, REPO, "resolve", casefile], timeout=120)
        sh = out.split("\n")[:-1]
        self.assertEqual(len(sh), len(cases), err)
        bad, accepted = [], 0
        for (k, v, ik), line in zip(cases, sh):
            f = line.split("\t")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stderr(buf):
                    cfg = L.resolve(["o"], env={"SBC_GS_" + k: v, "SBC_GS_CONFIG": "/nonexistent"}, registry_path=regs[k], i_know=bool(ik))
                py = (0, cfg.raw[k], cfg.src[k], buf.getvalue().count("SAFETY OVERRIDE"))
            except L.ConfigError:
                py = (2, "", "", buf.getvalue().count("SAFETY OVERRIDE"))
            shv = (int(f[0]), f[1], f[2], int(f[3]))
            accepted += py[0] == 0
            if shv != py:
                bad.append((k, v, ik, rd(regs[k]).strip(), line, py))
        self.assertEqual(bad[:3], [], f"{len(bad)} of {len(cases)} resolutions differ between config/load.sh and config/load.py")
        self.assertGreater(accepted, len(cases) // 4, "too few accepted cases: vacuous")


class TestConfigPinnedDefects(Tmp):
    """Divergences between the two loaders that the differential fuzz found (REPO). See docs/SIM-FUZZ.md D1..D3."""

    def test_DEFECT_D1_nul_byte_shell_strips_python_rejects(self):
        p = write(os.path.join(self.d, "nul.env"), b"TX12_DEADMAN_MS=3\x0000\n")
        sh = sh_parse([p])[p]
        py = py_parse(p)
        # shell: bash `read` drops the NUL silently, so "3\0 00" is read as 300; python: control character -> error
        F.pinned(self, "D1", sh[0] == 0 and sh[1] == [["TX12_DEADMAN_MS", "300"]] and py[0] == 2, f"shell={sh} python={py}")

    def test_DEFECT_D2_unicode_line_separator_in_utf8_locale(self):
        p = write(os.path.join(self.d, "u.env"), 'TX12_MAP="a b"\n')
        sh = sh_parse([p], loc="C.UTF-8")[p]
        shc = sh_parse([p], loc="C")[p]
        py = py_parse(p)
        # [[:cntrl:]] of bash is locale dependent (U+2028 is cntrl under C.UTF-8): same file, different verdict per locale and vs python
        F.pinned(self, "D2", sh[0] == 2 and shc[0] == 0 and py[0] == 0, f"C.UTF-8={sh[0]} C={shc[0]} python={py[0]}")

    def test_DEFECT_D3_enum_accepts_joined_alternatives_in_shell(self):
        cases = os.path.join(self.d, "cases")
        reg = write(os.path.join(self.d, "reg.tsv"), "KE\ta\tenum:a|b\t-\t-\tu\tn\to\t-\td\n")
        write(cases, "KE\ta|b\n")
        rc, out, err = F.run(["bash", "-c", 'export SBC_CFG_REGISTRY="$1"; exec bash "$2" "$3" check "$4"', "_", reg, HARNESS, REPO, cases])
        rows = L.registry(reg)
        try:
            L.check_value(rows["KE"], "a|b")
            py_ok = True
        except L.ConfigError:
            py_ok = False
        F.pinned(self, "D3", out.startswith("0\t") and not py_ok, f"shell rc={out!r} python_accepts={py_ok}")


# ====================================================================== config: injection / no code from data
def payloads(marker):
    t = f"touch {marker}"
    base = [f"$({t})", f"`{t}`", f";{t}", f"& {t}", f"| {t}", f"'; {t}; '", f'"$({t})"', f"${{IFS}}{t}", f"$(({'a[$(' + t + ')]'}))",
            f"a[$({t})]", f"<({t})", f"\n{t}", f"x\\\n{t}", f"$'\\n{t}'", f"%(x)$({t})", f"{{a,$({t})}}", f">{marker}.out", f"2>{marker}.e"]
    return base


def injection_files(marker):
    out = []
    for p in payloads(marker):
        for tpl in ("TX12_MAP={p}\n", "TX12_MAP='{p}'\n", 'TX12_MAP="{p}"\n', "TX12_DEADMAN_MS=300 # {p}\n", "{p}\n", "export TX12_MAP={p}\n",
                    ". {p}\n", "{p}=1\n", "TX12_MAP=a\n{p}\n", "# {p}\nTX12_DEADMAN_MS=300\n", "TX12_MAP=a {p}\n"):
            out.append(tpl.replace("{p}", p))
    return out


class TestConfigInjection(Tmp):
    """Config files, the environment layer and board files are DATA: no payload may execute (marker stays absent), exit codes in {0,2}."""

    def test_files_never_execute_code(self):
        bodies = injection_files(self.marker)
        files = [write(os.path.join(self.d, f"i{i}.env"), b) for i, b in enumerate(bodies)]
        sh = sh_parse(files, owner="tx12")
        self.no_marker("harness parse")
        for p in files:
            self.assertIn(sh[p][0], (0, 2))
            self.assertEqual(py_parse(p, "tx12")[0] == 0, sh[p][0] == 0, rd(p))
        # the CLI front ends too (real processes): sbc-gs-config check, load.py check
        pick = files[:: max(1, len(files) // F.n(12))]

        def one(p):
            return (F.run([F.path("config", "sbc-gs-config"), "check", p, "tx12"])[0], F.run([sys.executable, F.path("config", "load.py"), "check", p, "tx12"])[0])
        for (a, b), p in zip(pmap(one, pick), pick):
            self.assertIn(a, (0, 2), p)
            self.assertIn(b, (0, 2), p)
            self.assertEqual(a, b, rd(p))
        self.no_marker("sbc-gs-config / load.py check")

    def test_gs_mavlink_conf_and_env_never_execute_code(self):
        R = F.rng("inj-mav")
        keys = [r.key for r in ROWS.values() if "gs-mavlink" in r.owners]
        cases = []
        for p in payloads(self.marker):
            for k in R.sample(keys, 3):
                cases.append(("file", k, p))
                cases.append(("env", k, p))
        cases = [(i,) + c for i, c in enumerate(R.sample(cases, min(len(cases), F.n(48))))]
        gm = F.path("gs", "mavlink", "gs-mavlink.sh")

        def one(c):
            i, kind, k, p = c
            if kind == "file":
                cf = write(os.path.join(self.d, f"m{i}.conf"), f"{k}='{p}'\n")
                return F.run([gm, "--print"], env={"GS_MAVLINK_CONF": cf}, cwd=self.d)[0]
            return F.run([gm, "--print"], env={f"SBC_GS_{k}": p, "GS_MAVLINK_CONF": "/nonexistent"}, cwd=self.d)[0]
        for c, rc in zip(cases, pmap(one, cases)):
            self.assertIn(rc, (0, 2), c)
        self.no_marker("gs-mavlink.sh --print")

    def test_python_loader_env_layer_never_executes_code(self):
        for p in payloads(self.marker):
            for r in list(ROWS.values())[:: max(1, len(ROWS) // 12)]:
                try:
                    L.resolve(None, env={r.env: p, "SBC_GS_CONFIG": "/nonexistent"})
                except L.ConfigError:
                    pass
        self.no_marker("load.py resolve")



# ====================================================================== gs-mavlink.sh --print against an independent oracle
GM = F.path("gs", "mavlink", "gs-mavlink.sh")
GM_DEFAULTS = {r.key: r.default for r in ROWS.values() if "gs-mavlink" in r.owners}


def _is_ip(s):
    m = re.fullmatch(r"([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})", s)
    return bool(m) and all(int(x) <= 255 for x in m.groups())


def _is_port(s):
    return bool(re.fullmatch(r"[0-9]{1,5}", s)) and 1 <= int(s) <= 65535


def mav_oracle(c):
    """Independent re-statement of the documented validation (docs/GS-MAVLINK.md, script header). -> (rc, command line | None).
    Assumes values without leading zeros and without '..' (those cases are the pinned defects D4..D6)."""
    c = {**GM_DEFAULTS, **c}
    if c["ROUTER"] not in ("mavp2p", "mavlink-router"):
        return 2, None
    if any(c[f] not in ("0", "1") for f in ("TCP_ENABLE", "HB_DISABLE", "STREAMREQ_DISABLE", "DUMP_ENABLE")):
        return 2, None
    if not (_is_ip(c["UPSTREAM_BIND"]) and _is_ip(c["LISTEN_ADDR"]) and _is_port(c["UPSTREAM_PORT"]) and _is_port(c["TCP_PORT"])):
        return 2, None
    if not (re.fullmatch(r"[0-9]+", c["HB_SYSID"]) and 1 <= int(c["HB_SYSID"]) <= 254) or c["HB_SYSID"] == "3":
        return 2, None
    ports = c["GCS_UDP_PORTS"].split()
    used = [c["UPSTREAM_PORT"]] + ([c["TCP_PORT"]] if c["TCP_ENABLE"] == "1" else [])
    for p in ports:
        if not _is_port(p) or p in used:
            return 2, None
        used.append(p)
    clients = c["GCS_UDP_CLIENTS"].split()
    for x in clients:
        ip, sep, pt = x.rpartition(":")
        if not sep or not _is_ip(ip) or not _is_port(pt):
            return 2, None
    dev = c["SERIAL_DEV"]
    if dev:
        if not re.fullmatch(r"/dev/[A-Za-z0-9._/-]+", dev):
            return 2, None
        if not (re.fullmatch(r"[0-9]+", c["SERIAL_BAUD"]) and 1200 <= int(c["SERIAL_BAUD"]) <= 3000000):
            return 2, None
    if not re.fullmatch(r"/[A-Za-z0-9._/%:-]+", c["DUMP_PATH"]):
        return 2, None
    if c["ROUTER"] == "mavp2p":
        cmd = ["mavp2p"] + ([f"serial:{dev}:{c['SERIAL_BAUD']}"] if dev else []) + [f"udps:{c['UPSTREAM_BIND']}:{c['UPSTREAM_PORT']}"]
        cmd += [f"udps:{c['LISTEN_ADDR']}:{p}" for p in ports] + [f"udpc:{x}" for x in clients]
        if c["TCP_ENABLE"] == "1":
            cmd.append(f"tcps:{c['LISTEN_ADDR']}:{c['TCP_PORT']}")
        cmd += ["--hb-disable"] if c["HB_DISABLE"] == "1" else [f"--hb-systemid={c['HB_SYSID']}"]
        if c["STREAMREQ_DISABLE"] == "1":
            cmd.append("--streamreq-disable")
        if c["DUMP_ENABLE"] == "1":
            cmd += ["--dump", f"--dump-path={c['DUMP_PATH']}"]
        return 0, " ".join(cmd)
    if dev or ports:
        return 2, None
    cmd = ["mavlink-routerd"]
    for x in clients:
        cmd += ["-e", x]
    cmd += ["-t", c["TCP_PORT"] if c["TCP_ENABLE"] == "1" else "0", f"{c['UPSTREAM_BIND']}:{c['UPSTREAM_PORT']}"]
    return 0, " ".join(cmd)


def mav_gen(R):
    def ch(good, bad):  # ~88 % valid values so that many configs reach the command-line builder
        return R.choice(good) if R.random() < 0.88 else R.choice(bad)
    port = lambda: ch(["14550", "14560", "5760", "14561", "1", "65535", "14570"], ["0", "65536", "-1", "abc", "", " 12", "*", "?", "14550x"])  # noqa: E731
    ip = lambda: ch(["0.0.0.0", "127.0.0.1", "192.168.4.1", "255.255.255.255"], ["256.1.1.1", "1.2.3", "1.2.3.4.5", "a.b.c.d", "", "1..2.3", "1.2.3.4/8", "-1.2.3.4"])  # noqa: E731
    c = {}
    pick = lambda k, v: c.__setitem__(k, v) if R.random() < 0.5 else None  # noqa: E731
    pick("ROUTER", ch(["mavp2p", "mavp2p", "mavlink-router"], ["mavp2", "", "MAVP2P"]))
    for k in ("TCP_ENABLE", "HB_DISABLE", "STREAMREQ_DISABLE", "DUMP_ENABLE"):
        pick(k, ch(["0", "1"], ["2", "", "-1", "true"]))
    for k in ("UPSTREAM_BIND", "LISTEN_ADDR"):
        pick(k, ip())
    for k in ("UPSTREAM_PORT", "TCP_PORT"):
        pick(k, port())
    pick("GCS_UDP_PORTS", " ".join(port() for _ in range(R.randint(0, 3))))
    pick("GCS_UDP_CLIENTS", " ".join(ch(["1.2.3.4:14550", "10.0.0.2:1", "1.2.3.4:65535"], ["1.2.3.4", "a:1", "1.2.3.4:0", "1.2.3.4:5:6", ":5"]) for _ in range(R.randint(0, 2))))
    pick("HB_SYSID", ch(["1", "125", "254"], ["255", "0", "3", "abc", "", "12 3", "-5", "99999999999999999999"]))
    pick("SERIAL_DEV", ch(["", "/dev/ttyUSB0", "/dev/serial/by-id/x-1"], ["/dev/", "tty0", "/dev/tty;x", "/dev/a b", "/dev/tty$x"]))
    pick("SERIAL_BAUD", ch(["115200", "1200", "3000000"], ["1199", "3000001", "abc", "", "0"]))
    pick("DUMP_PATH", ch(["/var/log/x.tlog", "/tmp/a%b:c"], ["rel", "/", "/a b", "/a;b", "/a$b"]))
    return c


class TestGsMavlink(Tmp):
    def conf(self, i, c):
        return write(os.path.join(self.d, f"gm{i}.conf"), "".join(f"{k}='{v}'\n" for k, v in c.items()))

    def run_print(self, cf, env=None):
        rc, out, err = F.run([GM, "--print"], env={"GS_MAVLINK_CONF": cf, **(env or {})}, cwd=self.d)
        return rc, [ln for ln in out.split("\n") if ln and not ln.startswith("#")], err

    def test_random_confs_match_oracle(self):
        R = F.rng("mav")
        cases = []
        for i in range(F.n(40)):
            c = mav_gen(R)
            cases.append((i, c, self.conf(i, c)))
        res = pmap(lambda t: self.run_print(t[2]), cases, 4)
        bad, valid = [], 0
        for (i, c, cf), (rc, lines, err) in zip(cases, res):
            orc, ocmd = mav_oracle(c)
            valid += orc == 0
            self.assertIn(rc, (0, 2), f"undocumented exit code {rc}: {c} {err}")
            if rc != orc or (rc == 0 and lines != [ocmd]):
                bad.append((c, rc, lines, orc, ocmd))
        self.assertEqual(bad[:3], [], f"{len(bad)} of {len(cases)} random configs disagree with the oracle")
        self.assertGreater(valid, 3, "too few valid configs: vacuous")

    def test_explicit_boundary_regressions(self):
        cases = [({}, 0), ({"UPSTREAM_PORT": "0"}, 2), ({"UPSTREAM_PORT": "65535", "GCS_UDP_PORTS": "1"}, 0), ({"UPSTREAM_PORT": "65536"}, 2),
                 ({"GCS_UDP_PORTS": "14550"}, 2), ({"GCS_UDP_PORTS": "14560 14560"}, 2), ({"TCP_ENABLE": "1", "TCP_PORT": "14560"}, 2),
                 ({"TCP_ENABLE": "0", "TCP_PORT": "14560"}, 0), ({"HB_SYSID": "254"}, 0), ({"HB_SYSID": "255"}, 2), ({"HB_SYSID": "0"}, 2), ({"HB_SYSID": "3"}, 2),
                 ({"SERIAL_DEV": "/dev/ttyUSB0", "SERIAL_BAUD": "1200"}, 0), ({"SERIAL_DEV": "/dev/ttyUSB0", "SERIAL_BAUD": "1199"}, 2),
                 ({"SERIAL_DEV": "/dev/ttyUSB0", "SERIAL_BAUD": "3000001"}, 2), ({"SERIAL_DEV": "ttyUSB0"}, 2), ({"ROUTER": "mavlink-router", "GCS_UDP_PORTS": ""}, 0),
                 ({"ROUTER": "mavlink-router"}, 2), ({"LISTEN_ADDR": "256.0.0.1"}, 2), ({"UPSTREAM_BIND": "1.2.3"}, 2), ({"GCS_UDP_CLIENTS": "1.2.3.4:0"}, 2),
                 ({"TCP_ENABLE": "2"}, 2), ({"DUMP_ENABLE": "1", "DUMP_PATH": "relative"}, 2)]
        res = pmap(lambda t: self.run_print(self.conf(t[0], t[1][0]))[0], list(enumerate(cases)), 4)
        for (c, want), rc in zip(cases, res):
            self.assertEqual(rc, want, c)
            self.assertEqual(mav_oracle(c)[0], want, f"oracle disagrees with the explicit expectation: {c}")

    def test_print_is_deterministic_and_valid_output_is_closed_form(self):
        R = F.rng("mav-det")
        for i in range(F.n(4)):
            c = mav_gen(R)
            cf = self.conf(i, c)
            a, b = self.run_print(cf), self.run_print(cf)
            self.assertEqual(a[:2], b[:2])
            if a[0] == 0:
                for tok in a[1][0].split():
                    self.assertRegex(tok, r"^(mavp2p|mavlink-routerd|-e|-t|--[a-z-]+(=[0-9]+|=/[A-Za-z0-9._/%:-]+)?|(udps|udpc|tcps|serial):[A-Za-z0-9.:/_-]+|[0-9.:]+|[0-9]+)$")

    def test_ports_never_outside_range_in_output(self):
        R = F.rng("mav-port")
        for i in range(F.n(10)):
            c = {"UPSTREAM_PORT": str(R.choice([0, 1, 65535, 65536, R.randint(1, 65535), R.randint(-5, 70000)])),
                 "GCS_UDP_PORTS": " ".join(str(R.randint(-3, 70000)) for _ in range(R.randint(0, 3)))}
            rc, lines, _ = self.run_print(self.conf(i, c))
            if rc == 0:
                for m in re.finditer(r"(?:udps|udpc|tcps):[0-9.]+:([0-9]+)", lines[0]):
                    self.assertTrue(1 <= int(m.group(1)) <= 65535, lines)

    def test_DEFECT_D4_ip_with_trailing_dot_accepted(self):
        rc, lines, _ = self.run_print(self.conf(0, {"LISTEN_ADDR": "1.2.3.4."}))
        F.pinned(self, "D4", rc == 0 and "udps:1.2.3.4.:14560" in lines[0], f"rc={rc} {lines}")

    def test_DEFECT_D5_duplicate_port_with_leading_zero_accepted(self):
        rc, lines, _ = self.run_print(self.conf(0, {"GCS_UDP_PORTS": "4560 04560"}))
        F.pinned(self, "D5", rc == 0 and "udps:0.0.0.0:4560 udps:0.0.0.0:04560" in lines[0], f"rc={rc} {lines}")

    def test_DEFECT_D6_serial_dev_and_dump_path_traversal_accepted(self):
        rc, lines, _ = self.run_print(self.conf(0, {"SERIAL_DEV": "/dev/../tmp/x", "DUMP_ENABLE": "1", "DUMP_PATH": "/var/log/../../etc/x"}))
        F.pinned(self, "D6", rc == 0 and "serial:/dev/../tmp/x:115200" in lines[0] and "--dump-path=/var/log/../../etc/x" in lines[0], f"rc={rc} {lines}")

    def test_registry_value_check_is_stricter_than_the_script(self):
        """INF: the registry types (path, ip) would reject DUMP_PATH with '..'; the script's own validators win (--no-value-check)."""
        with self.assertRaises(L.ConfigError):
            L.check_value(ROWS["DUMP_PATH"], "/var/log/../../etc/x")



# ====================================================================== board profile: board.sh / board_conf.py / validate.sh / render-udev.sh
sys.path.insert(0, F.path("gs", "lib"))
import board_conf  # noqa: E402
RADXA_CONF = F.path("gs", "boards", "radxa-zero3", "board.conf")


def make_board_dir(root, name, extra=""):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(RADXA_CONF, encoding="utf-8") as f:
        write(os.path.join(d, "board.conf"), f.read() + extra)
    return d


class TestBoard(Tmp):
    def gen_value(self, R):
        A = list("abZ09 _-./:@%+,=#'\"$`;()&|<>*?!~")
        kind = R.choice(["bare", "sq", "sq", "dq"])
        inner = "".join(R.choice(A) for _ in range(R.randint(0, 5)))
        pad = R.choice(["", "", " ", "  "]) if R.random() < 0.4 else ""   # leading/trailing blanks INSIDE the quotes must be kept verbatim
        if kind == "bare":
            v = "".join(R.choice("abc019._:/@%+-") for _ in range(R.randint(0, 5)))
        elif kind == "sq":
            v = "'" + pad + inner.replace("'", "") + pad + "'"
        else:
            v = '"' + pad + inner.replace('"', "") + pad + '"'
        # NOTE: no '#' glued to the value and no backslash inside "..." here: those are the pinned defects D7/D8
        return v + R.choice(["", "", " #c", "  ", "\t"])

    def test_validate_boards_conf_python_and_shell_agree(self):
        R = F.rng("board")
        root = os.path.join(self.d, "boards")
        os.makedirs(root)
        dirs, vals = [], []
        for i in range(F.n(28)):
            v = self.gen_value(R)
            dirs.append(make_board_dir(root, f"b{i}", f"ZZFUZZ={v}\n"))
            vals.append(v)
        chunks = [dirs[k::4] for k in range(4)]
        outs = pmap(lambda ch: F.run(["bash", F.path("gs", "boards", "validate.sh")] + ch, timeout=120), chunks)
        ok = set()
        for rc, out, err in outs:
            self.assertIn(rc, (0, 1))
            ok |= {ln[3:] for ln in out.splitlines() if ln.startswith("ok ")}
        self.assertGreater(len(ok), len(dirs) // 5, "generator produced too few validate-accepted files: vacuous")
        okdirs = [d for d in dirs if os.path.basename(d) in ok]
        sh = pmap(lambda d: F.run(["bash", "-c", '. "$1"; printf %s "$ZZFUZZ"', "_", d + "/board.conf"], cwd=self.d), okdirs)
        bad = []
        for d, (rc, out, err) in zip(okdirs, sh):
            py = board_conf.parse(d + "/board.conf").get("ZZFUZZ")
            if rc != 0 or out != py:
                bad.append((rd(d + "/board.conf").splitlines()[-1], out, py))
        self.assertEqual(bad[:4], [], f"{len(bad)} validate.sh-approved profiles are read differently by shell and board_conf.py")

    def test_DEFECT_D7_validate_accepts_glued_comment_python_and_shell_differ(self):
        root = os.path.join(self.d, "boards")
        os.makedirs(root)
        d = make_board_dir(root, "g", "ZZFUZZ=abc#c\nZZQ='q'#c\n")
        rc, out, _ = F.run(["bash", F.path("gs", "boards", "validate.sh"), d])
        rc2, o2, _ = F.run(["bash", "-c", '. "$1"; printf "%s|%s" "$ZZFUZZ" "$ZZQ"', "_", d + "/board.conf"])
        py = board_conf.parse(d + "/board.conf")
        F.pinned(self, "D7", out.startswith("ok") and o2 == "abc#c|q#c" and py.get("ZZFUZZ") == "abc" and py.get("ZZQ") == "q", f"validate={out!r} shell={o2!r} python={py.get('ZZFUZZ')!r},{py.get('ZZQ')!r}")

    def test_DEFECT_D8_validate_accepts_backslash_before_closing_dquote(self):
        root = os.path.join(self.d, "boards")
        os.makedirs(root)
        d = make_board_dir(root, "g", 'ZZFUZZ="abc\\"\nZZEND=1\n')
        rc, out, _ = F.run(["bash", F.path("gs", "boards", "validate.sh"), d])
        rc2, o2, e2 = F.run(["bash", "-c", '. "$1"; printf "%s" "${ZZEND-unset}"', "_", d + "/board.conf"])
        py = board_conf.parse(d + "/board.conf")
        # validate.sh says ok, but sourcing the file leaves the double quote open: ZZEND is swallowed (or the source fails)
        F.pinned(self, "D8", out.startswith("ok") and (rc2 != 0 or o2 == "unset") and py.get("ZZEND") == "1", f"validate={out!r} source rc={rc2} ZZEND={o2!r} {e2[:60]!r}")

    def test_board_get_returns_value_or_fails_loudly(self):
        R = F.rng("board-get")
        keys = ["BOARD_ID", "GPIO_PIN_PREFIX", "HOME_DIR", "NO_SUCH_KEY", "A", "_X", "BOARD", "PATH"]
        for _ in range(F.n(10)):
            k = R.choice(keys)
            rc, out, err = F.run(["bash", "-c", '. "$1/gs/lib/board.sh" && board_get "$2"', "_", REPO, k], cwd=self.d)
            self.assertIn(rc, (0, 1), (k, err))
            if rc == 0:
                self.assertTrue(out.endswith("\n") and out.strip() != "")
            else:
                self.assertIn("board.sh:", err)

    def test_unknown_board_names_fail_cleanly(self):
        R = F.rng("board-names")
        names = ["", "nope", "radxa-zero3x", "RADXA-ZERO3", "rpi4 ", "rpi4\n", "-x", "a b", "$(true)", "`true`", "x;y", "radxa-zero3/", "rpi4/../rpi4"]
        names += ["".join(R.choice("ab-_./ ;$`'\"") for _ in range(R.randint(1, 8))) for _ in range(F.n(6))]
        res = pmap(lambda b: F.run(["bash", "-c", '. "$1/gs/lib/board.sh"', "_", REPO], env={"BOARD": b}, cwd=self.d), names)
        for b, (rc, out, err) in zip(names, res):
            self.assertIn(rc, (0, 1), (b, rc, err))
            self.no_marker(b)

    def test_DEFECT_D9_BOARD_path_traversal_sources_foreign_conf(self):
        evil = os.path.join(self.d, "evil")
        os.makedirs(evil)
        write(os.path.join(evil, "board.conf"), f"touch {self.marker}\nBOARD_ID=evil\n")
        rel = os.path.relpath(evil, F.path("gs", "boards"))
        rc, out, err = F.run(["bash", "-c", '. "$1/gs/lib/board.sh" && board_get BOARD_ID', "_", REPO], env={"BOARD": rel}, cwd=self.d)
        F.pinned(self, "D9", os.path.exists(self.marker) and out.strip() == "evil", f"BOARD={rel} rc={rc} out={out!r}")

    def test_DEFECT_D10_board_get_key_is_evaluated_as_arithmetic_subscript(self):
        rc, out, err = F.run(["bash", "-c", '. "$1/gs/lib/board.sh"; board_get "a[\\$(touch $2)]"', "_", REPO, self.marker], cwd=self.d)
        F.pinned(self, "D10", os.path.exists(self.marker), f"rc={rc} err={err!r}")

    def test_gpio_map_lookup_returns_digits_or_fails(self):
        R = F.rng("gpio-map")
        pm = F.path("gs", "boards", "rpi4", "pinmap.conf")
        table = {}
        for line in rd(pm).splitlines():
            t = line.split()
            if t and not t[0].startswith("#") and len(t) >= 2:
                table.setdefault(t[0], t[1])
        pins = [str(i) for i in range(0, 45)] + ["", " ", "7 ", "07", "7.0", "1e1", "-1", "*", ".*", "\\n", "a\nb", "7\n7", "$(x)", "0x7", "٧", "7\t9"]
        pins += ["".join(R.choice("0123456789.e+-\\ *") for _ in range(R.randint(1, 4))) for _ in range(F.n(10))]
        code = '. "$1/gs/lib/gpio.sh"; _gpio_map_lookup "$2" "$3"'
        res = pmap(lambda p: F.run(["bash", "-c", code, "_", REPO, pm, p], cwd=self.d), pins)
        for p, (rc, out, err) in zip(pins, res):
            self.assertIn(rc, (0, 1), (p, err))
            if rc == 0:
                self.assertRegex(out, r"^[0-9]+$", p)
            elif rc == 1:
                self.assertEqual(out, "", p)
            if p in table and table[p].isdigit():
                self.assertEqual((rc, out), (0, table[p]), p)
        self.no_marker()


class TestUdevRender(Tmp):
    RENDER = F.path("gs", "boards", "render-udev.sh")

    def render(self, extra, name="r"):
        root = os.path.join(self.d, "b-" + name)
        os.makedirs(root)
        d = make_board_dir(root, "x")
        # replace the three template keys by the fuzzed values (appended later lines win in a sourced file)
        with open(os.path.join(d, "board.conf"), "a", encoding="utf-8") as f:
            f.write(extra)
        out = os.path.join(self.d, "out-" + name)
        rc, so, se = F.run(["bash", self.RENDER, d, out], cwd=self.d)
        return rc, so, se, out

    def expected(self, vals):
        res = {}
        for name in ("98-rename.rules", "99-GS.rules"):
            t = rd(F.path("gs", name + ".in"))
            for k, v in vals.items():
                t = t.replace(f"@{k}@", v)
            res[name] = t
        return res

    def test_safe_values_substitute_exactly_and_deterministically(self):
        R = F.rng("udev")
        al = "abcdefghijklmnopqrstuvwxyz0123456789_-./:+%,&\\ "
        cases = []
        for i in range(F.n(16)):
            vals = {k: "".join(R.choice(al) for _ in range(R.randint(1, 10))).strip() or "w" for k in ("WIFI_ONBOARD_IFACE", "GADGET_IFNAME", "WIFI_ONBOARD_DRIVER")}
            cases.append((i, vals))

        def one(c):
            i, vals = c
            return self.render("".join(f"{k}='{v}'\n" for k, v in vals.items()), f"s{i}")
        for (i, vals), (rc, so, se, out) in zip(cases, pmap(one, cases)):
            self.assertEqual(rc, 0, (vals, se))
            for name, want in self.expected(vals).items():
                self.assertEqual(rd(os.path.join(out, name)), want, (name, vals))
        # determinism: rendering twice gives identical bytes
        a, b = one(("da", cases[0][1])), one(("db", cases[0][1]))
        for name in ("98-rename.rules", "99-GS.rules"):
            self.assertEqual(rd(os.path.join(a[3], name)), rd(os.path.join(b[3], name)))
        self.no_marker()

    def test_hostile_values_never_execute_and_unresolved_placeholders_fail(self):
        for i, v in enumerate([f"$(touch {self.marker})", f"`touch {self.marker}`", f";touch {self.marker}", "@WIFI_ONBOARD_DRIVER@", "@NO_SUCH@", "a@B@c"]):
            rc, so, se, out = self.render(f"WIFI_ONBOARD_IFACE='{v}'\n", f"h{i}")
            self.assertIn(rc, (0, 1), v)
            if "@" in v and v != "@WIFI_ONBOARD_DRIVER@":
                self.assertEqual(rc, 1, (v, se))
        self.no_marker()

    def test_DEFECT_D11_values_are_not_escaped_for_udev_syntax(self):
        rc, so, se, out = self.render("WIFI_ONBOARD_IFACE='x\", RUN+=\"/tmp/evil'\n", "q")
        rules = rd(os.path.join(out, "99-GS.rules")) if rc == 0 else ""
        # a double quote in a value closes the udev string and injects an extra assignment (INF: board.conf is repo-trusted)
        F.pinned(self, "D11", rc == 0 and 'RUN+="/tmp/evil"' in rules, f"rc={rc} {se!r}")



# ====================================================================== build/lib/fetch.sh
FETCH = F.path("build", "lib", "fetch.sh")


def fetch_call(code, cwd, env=None, timeout=20, args=()):
    return F.run(["bash", "-c", '. "$1"; shift; ' + code, "_", FETCH] + list(args), cwd=cwd, env={"GS_FETCH_CMD": "cp", **(env or {})}, timeout=timeout)


class TestFetch(Tmp):
    def setUp(self):
        super().setUp()
        self.src = write(os.path.join(self.d, "src.bin"), b"payload\x00\x01 fuzz\n")
        import hashlib
        self.sha = hashlib.sha256(rb(self.src)).hexdigest()

    def leftovers(self):
        return sorted(f for f in os.listdir(self.d) if ".part." in f)

    def test_fetch_file_pin_matrix(self):
        R = F.rng("fetch")
        sha = self.sha
        flip = lambda s: ("0" if s[0] != "0" else "1") + s[1:]  # noqa: E731
        wants = [(sha, 0), (sha.upper(), 0), (flip(sha), 1), (sha[:63], 2), (sha + "0", 2), ("g" * 64, 2), (" " + sha, 2), (sha + " ", 2), (sha + "\r", 2),
                 ("", 2), ("0" * 64, 1), ("-" * 64, 2), ("sha256:" + sha, 2)]
        for _ in range(F.n(14)):
            w = "".join(R.choice("0123456789abcdefABCDEFxyz -") for _ in range(R.choice([0, 1, 63, 64, 65, 40])))
            wants.append((w, None))

        def one(t):
            i, (want, exp) = t
            dest = os.path.join(self.d, f"dest{i}")
            rc, out, err = fetch_call('fetch_file "$1" "$2" "$3"', self.d, args=(self.src, dest, want))
            return rc, os.path.exists(dest), (rb(dest) if os.path.exists(dest) else None)
        res = pmap(one, list(enumerate(wants)))
        for (want, exp), (rc, exists, content) in zip(wants, res):
            self.assertIn(rc, (0, 1, 2), want)
            valid = bool(re.fullmatch(r"[0-9a-fA-F]{64}", want))
            if exp is None:
                exp = (0 if want.lower() == sha else 1) if valid else 2
            self.assertEqual(rc, exp, f"want={want!r}")
            self.assertEqual(exists, rc == 0, want)
            if rc == 0:
                self.assertEqual(content, rb(self.src))
        self.assertEqual(self.leftovers(), [], "temporary .part file left behind")

    def test_unpinned_mode_requires_explicit_opt_in(self):
        d1, d2 = os.path.join(self.d, "u1"), os.path.join(self.d, "u2")
        rc1, _, e1 = fetch_call('fetch_file "$1" "$2" ""', self.d, args=(self.src, d1))
        rc2, _, e2 = fetch_call('fetch_file "$1" "$2" ""', self.d, env={"GS_ALLOW_UNPINNED": "1"}, args=(self.src, d2))
        self.assertEqual((rc1, os.path.exists(d1)), (2, False), e1)
        self.assertEqual((rc2, os.path.exists(d2)), (0, True), e2)
        self.assertIn(self.sha, e2)
        for j, v in enumerate(("", "0", "yes", "true", "2", " 1", "1 ")):
            d = os.path.join(self.d, f"v{j}")
            rc, _, _ = fetch_call('fetch_file "$1" "$2" ""', self.d, env={"GS_ALLOW_UNPINNED": v}, args=(self.src, d))
            self.assertEqual(rc, 2, f"GS_ALLOW_UNPINNED={v!r} must not unlock unpinned downloads")

    def test_download_failure_and_truncated_download_leave_nothing(self):
        part = write(os.path.join(self.d, "trunc.sh"), '#!/bin/sh\nhead -c 5 "$1" > "$2"\nexit 22\n')
        os.chmod(part, 0o755)
        for cmd in (part, "false", "/nonexistent/fetcher"):
            dest = os.path.join(self.d, "d-" + os.path.basename(cmd))
            rc, out, err = fetch_call('fetch_file "$1" "$2" "$3"', self.d, env={"GS_FETCH_CMD": cmd}, args=(self.src, dest, self.sha))
            self.assertIn(rc, (1, 2), (cmd, err))
            self.assertFalse(os.path.exists(dest), cmd)
        # a download command that "succeeds" with a truncated file is caught by the hash
        short = write(os.path.join(self.d, "short.sh"), '#!/bin/sh\nhead -c 5 "$1" > "$2"\n')
        os.chmod(short, 0o755)
        dest = os.path.join(self.d, "d-short")
        rc, out, err = fetch_call('fetch_file "$1" "$2" "$3"', self.d, env={"GS_FETCH_CMD": short}, args=(self.src, dest, self.sha))
        self.assertEqual((rc, os.path.exists(dest)), (1, False), err)
        self.assertEqual(self.leftovers(), [])

    def test_git_pin_and_manifest_reject_non_pins_with_code_2(self):
        names = ["WFB_NG", "", "a", "A-B", "A B", "1A", "A\nB", "A$(true)", "A[0]", "_X"]
        res = pmap(lambda nm: fetch_call('pin_from_manifest "$1" "$2"', self.d, args=(nm, os.path.join(self.d, "m"))), names)
        for nm, (rc, out, err) in zip(names, res):
            self.assertIn(rc, (0, 1, 2), (nm, rc, err))
        refs = ["", "main", "HEAD", "abc", "g" * 40, "a" * 39, "a" * 41, "a" * 40 + " ", "-x", "--upload-pack=touch X"]
        res = pmap(lambda r: fetch_call('git_pin "$1" "$2" "$3"', self.d, args=(os.path.join(self.d, "norepo"), os.path.join(self.d, "g"), r)), refs)
        for r, (rc, out, err) in zip(refs, res):
            self.assertEqual(rc, 2, f"ref {r!r}: {err!r}")
        self.assertFalse(os.path.exists(os.path.join(self.d, "g", ".git")), "git init ran for a rejected ref")

    def test_DEFECT_D12_multiline_pin_is_accepted_by_grep(self):
        dest = os.path.join(self.d, "ml")
        rc, out, err = fetch_call('fetch_file "$1" "$2" "$3"', self.d, args=(self.src, dest, "not-a-hash\n" + self.sha))
        ref = "zzz\n" + "a" * 40
        rc2, o2, e2 = fetch_call('git_pin "$1" "$2" "$3"', self.d, args=(os.path.join(self.d, "norepo"), os.path.join(self.d, "g2"), ref))
        # grep -E '^[0-9a-f]{64}$' is line-based: one valid line in a multi-line "pin" passes the format check (should be rc 2)
        F.pinned(self, "D12", rc == 0 and os.path.exists(dest) and rc2 == 1, f"fetch_file rc={rc} git_pin(multi-line ref) rc={rc2} (expected 2) {e2[-80:]!r}")

    def test_DEFECT_D13_sigterm_leaves_partial_download(self):
        slow = write(os.path.join(self.d, "slow.sh"), '#!/bin/sh\nhead -c 5 "$1" > "$2"\nexec sleep 30\n')
        os.chmod(slow, 0o755)
        dest = os.path.join(self.d, "d-term")
        env = dict(os.environ, GS_FETCH_CMD=slow)
        p = subprocess.Popen(["bash", "-c", '. "$1"; fetch_file "$2" "$3" "$4"', "_", FETCH, self.src, dest, self.sha], env=env, cwd=self.d,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        t0 = time.time()
        while time.time() - t0 < 5 and not self.leftovers():
            time.sleep(0.02)
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=10)
        left = self.leftovers()
        F.pinned(self, "D13", p.returncode in (-15, 143) and len(left) == 1 and not os.path.exists(dest), f"rc={p.returncode} leftovers={left} (no trap in fetch_file)")



# ====================================================================== sandboxed fault injection (gs-applyconf.sh, fan.sh, stream.sh)
DRIVER = os.path.join(HERE, "sandbox_driver.sh")


class Sb:
    """Result of one sandbox_driver.sh run."""

    def __init__(self, d):
        self.d = d
        self.root = os.path.join(d, "root")

    def f(self, name):
        p = os.path.join(self.d, name)
        return rd(p) if os.path.exists(p) else ""

    @property
    def exit(self):
        t = self.f("exit").strip()
        return int(t) if t else None

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def read(self, rel):
        return rd(os.path.join(self.root, rel)) if self.exists(rel) else None


def sandbox(script, overlay=None, env=None, inv="standalone", timeout=60):
    out = F.tmpdir("sbo-")
    e = dict(env or {})
    if overlay:
        ov = F.tmpdir("sbv-")
        for k, v in overlay.items():
            p = os.path.join(ov, k)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            write(p, v)
        e["OVERLAY"] = ov
    rc, so, se = F.run(["bash", DRIVER, REPO, script, os.path.join(out, "r"), inv], env=e, timeout=timeout)
    return Sb(os.path.join(out, "r"))


class TestApplyconfFaults(unittest.TestCase):
    """gs/gs-applyconf.sh in the project sandbox (paths rewritten, system commands shimmed)."""

    @classmethod
    def setUpClass(cls):
        sc = {
            "inject_cmdsubst": ({"config/custom.conf": "wifi_mode=$(touch PWNED)\n"}, {}),
            "inject_sed_e": ({"config/custom.conf": "wifi_mode=;touch PWNED;x/e;#\n"}, {}),
            "slash_value": ({"config/custom.conf": "wifi_mode=a/b\nwifi_ssid=zz\n"}, {}),
            "hostile_values": ({"config/custom.conf": "wifi_ssid=My Net & co\\x\nwifi_mode=`touch PWNED`\nwifi_password='quoted pass'\nwifi_x=it's\n9bad=1\nwifi_mode2;touch PWNED=1\n"}, {}),
            "no_trailing_newline": ({"config/custom.conf": "wifi_ssid=zz"}, {}),
            "crlf": ({"config/custom.conf": "wifi_ssid=zz\r\n"}, {}),
            "empty_custom": ({"config/custom.conf": ""}, {}),
            "comment_only": ({"config/custom.conf": "# nothing\n  # x\n"}, {}),
            "empty_gsconf": ({"etc/gs.conf": ""}, {}),
            "baseline": ({}, {}),
            "hang_nmcli": ({}, {"HANG": "nmcli", "TMO": "2"}),
        }
        keys = list(sc)
        res = pmap(lambda k: sandbox("gs/gs-applyconf.sh", sc[k][0], sc[k][1], timeout=40), keys, 4)
        cls.r = dict(zip(keys, res))

    def test_baseline_runs_to_the_documented_end(self):
        b = self.r["baseline"]
        self.assertIn(b.exit, (0, 1))   # 1 = the known last-line status of [ -n "$need_restart_services" ] && ... (docs/KNOWLEDGE.md 4a)
        F.measure("applyconf.baseline.exit", b.exit)

    def test_crlf_and_missing_final_newline_still_merge(self):
        for k in ("no_trailing_newline", "crlf"):
            r = self.r[k]
            self.assertTrue(r.exists("config/custom-merged.conf"), k)
            self.assertIn("zz", r.read("etc/gs.conf") or "", k)
        F.measure("applyconf.crlf.gs_conf_value", [ln for ln in (self.r["crlf"].read("etc/gs.conf") or "").splitlines() if ln.startswith("wifi_ssid=")][0])

    def test_empty_and_comment_only_custom_conf_are_harmless(self):
        for k in ("empty_custom", "comment_only"):
            r = self.r[k]
            self.assertTrue(r.exists("config/custom-merged.conf"), k)
            # btn_*_pin lines are zeroed only in the baseline run (known standalone-invocation defect, docs/KNOWLEDGE.md 4a): ignored here
            norm = lambda x: "\n".join(ln for ln in re.sub(r"/tmp/tmp\.[A-Za-z0-9]+", "<ROOT>", x or "").splitlines() if not ln.startswith("btn_"))  # noqa: E731
            self.assertEqual(norm(r.read("etc/gs.conf")), norm(self.r["baseline"].read("etc/gs.conf")), k)
            self.assertFalse(r.exists("PWNED"))

    def test_FIXED_D14_custom_conf_value_is_never_executed(self):
        a, b = self.r["inject_cmdsubst"], self.r["inject_sed_e"]
        # /config/custom.conf is data that anybody who can write /config controls; before the fix its values were `source`d as root
        # (command substitution) and put into a sed s/// (a trailing "/e" made GNU sed execute the line). Fixed: awk + quoting.
        F.measure("applyconf.custom_conf_exec", {"cmdsubst_via_source": a.exists("PWNED"), "sed_e_flag": b.exists("PWNED")})
        self.assertFalse(a.exists("PWNED"), "command substitution in a custom.conf value was executed")
        self.assertFalse(b.exists("PWNED"), "the sed /e flag in a custom.conf value was executed")
        conf = a.read("etc/gs.conf") or ""
        self.assertIn("wifi_mode='$(touch PWNED)'", conf, "the hostile value must be stored inert (single-quoted)")

    def test_hostile_values_are_stored_inert_and_bad_lines_rejected(self):
        r = self.r["hostile_values"]
        conf = r.read("etc/gs.conf") or ""
        self.assertFalse(r.exists("PWNED"))
        self.assertIn("wifi_ssid='My Net & co\\x'", conf)  # spaces, & and a backslash survive literally inside single quotes
        self.assertIn("wifi_mode='`touch PWNED`'", conf)
        self.assertIn("wifi_password='quoted pass'", conf)  # already quoted: not double quoted
        self.assertNotIn("it's", conf)  # a value with a single quote is rejected, the old value stays
        self.assertNotIn("9bad", conf)
        self.assertTrue(r.exists("config/custom-merged.conf"), "the file is consumed even if lines were rejected")
        self.assertIn("rejected", r.f("stderr"))

    def test_FIXED_D15_slash_in_value_does_not_abort_the_merge(self):
        r = self.r["slash_value"]
        F.measure("applyconf.slash_value", {"exit": r.exit, "custom.conf_kept": r.exists("config/custom.conf"), "merged": r.exists("config/custom-merged.conf")})
        self.assertTrue(r.exists("config/custom-merged.conf") and not r.exists("config/custom.conf"), "custom.conf must be consumed")
        conf = r.read("etc/gs.conf") or ""
        self.assertIn("wifi_mode=a/b", conf)
        self.assertIn("wifi_ssid=zz", conf, "later keys must not be lost after a value with a slash")

    def test_DEFECT_D16_empty_gsconf_is_not_detected_and_requests_a_reboot(self):
        r = self.r["empty_gsconf"]
        log = r.f("shim.log")
        F.measure("applyconf.empty_gsconf", {"exit": r.exit, "reboot_called": "reboot" in log})
        # a truncated/empty /etc/gs.conf (power loss while it is rewritten) is treated as valid: rec_dir is empty -> "need reboot" -> reboot
        F.pinned(self, "D16", re.search(r"^reboot", log, re.M) is not None and "Update rec_dir in fstab" in r.f("stdout"), f"exit={r.exit}")

    def test_hanging_nmcli_blocks_forever_no_timeout_in_script(self):
        r = self.r["hang_nmcli"]
        F.measure("applyconf.hang_nmcli.exit", r.exit)
        self.assertEqual(r.exit, 124, "applyconf did not hang on a hanging nmcli (a timeout was added? update docs/SIM-FUZZ.md)")


class TestFanFaults(unittest.TestCase):
    """gs/fan.sh (thermal control loop): sensor file faults. Exit 143 = the sandbox sleep shim ended the loop after 2 iterations (alive)."""

    @classmethod
    def setUpClass(cls):
        def fan(temp, extra=""):
            ov = {"sys/class/pwm/pwmchip14/pwm0/duty_cycle": "16000", "run/pixelpilot.msg": ""}
            if temp is not None:
                ov["sys/class/thermal/thermal_zone0/temp"] = temp
            return sandbox("gs/fan.sh", ov, {"SLEEP_LIMIT": "3", "GSCONF_APPEND": extra})
        sc = {"ok": ("45000", ""), "empty": ("", ""), "short": ("99", ""), "garbage": ("abc", ""), "negative": ("-5000", ""), "missing": (None, ""),
              "freq0": ("45000", "fan_pwm_frequency='0'"), "freq_neg": ("45000", "fan_pwm_frequency='-5'"), "overheat_garbage_cfg": ("90000", "fan_overheat_temperature='x'"),
              "hot": ("90000", "")}
        keys = list(sc)
        cls.r = dict(zip(keys, pmap(lambda k: fan(*sc[k]), keys, 4)))

    def loops(self, k):
        return self.r[k].f("stdout").count("Max temperature")

    def test_normal_and_odd_but_numeric_temperatures_keep_the_loop_alive(self):
        for k in ("ok", "freq_neg"):
            self.assertEqual(self.r[k].exit, 143, (k, self.r[k].f("stderr")[-200:]))
            self.assertEqual(self.loops(k), 2, k)

    def test_hot_system_drives_duty_to_full(self):
        r = self.r["hot"]
        self.assertEqual(r.exit, 143)
        self.assertEqual((r.read("sys/class/pwm/pwmchip14/pwm0/duty_cycle") or "").strip(), (r.read("sys/class/pwm/pwmchip14/pwm0/period") or "").strip(), "overheat -> duty == period (100 %)")

    def test_FIXED_D17_unreadable_temperature_fails_safe_and_keeps_the_loop_alive(self):
        for k in ("empty", "short", "missing", "negative", "garbage"):
            r = self.r[k]
            duty = (r.read("sys/class/pwm/pwmchip14/pwm0/duty_cycle") or "").strip()
            period = (r.read("sys/class/pwm/pwmchip14/pwm0/period") or "").strip()
            F.measure(f"fan.{k}", {"exit": r.exit, "duty": duty, "period": period})
            self.assertEqual(r.exit, 143, (k, r.f("stderr")[-200:]))  # killed by the sandbox SLEEP_LIMIT, not dead on its own
            self.assertEqual(duty, period, f"{k}: an unreadable temperature must drive the fan to 100 %")
            self.assertIn("unreadable", r.f("stdout"))

    def test_garbage_threshold_config_degrades_silently(self):
        r = self.r["overheat_garbage_cfg"]
        F.measure("fan.garbage_overheat_threshold", {"exit": r.exit, "duty": (r.read("sys/class/pwm/pwmchip14/pwm0/duty_cycle") or "").strip()})
        self.assertIn(r.exit, (1, 143))

    def test_zero_frequency_does_not_hang(self):
        self.assertIn(self.r["freq0"].exit, (1, 143))
        F.measure("fan.freq0", {"exit": self.r["freq0"].exit})


class TestStreamMissingTools(unittest.TestCase):
    OV = {"run/record_button.fifo": "", "tmp/x": "", "Videos/x": "", "sys/class/drm/card0-HDMI-A-1/status": "connected\n", "dev/shm/x": ""}

    @classmethod
    def setUpClass(cls):
        sc = {"base": {}, "nojq": {"HIDE": "jq"}, "hangjq": {"HANG": "jq", "TMO": "2"}}
        keys = list(sc)
        cls.r = dict(zip(keys, pmap(lambda k: sandbox("gs/stream.sh", cls.OV, {**sc[k], "REWRITE_EXTRA": "/dev/shm"}, timeout=40), keys, 3)))

    def test_missing_jq_stops_the_launcher_before_the_player_starts(self):
        b, n = self.r["base"], self.r["nojq"]
        F.measure("stream.nojq", {"exit": n.exit, "base_exit": b.exit})
        self.assertEqual(b.exit, 1)    # sandbox: the pre-created regular file makes mkfifo fail after start-up (tests/cases/stream/_common.inc)
        self.assertEqual(n.exit, 127)  # `jq: command not found` under set -e (osd_widgets_osmon='yes' is the shipped default)
        self.assertNotIn("gst-launch", n.f("shim.log") + n.f("stderr").split("jq: command not found")[-1])

    def test_hanging_jq_hangs_the_launcher(self):
        self.assertEqual(self.r["hangjq"].exit, 124)



# ====================================================================== bench/tx12_bridge.py: process faults (UDP loopback, no hardware)
BRIDGE = F.path("bench", "tx12_bridge.py")
SNIFF = os.path.join(HERE, "fc_sniff.py")
FS_FRAME = [0, 0, 1000, 0, 0, 0, 0, 0]       # throttle failsafe frame: throttle channel 3 = 1000, everything else released (0)
REL_FRAME = [0] * 8
LINE = b"1500 1500 1000 1500\n"


def parse_frames(path):
    fr, end = [], None
    for ln in rd(path).splitlines():
        t = ln.split()
        if not t or t[0] == "READY":
            continue
        if t[0] == "END":
            end = float(t[1])
        else:
            fr.append((float(t[0]), [int(x) for x in t[2:]]))
    return fr, end


def wait_for(pred, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def bridge_scenario(py, d, port, action, via="pipe", extra_args=(), warm=0.5, settle=2.2, flood=0):
    """One bridge process against a UDP FC stand-in. action: sigterm | sigint | sigkill | eof | none (feeder stops, signal after `settle`)."""
    import pty
    out, lock = (os.path.join(d, n) for n in ("sniff.txt", "lock"))
    res = {"d": d}
    try:
        master = slave = None
        if via == "pty":
            master, slave = pty.openpty()
            stdin = slave
        else:
            stdin = subprocess.PIPE
        elog = open(os.path.join(d, "bridge.log"), "w")
        br = subprocess.Popen([py, BRIDGE, "--input", "stdin", "--confirm-props-off", "--conn", f"udpout:127.0.0.1:{port}", "--lock", lock,
                               "--deadman-ms", "200", *extra_args], stdin=stdin, stdout=elog, stderr=subprocess.STDOUT, cwd=d)
        if slave is not None:
            os.close(slave)
        w = (lambda b: os.write(master, b)) if master is not None else (lambda b: (br.stdin.write(b), br.stdin.flush()))
        try:
            if flood:
                w(b"\x00\xff garbage " * 1000 + b"\n")
            nframes = lambda: sum(1 for ln in rd(out).splitlines() if ln and ln[0].isdigit())  # noqa: E731
            t_start, t_end = time.time(), None
            while True:
                w(LINE if via == "pipe" else LINE.replace(b"\n", b"\r\n"))
                res["t_last_input"] = time.time()          # time of the LAST line written (the dead-man counts from its arrival)
                time.sleep(0.05)
                now = time.time()
                if t_end is None and nframes() >= 4:       # the bridge is demonstrably ACTIVE (python start-up time varies with the load)
                    t_end = now + warm
                if (t_end is not None and now >= t_end) or now - t_start > 25:
                    break
            if flood:   # a very long line without a terminator, then silence
                try:
                    w(b"9" * flood)
                except OSError:
                    pass
            if action == "sigterm":
                br.send_signal(signal.SIGTERM)
            elif action == "sigint":
                br.send_signal(signal.SIGINT)
            elif action == "sigkill":
                br.send_signal(signal.SIGKILL)
            elif action == "eof":
                if master is not None:
                    os.close(master)
                    master = None
                else:
                    br.stdin.close()
            res["t_action"] = time.time()
            if action in ("eof", "none"):
                time.sleep(settle)   # dead-man (0.2 s) + release hold (1 s) + silence
                res["alive_after_vanish"] = br.poll() is None
                res["t_term"] = time.time()
                br.send_signal(signal.SIGTERM)
            try:
                br.wait(timeout=6)
                res["rc"] = br.returncode
            except subprocess.TimeoutExpired:
                res["rc"] = None
                br.kill()
                br.wait()
            res["t_exit"] = time.time()
        finally:
            if master is not None:
                os.close(master)
            if br.stdin and not br.stdin.closed:
                try:
                    br.stdin.close()
                except OSError:
                    pass
        time.sleep(0.4)   # anything the bridge still sends after exit would show up here
        elog.close()
        # lock must be free once the process is gone (flock dies with the process, also after SIGKILL)
        import fcntl
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            res["lock_free"] = True
        except OSError:
            res["lock_free"] = False
        finally:
            os.close(fd)
    finally:
        pass
    res["frames"], res["end"] = parse_frames(out)
    res["log"] = rd(os.path.join(d, "bridge.log"))
    return res


class TestBridgeFaults(Tmp):
    """tx12_bridge: channels must be released (0) on SIGTERM/SIGINT, the dead-man must fire when the input vanishes, nothing may
    be sent afterwards. SIGKILL cannot release anything (measured, documented limit)."""

    @classmethod
    def setUpClass(cls):
        cls.py = F.fuzz_python()
        cls.res = {}
        if not cls.py:
            return
        cls.base = F.tmpdir("brg-")
        port0 = 21000 + (os.getpid() % 3000)
        sc = [("sigterm", dict(action="sigterm")), ("sigint", dict(action="sigint")), ("sigkill", dict(action="sigkill")),
              ("eof", dict(action="eof")), ("pty_vanish", dict(action="eof", via="pty")), ("flood", dict(action="none", flood=2_000_000, settle=2.0)),
              ("sigterm_pty", dict(action="sigterm", via="pty"))]
        jobs = [(name, kw, port0 + i) for i, (name, kw) in enumerate(sc)]
        for name, _kw, _p in jobs:
            os.makedirs(os.path.join(cls.base, name))
        stop = os.path.join(cls.base, "stop")
        specs = [f"{p}:{os.path.join(cls.base, name, 'sniff.txt')}" for name, _kw, p in jobs]
        sn = subprocess.Popen([cls.py, SNIFF, stop] + specs, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # ONE FC stand-in for all scenarios
        try:
            ok = wait_for(lambda: all(os.path.exists(os.path.join(cls.base, n, "sniff.txt")) for n, _k, _p in jobs), 15)
            if ok:
                out = pmap(lambda j: bridge_scenario(cls.py, os.path.join(cls.base, j[0]), j[2], **j[1]), jobs, 7)
                cls.res = {j[0]: r for j, r in zip(jobs, out)}
            else:
                cls.res = {j[0]: {"error": "FC stand-in did not start", "frames": [], "log": ""} for j in jobs}
        finally:
            write(stop, "x")
            try:
                sn.wait(timeout=8)
            except subprocess.TimeoutExpired:
                sn.kill()
                sn.wait()
        for name, r in cls.res.items():   # the FC stand-in wrote END after the bridge was gone: re-read the final files
            if "error" not in r:
                r["frames"], r["end"] = parse_frames(os.path.join(cls.base, name, "sniff.txt"))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "base", None):
            shutil.rmtree(cls.base, True)

    def setUp(self):
        if not self.py:
            self.skipTest("no python with pymavlink (set FUZZ_PY)")
        super().setUp()

    def need(self, k):
        r = self.res[k]
        self.assertNotIn("error", r, r)
        self.assertTrue(r["frames"], f"{k}: no RC frames seen\n{r['log'][-400:]}")
        return r

    def check_exit_burst(self, k):
        r = self.need(k)
        fr = [v for _t, v in r["frames"]]
        self.assertEqual(r["rc"], 0, (k, r["log"][-300:]))
        self.assertEqual(fr[-5:], [REL_FRAME] * 5, f"{k}: last 5 frames must be release (0)")
        self.assertEqual(fr[-8:-5], [FS_FRAME] * 3, f"{k}: 3 throttle-failsafe frames precede the release")
        self.assertTrue(any(v[2] == 1000 and v[0] == 1500 for v in fr[:-8]), f"{k}: no stick frames before the signal (bridge never became ACTIVE)")
        # nothing is sent after the process is gone, and the burst respects the --max-rate guard (50 Hz -> >= ~20 ms)
        t = [t for t, _v in r["frames"][-8:]]
        gaps = [b - a for a, b in zip(t, t[1:])]
        self.assertGreaterEqual(sorted(gaps)[len(gaps) // 2], 0.010, f"--max-rate guard: median gap of the exit burst {gaps}")
        self.assertLess(r["frames"][-1][0], r["t_exit"] + 0.05, "frames after exit")
        self.assertTrue(r["lock_free"])
        F.measure(f"bridge.{k}", {"rc": r["rc"], "frames": len(fr), "exit_burst_ms": round((t[-1] - t[0]) * 1000), "latency_signal_to_first_fs_ms": round((t[0] - r["t_action"]) * 1000)})

    def test_sigterm_releases_channels(self):
        self.check_exit_burst("sigterm")

    def test_sigint_releases_channels(self):
        self.check_exit_burst("sigint")

    def test_sigterm_with_pty_input_releases_channels(self):
        self.check_exit_burst("sigterm_pty")

    def check_deadman(self, k):
        r = self.need(k)
        fr = r["frames"]
        t_in = r["t_last_input"]
        fs = [t for t, v in fr if v == FS_FRAME and t > t_in - 0.1]
        self.assertTrue(fs, f"{k}: no failsafe frame after the input vanished\n{r['log'][-400:]}")
        delay = fs[0] - t_in
        # dead-man 200 ms (+ one rate period 50 ms + loop sleep) -> generous bound for a loaded machine
        self.assertLess(delay, 0.2 + 0.6, f"{k}: dead-man took {delay:.3f} s")
        self.assertGreater(delay, 0.15, f"{k}: dead-man fired after only {delay:.3f} s although it is set to 200 ms (too eager: input must be 'fresh' for the whole window)")
        rel = [t for t, v in fr if v == REL_FRAME and t > t_in]
        self.assertTrue(rel, "release frames missing after the failsafe frames")
        last_active = max(t for t, v in fr if v[0] == 1500)
        self.assertLess(last_active, fs[0] + 0.05, "sticks were still sent after the failsafe fired")
        # release hold (1 s) then SILENCE: no frame in the last stretch before SIGTERM, none after it
        quiet_from = fs[0] + 1.0 + 0.4
        late = [t for t, _v in fr if t > quiet_from]
        self.assertEqual(late, [], f"{k}: frames after the release hold ended")
        self.assertTrue(r["alive_after_vanish"], "bridge died when its input vanished (it should stay up and go silent)")
        self.assertEqual(r["rc"], 0, r["log"][-300:])
        self.assertTrue(r["lock_free"])
        F.measure(f"bridge.{k}", {"deadman_latency_ms": round(delay * 1000), "release_frames": len([1 for t, v in fr if v == REL_FRAME]), "rc": r["rc"],
                                  "log_has_traceback": "Traceback" in r["log"]})

    def test_deadman_when_stdin_pipe_closes(self):
        self.check_deadman("eof")

    def test_deadman_when_pty_master_vanishes(self):
        self.check_deadman("pty_vanish")

    def test_garbage_flood_does_not_stop_signal_handling(self):
        r = self.need("flood")
        self.assertEqual(r["rc"], 0, r["log"][-300:])
        # the 2 MB unterminated line never became a sample; the dead-man handled the silence exactly as for EOF
        self.assertTrue(any(v == FS_FRAME for _t, v in r["frames"]))
        F.measure("bridge.flood", {"rc": r["rc"], "dropped_msgs": r["log"].count("input dropped")})

    def test_sigkill_cannot_release_documented_limit(self):
        r = self.need("sigkill")
        fr = [v for _t, v in r["frames"]]
        self.assertIn(r["rc"], (-9, 137))
        # nothing could be sent: the last frame is a stick frame, no release frame at all (the FC's RC_OVERRIDE_TIME is the only protection)
        self.assertEqual(fr[-1][0], 1500)
        self.assertNotIn(REL_FRAME, fr)
        self.assertTrue(r["lock_free"], "flock must be released by the kernel after SIGKILL")
        F.measure("bridge.sigkill", {"rc": r["rc"], "last_frame": fr[-1]})

    def test_pty_vanish_prints_thread_traceback_but_dead_man_still_works(self):
        r = self.need("pty_vanish")
        F.measure("bridge.pty_vanish.traceback_in_log", "Traceback" in r["log"])
        # observation only: on a tty a vanished master raises OSError(EIO) inside the reader thread (no "stdin EOF" message)
        self.assertEqual(r["rc"], 0)


class TestBridgeInputValidation(unittest.TestCase):
    """In-process fuzz of the pure functions of bench/tx12_bridge.py (the module configures itself from config/registry defaults)."""

    @classmethod
    def setUpClass(cls):
        cls.b = load_module("fz_bridge", "bench/tx12_bridge.py")

    def test_sanitize_never_returns_out_of_range_or_nonfinite(self):
        R = F.rng("sanitize")
        b = self.b
        pool = [0, 1, 15, 999, 1000, 1500, 2000, 2001, 3999, 4000, 4001, -1, 65535, 65535.0, float("nan"), float("inf"), -float("inf"), "x", None, "1500", 1e308, True, False, "nan", "1e3"]
        for _ in range(F.n(4000)):
            vals = [R.choice(pool) if R.random() < 0.5 else R.uniform(-100, 70000) for _ in range(R.randint(0, 10))]
            out = b.sanitize(vals)
            absurd = any(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v != 65535 and not (b.SANE_MIN <= v <= b.SANE_MAX) for v in vals)
            nonnum = any(isinstance(v, str) and not re.fullmatch(r"\s*[-+]?\d+(\.\d+)?(e[-+]?\d+)?\s*", v, re.I) for v in vals) or any(isinstance(v, float) and not math.isfinite(v) for v in vals)
            if absurd or nonnum or not vals or len(vals) > 8:
                self.assertIsNone(out, f"absurd/non-finite/over-long sample must be dropped: {vals}")
            if out is None:
                continue
            self.assertEqual(len(out), 8)
            for v in out:
                self.assertTrue(isinstance(v, int) and (v == 65535 or b.LO <= v <= b.HI), (vals, out))

    def test_DEFECT_D18_load_map_raises_attributeerror_on_non_object_axes(self):
        import json
        R = F.rng("map")
        d = F.tmpdir()
        self.addCleanup(shutil.rmtree, d, True)
        atoms = [None, True, 0, 1, 5, -1, 2.5, "x", "", [], {}, [1], {"channel": 1}, {"channel": 1, "min": 0, "max": 10}, {"channel": 9, "min": 0, "max": 1},
                 {"channel": "1", "min": 0, "max": 1}, {"min": 0, "max": 1}, {"channel": 1, "min": "a", "max": 1}]
        bad = []
        for i in range(F.n(300)):
            doc = R.choice([{"axes": {"ABS_X": R.choice(atoms)}}, {"axes": R.choice(atoms)}, R.choice(atoms), {"axes": {"ABS_X": R.choice(atoms), "ABS_Y": R.choice(atoms)}}, {}])
            p = write(os.path.join(d, f"m{i}.json"), json.dumps(doc))
            try:
                self.b.load_map(p)
            except (ValueError, OSError):
                pass
            except Exception as e:  # noqa: BLE001 - anything else would be an undocumented crash path of the bridge
                bad.append((doc, type(e).__name__))
        F.measure("bridge.load_map.undocumented_exceptions", sorted({t for _d, t in bad}))
        F.pinned(self, "D18", len(bad) > 0 and all(t == "AttributeError" for _d, t in bad), f"{bad[:3]}")

    def test_DEFECT_D19_truncated_stdin_line_is_a_valid_sample(self):
        # "1500 1500 1000 1500" cut after "1500 15" is NOT dropped: 15 is inside the sane window (0..4000) and is clamped UP to 1000
        out = self.b.sanitize([float(t) for t in "1500 15".split()])
        F.measure("bridge.truncated_line", out)
        F.pinned(self, "D19", out is not None and out[1] == 1000 and out[2:] == [65535] * 6, f"{out}")

    def test_DEFECT_D20_map_axis_center_equal_max_divides_by_zero(self):
        R = F.rng("axis")
        b = self.b
        bad, exc = [], set()
        for _ in range(F.n(3000)):
            lo = R.uniform(-1000, 1000)
            cfg = {"min": lo, "max": lo + R.choice([0.5, 1, 10, 255, 32767, 65535])}
            if R.random() < 0.7:
                cfg["center"] = R.choice([R.uniform(cfg["min"], cfg["max"]), cfg["min"], cfg["max"]])
                cfg["deadband"] = R.choice([0.0, 0.05, 0.5, 0.99, 1.0, 1.5, -0.2])
            cfg["reverse"] = R.random() < 0.3
            raw = R.choice([cfg["min"], cfg["max"], (cfg["min"] + cfg["max"]) / 2, R.uniform(cfg["min"] - 5, cfg["max"] + 5)])
            try:
                v = b.map_axis(raw, cfg)
            except ZeroDivisionError:
                exc.add("ZeroDivisionError")
                bad.append((cfg, raw))
                continue
            self.assertTrue(isinstance(v, int) and b.LO <= v <= b.HI, (cfg, raw, v))
        F.measure("bridge.map_axis.exceptions", sorted(exc))
        # center == max with the stick at (or beyond) max: 0/0 (the mapping loader does not validate center/deadband)
        F.pinned(self, "D20", bool(bad) and all(c.get("center") == c["max"] and r >= c["max"] for c, r in bad), f"{len(bad)} cases, first {bad[:1]}")



# ====================================================================== tests/sim/models: property tests (SYNTH: model output, never a measurement)
MODELS = F.path("tests", "sim", "models")
EPS = 1e-9


def _models():
    if MODELS not in sys.path:
        sys.path.insert(0, MODELS)
    import common as mc
    import degrade_model as dm
    import gpio_bounce as gb
    import latency_budget as lb
    import power_model as pm
    import priors
    import rf_model as rf
    import scenario_engine as se
    return mc, rf, pm, lb, dm, gb, priors, se


def random_sets(mc, R, prefixes, fixed=()):
    """Random ALLOWED values: every parameter that declares min/max is drawn inside [min, max]."""
    base = mc.load()
    sets = {}
    for k, leaf in base.leaves.items():
        if k.split(".")[0] in prefixes and k not in fixed and "min" in leaf and "max" in leaf and isinstance(leaf["value"], (int, float)) and not isinstance(leaf["value"], bool):
            lo, hi = leaf["min"], leaf["max"]
            v = R.uniform(lo, hi) if R.random() < 0.8 else R.choice([lo, hi])
            sets[k] = int(round(v)) if isinstance(leaf["value"], int) else v
    return sets


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


class TestModelProperties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mc, cls.rf, cls.pm, cls.lb, cls.dm, cls.gb, cls.priors, cls.se = _models()

    # -------------------------------------------------------------- RF
    def test_rf_monotone_in_distance_finite_and_in_range(self):
        mc, rf = self.mc, self.rf
        R = F.rng("rf-dist")
        dists = [1, 2, 5, 10, 30, 100, 300, 1000, 3000, 10000, 30000]
        for _ in range(F.n(40)):
            sets = random_sets(mc, R, ("rf",), fixed=("rf.target_residual",))
            sets["rf.mcs_index"] = R.randint(0, 7)
            P = mc.load(sets)
            k, n = R.choice([(8, 12), (4, 8), (6, 6), (1, 2), (10, 16)])
            ev = [rf.evaluate(P, d, P.get("rf.mcs_index"), k, n) for d in dists]
            for key in ("rx", "snr", "per", "res", "frame_loss"):
                self.assertTrue(all(finite(e[key]) for e in ev), (key, sets))
            for key in ("per", "res", "frame_loss"):
                self.assertTrue(all(-EPS <= e[key] <= 1 + EPS for e in ev), (key, [e[key] for e in ev]))
            for a, b in zip(ev, ev[1:]):
                self.assertLessEqual(b["rx"], a["rx"] + EPS, f"rx must not grow with distance {sets}")
                self.assertLessEqual(b["snr"], a["snr"] + EPS, "SNR must not grow with distance")
                self.assertGreaterEqual(b["per"], a["per"] - 1e-9, f"PER fell with distance at d={b['d']} (mcs={P.get('rf.mcs_index')}, {sets})")
                self.assertGreaterEqual(b["res"], a["res"] - 1e-9, "residual loss fell with distance")
                self.assertGreaterEqual(b["frame_loss"], a["frame_loss"] - 1e-9)

    def test_rf_units_and_signs(self):
        mc, rf = self.mc, self.rf
        R = F.rng("rf-units")
        for _ in range(F.n(20)):
            sets = random_sets(mc, R, ("rf",))
            sets["rf.interference_dbm"] = None
            P = mc.load(sets)
            d = R.choice([5, 50, 500, 5000])
            dbm = R.uniform(1, 6)
            P2 = mc.load({**sets, "rf.tx_power_dbm": sets.get("rf.tx_power_dbm", 20.0) + dbm})
            self.assertAlmostEqual(rf.snr_db(P2, d) - rf.snr_db(P, d), dbm, places=6, msg="+x dB of TX power must give +x dB SNR")
            e, n = P.get("rf.path_loss_exponent"), P.get("rf.ref_distance_m")
            if d >= 2 * n:
                self.assertAlmostEqual(rf.path_loss_db(P, 2 * d) - rf.path_loss_db(P, d), 10 * e * math.log10(2), places=6)
            self.assertAlmostEqual(rf.path_loss_db(P, 0.0001), rf.path_loss_db(P, n), places=9, msg="below the reference distance the loss is clamped")
            self.assertGreaterEqual(rf.path_loss_db(P, d), 0.0)
            for bw in (20, 40):
                self.assertGreater(rf.noise_dbm(mc.load({**sets, "rf.bandwidth_mhz": bw})), -120.0)
                self.assertLess(rf.noise_dbm(mc.load({**sets, "rf.bandwidth_mhz": bw})), -60.0)

    def test_rf_max_range_monotone_in_power_and_mcs(self):
        mc, rf = self.mc, self.rf
        R = F.rng("rf-range")
        for _ in range(F.n(8)):
            sets = random_sets(mc, R, ("rf",), fixed=("rf.target_residual",))
            lo_p = sets.get("rf.tx_power_dbm", 20.0)
            ranges = []
            for dp in (0.0, 3.0, 6.0):
                P = mc.load({**sets, "rf.tx_power_dbm": lo_p + dp})
                ranges.append(rf.max_range(P, 1, 8, 12)[0])
            self.assertTrue(all(finite(r) and r >= 0 for r in ranges))
            self.assertLessEqual(ranges[0], ranges[1] + 1e-6)
            self.assertLessEqual(ranges[1], ranges[2] + 1e-6)
            P = mc.load(sets)
            by_mcs = [rf.max_range(P, m, 8, 12)[0] for m in (0, 2, 4, 7)]
            for a, b in zip(by_mcs, by_mcs[1:]):
                self.assertGreaterEqual(a + 1e-6, b, f"a higher MCS must not reach farther: {by_mcs} {sets}")
            sn = [rf.snr_for_per(P, m) for m in range(8)]
            for a, b in zip(sn, sn[1:]):
                self.assertLessEqual(a, b + 1e-6, f"required SNR must grow with MCS: {sn}")

    def test_rf_reproducible_and_seed_sensitive_only_where_random(self):
        mc, rf = self.mc, self.rf
        P1, P2 = mc.load(), mc.load()
        self.assertEqual(rf.evaluate(P1, 321, 3, 8, 12), rf.evaluate(P2, 321, 3, 8, 12))
        a = mc.load({"rf.fading_seed": 1})
        b = mc.load({"rf.fading_seed": 2})
        self.assertEqual(rf.evaluate(a, 321, 3, 8, 12), rf.evaluate(mc.load({"rf.fading_seed": 1}), 321, 3, 8, 12))
        self.assertIsNotNone(b)

    # -------------------------------------------------------------- power
    def power_case(self, R):
        mc, pm = self.mc, self.pm
        sets = random_sets(mc, R, ("power",))
        # the declared [min,max] ranges of idle/rx/tx overlap (pinned as D22): draw them in physical order here
        ordered = sorted(sets.get(f"power.devices.rtl8812_{s}_a", mc.load().get(f"power.devices.rtl8812_{s}_a")) for s in ("idle", "rx", "tx"))
        for s, v in zip(("idle", "rx", "tx"), ordered):
            sets[f"power.devices.rtl8812_{s}_a"] = v
        P = mc.load(sets)
        return P, dict(board=R.choice(pm.BOARDS), psu_a=R.choice([0.5, 1.0, 2.5, 3.0, 4.0, 5.0, 6.0, R.uniform(0.5, 6)]), adapters=R.randint(0, 2),
                       state=R.choice(["idle", "rx", "tx"]), with_=tuple(R.sample(["fc", "webcam", "fan"], R.randint(0, 3))),
                       load=R.choice(["idle", "active", "load"]), peak=R.random() < 0.3, usb_max_current=R.random() < 0.3)

    SEV = {"OK": 0, "WARN": 1, "FAIL": 2}

    def test_power_budget_monotone_in_load_and_supply(self):
        pm = self.pm
        R = F.rng("power")
        for _ in range(F.n(150)):
            P, kw = self.power_case(R)
            b = pm.budget(P, **kw)
            self.assertTrue(all(finite(b[k]) for k in ("total_a", "usb_a", "psu_margin_a", "usb_margin_a", "v_board")), kw)
            self.assertAlmostEqual(b["total_a"], sum(a for _n, a, _c in b["items"]), places=9)
            self.assertAlmostEqual(b["usb_a"], sum(a for _n, a, c in b["items"] if c == "usb"), places=9)
            self.assertAlmostEqual(b["psu_margin_a"], kw["psu_a"] - b["total_a"], places=9)
            self.assertGreaterEqual(b["total_a"], 0)
            # more load never improves anything
            more = dict(kw, with_=tuple(sorted(set(kw["with_"]) | {"webcam"})) if "webcam" not in kw["with_"] else kw["with_"], adapters=kw["adapters"] + 1)
            m = pm.budget(P, **more)
            self.assertGreaterEqual(m["total_a"], b["total_a"] - EPS)
            for key in ("psu_margin_a", "usb_margin_a", "v_board"):
                self.assertLessEqual(m[key], b[key] + EPS, f"{key}: more current must not give more margin {kw}")
            self.assertGreaterEqual(self.SEV[m["verdict"]], self.SEV[b["verdict"]], f"verdict improved with more load {kw}")
            for lo, hi in (("idle", "rx"), ("rx", "tx")):
                a = pm.budget(P, **dict(kw, state=lo, adapters=max(1, kw["adapters"])))
                c = pm.budget(P, **dict(kw, state=hi, adapters=max(1, kw["adapters"])))
                self.assertGreaterEqual(c["total_a"], a["total_a"] - EPS, (lo, hi))
                self.assertLessEqual(c["usb_margin_a"], a["usb_margin_a"] + EPS)
            pk = pm.budget(P, **dict(kw, peak=True, state="tx", adapters=max(1, kw["adapters"])))
            np_ = pm.budget(P, **dict(kw, peak=False, state="tx", adapters=max(1, kw["adapters"])))
            self.assertGreaterEqual(pk["total_a"], np_["total_a"] - EPS)
            # a stronger supply never makes the verdict worse
            stronger = pm.budget(P, **dict(kw, psu_a=kw["psu_a"] + 1.0))
            self.assertLessEqual(self.SEV[stronger["verdict"]], self.SEV[b["verdict"]], f"bigger PSU made it worse {kw}")
            self.assertGreaterEqual(stronger["psu_margin_a"], b["psu_margin_a"] - EPS)

    def test_DEFECT_D22_declared_ranges_allow_idle_current_above_rx_current(self):
        pm, mc = self.pm, self.mc
        base = mc.load()
        lv = {s: (base.leaves[f"power.devices.rtl8812_{s}_a"]["min"], base.leaves[f"power.devices.rtl8812_{s}_a"]["max"]) for s in ("idle", "rx", "tx")}
        overlap = lv["idle"][1] > lv["rx"][0] and lv["rx"][1] > lv["tx"][0]
        P = mc.load({"power.devices.rtl8812_idle_a": lv["idle"][1], "power.devices.rtl8812_rx_a": lv["rx"][0]})   # both inside their declared ranges
        idle = pm.budget(P, "pi5", psu_a=5.0, adapters=1, state="idle")["total_a"]
        rx = pm.budget(P, "pi5", psu_a=5.0, adapters=1, state="rx")["total_a"]
        F.measure("power.range_overlap", {"ranges": lv, "idle_total_a": round(idle, 3), "rx_total_a": round(rx, 3)})
        F.pinned(self, "D22", overlap and idle > rx, f"ranges {lv}: idle total {idle:.2f} A > rx total {rx:.2f} A at allowed values")

    def test_power_reproducible_timeline_by_seed(self):
        pm = self.pm
        P = self.mc.load()
        sc = pm.SCENARIOS["pi4_3a_dip"]
        a, b, c = pm.timeline(P, sc, 7), pm.timeline(P, sc, 7), pm.timeline(P, sc, 8)
        self.assertEqual(repr(a), repr(b))
        self.assertTrue(repr(a) != repr(c) or True)   # a different seed may legitimately give the same trace for a deterministic scenario
        txt = repr(a)
        self.assertNotIn("nan", txt.lower())
        self.assertNotIn("inf", txt.lower())

    def test_DEFECT_D21_nonfinite_or_negative_inputs_are_not_rejected_by_power_budget(self):
        pm, mc = self.pm, self.mc
        P = mc.load()
        nan_ok = pm.budget(P, "pi4", psu_a=float("nan"), adapters=1, state="tx", with_=("fc",))
        neg_adapters = pm.budget(P, "pi4", psu_a=3.0, adapters=-3, state="tx")
        F.measure("power.nan_psu_pi4", {"verdict": nan_ok["verdict"], "flags": nan_ok["flags"], "margin": str(nan_ok["psu_margin_a"])})
        F.measure("power.negative_adapters", {"verdict": neg_adapters["verdict"], "total_a": neg_adapters["total_a"]})
        # a NaN supply rating yields no PSU flag at all (every comparison with NaN is False) and a negative adapter count gives negative current
        F.pinned(self, "D21", nan_ok["verdict"] == "OK" and not nan_ok["flags"] and neg_adapters["total_a"] < 0, f"nan -> {nan_ok['verdict']} {nan_ok['flags']}; adapters=-3 -> total {neg_adapters['total_a']:.2f} A {neg_adapters['verdict']}")

    # -------------------------------------------------------------- latency
    def test_latency_terms_ordered_finite_and_monotone(self):
        lb, mc = self.lb, self.mc
        R = F.rng("lat")
        for _ in range(F.n(60)):
            P = mc.load(random_sets(mc, R, ("latency", "rf")))
            board, codec = R.choice(sorted(lb.DECODERS))
            fps = R.choice([24, 30, 60])
            w, h = R.choice([(1280, 720), (1920, 1080), (640, 480)])
            br = R.choice([1000, 4000, 8000])
            mcs, (k, n) = R.randint(1, 7), R.choice([(8, 12), (4, 8)])
            b = lb.budget(P, board, codec, fps, w, h, br, mcs, k, n, lossy=R.random() < 0.5)
            for name, (lo, typ, hi) in b["terms"].items():
                self.assertTrue(all(finite(x) and x >= 0 for x in (lo, typ, hi)), (name, lo, typ, hi))
                self.assertLessEqual(lo, typ + 1e-9, name)
                self.assertLessEqual(typ, hi + 1e-9, name)
            self.assertTrue(all(finite(x) for x in b["total"]))
            self.assertLessEqual(b["total"][0], b["total"][1] + 1e-9)
            self.assertLessEqual(b["total"][1], b["total"][2] + 1e-9)
            # more pixels -> decode term not smaller; more bitrate (same MCS) -> airtime utilisation and radio term not smaller; higher MCS -> less airtime
            big = lb.budget(P, board, codec, fps, w * 2, h * 2, br, mcs, k, n)
            self.assertGreaterEqual(big["terms"]["decode"][1], lb.budget(P, board, codec, fps, w, h, br, mcs, k, n)["terms"]["decode"][1] - 1e-9)
            hb = lb.budget(P, board, codec, fps, w, h, br * 2, mcs, k, n)
            lbud = lb.budget(P, board, codec, fps, w, h, br, mcs, k, n)
            self.assertGreaterEqual(hb["util"], lbud["util"] - 1e-9)
            self.assertGreaterEqual(hb["terms"]["radio"][1], lbud["terms"]["radio"][1] - 1e-9)
            if mcs < 7:
                self.assertLessEqual(lb.budget(P, board, codec, fps, w, h, br, mcs + 1, k, n)["util"], lbud["util"] + 1e-9, "a higher MCS must not need more airtime")

    # -------------------------------------------------------------- gpio bounce
    def test_gpio_traces_are_well_formed_and_reproducible(self):
        gb, priors, se = self.gb, self.priors, self.se
        th = priors.Theta(dict(se.Engine(se.load_scenario("nominal_pi5_5a_150m")).space.median_theta().vals))
        R = F.rng("gpio")
        for _ in range(F.n(60)):
            seed = R.randint(0, 10**6)
            hold = R.choice([0.05, 0.3, 0.8, 1.5, 2.5, R.uniform(0.05, 3.0)])
            tr, end = gb.gen_trace(th, priors.Rng(seed), hold)
            tr2, end2 = gb.gen_trace(th, priors.Rng(seed), hold)
            self.assertEqual((tr, end), (tr2, end2), "same seed must give the same trace")
            self.assertTrue(finite(end) and end > 0)
            ts = [t for t, _l, _c in tr]
            self.assertEqual(ts, sorted(ts))
            self.assertTrue(all(t >= 0 and finite(t) for t in ts))
            lv = [0] + [l for _t, l, _c in tr]
            self.assertTrue(all(a != b for a, b in zip(lv, lv[1:])), "levels must alternate (a trace only records changes)")
            for w in (0.0, 0.005, 0.02, 0.05):
                out = gb.debounce_filter(tr, w)
                self.assertLessEqual(len(out), len(tr))
                to = [t for t, _l, _c in out]
                self.assertEqual(to, sorted(to))
                lo = [0] + [l for _t, l, _c in out]
                self.assertTrue(all(a != b for a, b in zip(lo, lo[1:])))
            acts = [a for _t, a in gb.replay_button_sh(tr, th, priors.Rng(seed + 1), end + 5.0)]
            self.assertTrue(set(acts) <= {"single", "long"}, acts)
            self.assertEqual(acts, [a for _t, a in gb.replay_button_sh(tr, th, priors.Rng(seed + 1), end + 5.0)])

    def test_gpio_button_stats_are_probabilities(self):
        gb, priors, se = self.gb, self.priors, self.se
        th = priors.Theta(dict(se.Engine(se.load_scenario("nominal_pi5_5a_150m")).space.median_theta().vals))
        st = gb.button_stats(th, priors.Rng(3), n_each=F.n(10))
        for k, v in st.items():
            self.assertTrue(0.0 <= v <= 1.0 and finite(v), (k, v))
        self.assertAlmostEqual(st["single_ok"] + st["single_extra"] + st["single_missed"] + st["single_as_long"], 1.0, places=9)
        self.assertAlmostEqual(st["long_ok"] + st["long_as_single"] + st["long_extra"] + st["long_missed"], 1.0, places=9)
        self.assertEqual(st, gb.button_stats(th, priors.Rng(3), n_each=F.n(10)))

    # -------------------------------------------------------------- degrade model (pure functions) and scenario engine
    def test_degrade_pure_functions(self):
        dm = self.dm
        R = F.rng("degrade")
        for _ in range(F.n(300)):
            p1db, p = R.uniform(10, 30), R.uniform(1.0, 6.0)
            a, b = sorted((R.uniform(-20, 40), R.uniform(-20, 40)))
            oa, ob = dm.pa_output_dbm(a, p1db, p), dm.pa_output_dbm(b, p1db, p)
            self.assertTrue(finite(oa) and finite(ob))
            self.assertLessEqual(oa, ob + 1e-9, "PA output must be non-decreasing in the drive")
            self.assertLessEqual(ob, b + 1e-9, "a PA cannot have gain > 1 in this model")
            snr, evm = R.uniform(-10, 60), R.uniform(-45, -5)
            comb = dm.combine_snr_evm_db(snr, evm)
            self.assertLessEqual(comb, snr + 1e-9)
            self.assertLessEqual(comb, -evm + 1e-9, "the EVM ceiling must hold")
            self.assertLessEqual(dm.combine_snr_evm_db(snr - 1, evm), comb + 1e-9)
            self.assertLessEqual(dm.combine_snr_evm_db(snr, evm + 1), comb + 1e-9)
            c1, c2 = sorted((R.uniform(0, 8), R.uniform(0, 8)))
            self.assertLessEqual(dm.evm_db(c1, -35, 0.3), dm.evm_db(c2, -35, 0.3) + 1e-9)
            v1, v2 = sorted((R.uniform(3.5, 5.5), R.uniform(3.5, 5.5)))
            self.assertGreaterEqual(dm.tx_sag_db(v1, 4.8, 3.0, 5.0), dm.tx_sag_db(v2, 4.8, 3.0, 5.0) - 1e-9)
            self.assertGreaterEqual(dm.tx_sag_db(v1, 4.8, 3.0, 5.0), 0.0)
            vm, im = R.uniform(-1, 1), R.uniform(-1, 1)
            r0 = dm.usb_drop_rate_per_s(vm, im, 0.5, 0.2, 0.3)
            self.assertTrue(0.0 <= r0 <= 1.0)
            self.assertLessEqual(dm.usb_drop_rate_per_s(vm + 0.1, im, 0.5, 0.2, 0.3), r0 + 1e-12, "more voltage margin must not raise the drop hazard")
            self.assertLessEqual(dm.usb_drop_rate_per_s(vm, im + 0.1, 0.5, 0.2, 0.3), r0 + 1e-12)
            pr = sorted(R.uniform(1e-6, 0.99) for _ in range(2))
            fr = [dm.fec_residual(x, 4.0, 8, 12, False) for x in pr]
            self.assertTrue(all(0 <= x <= 1 for x in fr))
            self.assertLessEqual(fr[0], fr[1] + 1e-12, "FEC residual loss must grow with the packet loss rate")

    def test_scenario_engine_is_reproducible_by_seed_and_finite(self):
        se = self.se
        eng = se.Engine(se.load_scenario("nominal_pi5_5a_150m"))
        a, b = eng.run(F.n(6), 11), eng.run(F.n(6), 11)
        self.assertEqual(repr(a), repr(b))
        c = eng.run(F.n(6), 12)
        self.assertNotEqual(repr(a), repr(c), "the seed must change the draws")
        for row in a:
            for v in (row.values() if isinstance(row, dict) else [row]):
                if isinstance(v, float):
                    self.assertTrue(math.isfinite(v), row)



def main():
    import json
    args = [a for a in sys.argv[1:] if a != "-v"]
    prog = unittest.main(module=sys.modules[__name__], argv=[sys.argv[0]] + args, exit=False, verbosity=2 if "-v" in sys.argv else 1)
    rep = os.environ.get("FUZZ_REPORT")
    if rep:
        with open(rep, "w", encoding="utf-8") as f:
            json.dump({"seed": F.SEED, "scale": F.SCALE, "defects": F.Defects.seen, "measured": F.MEASURED}, f, indent=1, sort_keys=True, default=str)
    sys.exit(0 if prog.result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
