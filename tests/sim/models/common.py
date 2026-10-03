"""Shared parameter loader for the SBC-GS calibratable models (stdlib only).

All parameters live in params.json (sections -> params tree; a leaf is a dict with a "value" or "provenance" key, so a leaf without provenance is reported, not skipped).
A measured value overrides the model WITHOUT code changes, in this order (later wins):
  1. MODEL_PARAMS=<file>        replace the whole params file
  2. MODEL_MEASURED=<file>      overlay {"rf.tx_power_dbm": 21.5, "power.devices.rtl8812_tx_a": {"value": 0.8, "source": "meter 2026-10-05"}}
  3. MODEL_SET='k=v;k=v'        quick override (value parsed as JSON, else string)
  4. --set k=v (CLI, repeatable) handled by add_cli()/from_args()
Overlay values get provenance MEASURED_HW; MODEL_SET/--set values get OVERRIDE.
"""
import copy
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARAMS = os.path.join(HERE, "params.json")
PROVENANCE = ("SRC", "REPO", "INF", "UNMEASURED", "MEASURED_SIM", "MEASURED_HW")
NEEDS_SOURCE = ("SRC", "REPO", "MEASURED_SIM", "MEASURED_HW")


class ParamError(Exception):
    pass


def _jv(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def iter_leaves(node, prefix=""):
    """Yield (dotted_key, leaf_dict) for every parameter leaf under node."""
    for k, v in node.items():
        key = prefix + k
        if isinstance(v, dict) and ("provenance" in v or "value" in v):
            yield key, v
        elif isinstance(v, dict):
            yield from iter_leaves(v, key + ".")


class Params:
    def __init__(self, doc, sets=None, measured=None):
        self.doc = copy.deepcopy(doc)
        self.leaves = {}
        for sec, body in self.doc["sections"].items():
            for key, leaf in iter_leaves(body["params"], sec + "."):
                self.leaves[key] = leaf
        self.used = set()
        self.overridden = {}
        for key, val in (measured or {}).items():
            self._override(key, val, "MEASURED_HW")
        for key, val in (sets or {}).items():
            self._override(key, val, "OVERRIDE")

    def _override(self, key, val, prov):
        if key not in self.leaves:
            raise ParamError("unknown parameter: " + key)
        leaf = self.leaves[key]
        if isinstance(val, dict):
            leaf.update({k: v for k, v in val.items() if k in ("value", "min", "max", "source", "note")})
        else:
            leaf["value"] = val
        leaf["provenance"] = prov
        self.overridden[key] = prov

    def get(self, key):
        if key not in self.leaves:
            raise ParamError("unknown parameter: " + key)
        self.used.add(key)
        return self.leaves[key]["value"]

    def rng(self, key):
        """(min, typ, max); min/max default to the value."""
        v = self.get(key)
        leaf = self.leaves[key]
        return leaf.get("min", v), v, leaf.get("max", v)

    def prov(self, key):
        return self.leaves[key]["provenance"]

    def used_unmeasured(self):
        return sorted(k for k in self.used if self.prov(k) == "UNMEASURED")

    def footer(self):
        """One stable line listing the UNMEASURED parameters the result depends on."""
        u = self.used_unmeasured()
        ov = sorted(self.overridden)
        s = "# depends on %d UNMEASURED parameter(s): %s" % (len(u), ",".join(u) if u else "-")
        if ov:
            s += "\n# overridden: " + ",".join(ov)
        return s


def load_doc(path=None):
    path = path or os.environ.get("MODEL_PARAMS") or DEFAULT_PARAMS
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_set(items):
    out = {}
    for it in items:
        if "=" not in it:
            raise ParamError("bad --set (want key=value): " + it)
        k, v = it.split("=", 1)
        out[k.strip()] = _jv(v.strip())
    return out


def load(sets=None, params_path=None):
    """Params with env/CLI overrides applied. sets: list of 'k=v' strings (CLI) or dict."""
    doc = load_doc(params_path)
    measured = {}
    mp = os.environ.get("MODEL_MEASURED")
    if mp:
        with open(mp, encoding="utf-8") as f:
            measured = json.load(f)
    s = {}
    env_set = os.environ.get("MODEL_SET", "")
    if env_set:
        s.update(parse_set([x for x in env_set.split(";") if x.strip()]))
    if isinstance(sets, dict):
        s.update(sets)
    elif sets:
        s.update(parse_set(sets))
    return Params(doc, s, measured)


def add_cli(ap):
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                    help="override a parameter (repeatable), e.g. --set rf.tx_power_dbm=23")
    ap.add_argument("--params", default=None, help="alternative params.json")


def from_args(a):
    return load(a.set, a.params)


def validate(doc):
    """Schema check of a params document; returns a list of error strings (empty = valid)."""
    err = []
    if doc.get("schema") != 1:
        err.append("schema must be 1")
    secs = doc.get("sections")
    if not isinstance(secs, dict) or not secs:
        return err + ["no sections"]
    keys = {}
    for sec, body in secs.items():
        for need in ("description", "assumptions", "params", "calibration"):
            if need not in body:
                err.append("%s: missing %s" % (sec, need))
        if not body.get("assumptions") and sec != "video":
            err.append("%s: assumptions must be non-empty" % sec)
        for key, leaf in iter_leaves(body.get("params", {}), sec + "."):
            keys[key] = leaf
    for key, leaf in keys.items():
        for need in ("value", "unit", "provenance"):
            if need not in leaf:
                err.append("%s: missing %s" % (key, need))
        prov = leaf.get("provenance")
        if prov not in PROVENANCE:
            err.append("%s: bad provenance %r" % (key, prov))
        if prov in NEEDS_SOURCE and not leaf.get("source"):
            err.append("%s: provenance %s requires a source" % (key, prov))
        if prov == "SRC" and not str(leaf.get("source", "")).startswith("https://"):
            err.append("%s: SRC source must be an https URL that was read" % key)
        v = leaf.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "min" in leaf and leaf["min"] > v:
                err.append("%s: min > value" % key)
            if "max" in leaf and leaf["max"] < v:
                err.append("%s: max < value" % key)
    covered = set()
    for sec, body in secs.items():
        for c in body.get("calibration", []):
            for need in ("id", "measure", "params", "command", "tool", "how"):
                if not c.get(need):
                    err.append("%s calibration %s: missing %s" % (sec, c.get("id", "?"), need))
            for p in c.get("params", []):
                if p not in keys:
                    err.append("calibration %s: unknown parameter %s" % (c.get("id"), p))
                covered.add(p)
    for key, leaf in keys.items():
        if leaf.get("provenance") == "UNMEASURED" and key not in covered:
            err.append("%s: UNMEASURED but no calibration entry fixes it" % key)
    return err


def count_unmeasured(doc):
    n = 0
    for body in doc["sections"].values():
        for _k, leaf in iter_leaves(body["params"]):
            if leaf["provenance"] == "UNMEASURED":
                n += 1
    return n


def split_wh(s):
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    if not m:
        raise ParamError("want WxH, got " + s)
    return int(m.group(1)), int(m.group(2))
