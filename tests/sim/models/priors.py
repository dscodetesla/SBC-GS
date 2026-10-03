"""Prior distributions and the parameter space of the stochastic scenario engine (stdlib only, deterministic).

params.degrade.json uses the same leaf schema as params.json (value / unit / provenance / source / note) and adds
  "dist": {"kind": "point|uniform|normal|lognormal|beta|triangular", ...}   the PRIOR of the parameter
Provenance: SRC REPO INF SYNTH UNMEASURED MEASURED_SIM MEASURED_HW.
  SYNTH = synthesized realistic placeholder (planning number, NEVER a fact); it still needs a calibration entry.
Dist specs:
  point        {}                              the leaf "value"
  uniform      {"lo", "hi"}
  normal       {"mu", "sigma"[, "lo", "hi"]}   optional clipping
  lognormal    {"median", "sigma"[, "lo", "hi"]}   sigma = std of ln(x)
  beta         {"a", "b"[, "lo", "hi"]}        scaled to [lo, hi] (default 0..1)
  triangular   {"lo", "mode", "hi"}            used for base params with min/typ/max (params.json)
Overrides (no code change), later wins:
  DEGRADE_PARAMS=<file>      replace params.degrade.json
  DEGRADE_MEASURED=<file>    {"hw.pa_p1db_out_dbm": 24.5} or {"key": {"value": 24.5, "dist": {...}, "source": "..."}}  -> MEASURED_HW
  DEGRADE_SET='k=v;k=v'      quick override, JSON value -> point mass, provenance OVERRIDE
  MODEL_PARAMS / MODEL_MEASURED / MODEL_SET still act on the BASE params.json (common.py).
"""
import copy
import json
import math
import os
import random
import statistics

import common

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DEGRADE = os.path.join(HERE, "params.degrade.json")
PROVENANCE = ("SRC", "REPO", "INF", "SYNTH", "UNMEASURED", "MEASURED_SIM", "MEASURED_HW", "OVERRIDE")
NEEDS_SOURCE = ("SRC", "REPO", "MEASURED_SIM", "MEASURED_HW")
KINDS = ("point", "uniform", "normal", "lognormal", "beta", "triangular")
_ND = statistics.NormalDist()
_EPS = 1e-9


def phi(z):
    return _ND.cdf(z)


def phi_inv(u):
    return _ND.inv_cdf(min(max(u, _EPS), 1.0 - _EPS))


# ---------------------------------------------------------------- incomplete beta (for the Beta quantile)
def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < 3e-14:
            break
    return h


def betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_ppf_exact(a, b, u):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < u:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


_BETA_TABLES = {}
_ZNODES = [-4.0 + 0.2 * i for i in range(41)]


def beta_ppf(a, b, u):
    """Beta quantile via a table in z = Phi^-1(u) (41 nodes, linear interpolation; built lazily per (a, b))."""
    tab = _BETA_TABLES.get((a, b))
    if tab is None:
        tab = [beta_ppf_exact(a, b, phi(z)) for z in _ZNODES]
        _BETA_TABLES[(a, b)] = tab
    z = min(max(phi_inv(u), -3.999999), 3.999999)
    f = (z + 4.0) / 0.2
    i = int(f)
    return tab[i] + (tab[i + 1] - tab[i]) * (f - i)


# ---------------------------------------------------------------- distributions
class Dist:
    def __init__(self, spec, value=None):
        self.spec = dict(spec or {"kind": "point"})
        self.kind = self.spec.get("kind", "point")
        self.value = value
        if self.kind not in KINDS:
            raise common.ParamError("unknown dist kind %r" % self.kind)
        s = self.spec
        if self.kind == "uniform" and not s["lo"] < s["hi"]:
            raise common.ParamError("uniform needs lo < hi")
        if self.kind in ("normal", "lognormal") and not s["sigma"] > 0:
            raise common.ParamError("%s needs sigma > 0" % self.kind)
        if self.kind == "lognormal" and not s["median"] > 0:
            raise common.ParamError("lognormal needs median > 0")
        if self.kind == "beta" and not (s["a"] > 0 and s["b"] > 0):
            raise common.ParamError("beta needs a, b > 0")
        if self.kind == "triangular" and not s["lo"] <= s["mode"] <= s["hi"]:
            raise common.ParamError("triangular needs lo <= mode <= hi")

    @property
    def varies(self):
        return self.kind != "point"

    def ppf(self, u):
        s, k = self.spec, self.kind
        if k == "point":
            return self.value
        if k == "uniform":
            x = s["lo"] + (s["hi"] - s["lo"]) * u
        elif k == "normal":
            x = s["mu"] + s["sigma"] * phi_inv(u)
        elif k == "lognormal":
            x = s["median"] * math.exp(s["sigma"] * phi_inv(u))
        elif k == "beta":
            lo, hi = s.get("lo", 0.0), s.get("hi", 1.0)
            x = lo + (hi - lo) * beta_ppf(s["a"], s["b"], u)
        else:  # triangular
            lo, m, hi = s["lo"], s["mode"], s["hi"]
            if hi == lo:
                return lo
            fc = (m - lo) / (hi - lo)
            x = lo + math.sqrt(u * (hi - lo) * (m - lo)) if u < fc else hi - math.sqrt((1 - u) * (hi - lo) * (hi - m))
        if "lo" in s and k in ("normal", "lognormal"):
            x = max(x, s["lo"])
        if "hi" in s and k in ("normal", "lognormal"):
            x = min(x, s["hi"])
        return x

    def mean(self):
        s, k = self.spec, self.kind
        if k == "point":
            return self.value
        if k == "uniform":
            return 0.5 * (s["lo"] + s["hi"])
        if k == "normal":
            return s["mu"]
        if k == "lognormal":
            return s["median"] * math.exp(0.5 * s["sigma"] ** 2)
        if k == "beta":
            lo, hi = s.get("lo", 0.0), s.get("hi", 1.0)
            return lo + (hi - lo) * s["a"] / (s["a"] + s["b"])
        return (s["lo"] + s["mode"] + s["hi"]) / 3.0

    def median(self):
        return self.ppf(0.5)


# ---------------------------------------------------------------- degrade parameter file
class DParams(common.Params):
    """common.Params + a prior per leaf. A measured override turns the leaf into a point mass (or the given dist)."""

    def _override(self, key, val, prov):
        spec = val.get("dist") if isinstance(val, dict) else None
        super()._override(key, val, prov)
        leaf = self.leaves[key]
        if spec:
            leaf["dist"] = spec
            if not (isinstance(val, dict) and "value" in val):
                leaf["value"] = Dist(spec, leaf["value"]).mean()
        else:
            leaf["dist"] = {"kind": "point"}

    def dist(self, key):
        leaf = self.leaves[key]
        return Dist(leaf.get("dist") or {"kind": "point"}, leaf["value"])


def load_degrade_doc(path=None):
    path = path or os.environ.get("DEGRADE_PARAMS") or DEFAULT_DEGRADE
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_degrade(sets=None, params_path=None):
    doc = load_degrade_doc(params_path)
    measured = {}
    mp = os.environ.get("DEGRADE_MEASURED")
    if mp:
        with open(mp, encoding="utf-8") as f:
            measured = json.load(f)
    s = {}
    env_set = os.environ.get("DEGRADE_SET", "")
    if env_set:
        s.update(common.parse_set([x for x in env_set.split(";") if x.strip()]))
    if isinstance(sets, dict):
        s.update(sets)
    elif sets:
        s.update(common.parse_set(sets))
    return DParams(doc, s, measured)


def validate_degrade(doc, base_keys=None):
    """Schema check of params.degrade.json; returns a list of error strings (empty = valid)."""
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
        for key, leaf in common.iter_leaves(body.get("params", {}), sec + "."):
            keys[key] = leaf
    for key, leaf in keys.items():
        for need in ("value", "unit", "provenance", "dist"):
            if need not in leaf:
                err.append("%s: missing %s" % (key, need))
        prov = leaf.get("provenance")
        if prov not in PROVENANCE or prov == "OVERRIDE":
            err.append("%s: bad provenance %r" % (key, prov))
        if prov in NEEDS_SOURCE and not leaf.get("source"):
            err.append("%s: provenance %s requires a source" % (key, prov))
        if prov == "SRC" and not str(leaf.get("source", "")).startswith("https://"):
            err.append("%s: SRC source must be an https URL that was read" % key)
        if prov in ("SYNTH", "UNMEASURED") and not leaf.get("note"):
            err.append("%s: %s needs a note (rationale)" % (key, prov))
        if "dist" in leaf:
            try:
                d = Dist(leaf["dist"], leaf.get("value"))
                if isinstance(leaf.get("value"), (int, float)) and not isinstance(leaf.get("value"), bool) and d.varies:
                    a, b = d.ppf(0.001), d.ppf(0.999)
                    if not (a <= b):
                        err.append("%s: dist quantiles not ordered" % key)
            except (common.ParamError, KeyError) as e:
                err.append("%s: bad dist (%s)" % (key, e))
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
        if leaf.get("provenance") in ("UNMEASURED", "SYNTH") and key not in covered:
            err.append("%s: %s but no calibration entry fixes it" % (key, leaf.get("provenance")))
    for bk in doc.get("base_sampled", []):
        if base_keys is not None and bk not in base_keys:
            err.append("base_sampled: unknown base parameter %s" % bk)
    return err


def count_prov(doc, prov):
    n = 0
    for body in doc["sections"].values():
        for _k, leaf in common.iter_leaves(body["params"]):
            if leaf["provenance"] == prov:
                n += 1
    return n


# ---------------------------------------------------------------- deterministic random source
class Rng:
    """random.Random wrapper: every draw goes through u() so an antithetic twin is just 1 - u."""

    def __init__(self, seed, anti=False):
        self.seed = seed
        self.r = random.Random(seed)
        self.anti = anti

    def child(self, tag):
        """Independent sub-stream: each process owns one, so changing a parameter of one process does not shift the others
        (common random numbers for the sensitivity analysis)."""
        return Rng(self.seed * 7919 + tag, self.anti)

    def u(self):
        x = self.r.random()
        return 1.0 - x if self.anti else x

    def z(self):
        return phi_inv(self.u())

    def expo(self, mean):
        return -mean * math.log(1.0 - min(self.u(), 1.0 - _EPS))

    def lognorm(self, median, sigma):
        return median * math.exp(sigma * self.z())

    def poisson_step(self, rate_per_s, dt):
        """Bernoulli approximation of a Poisson process step (exact for at most one event per step). Not used by the engine any more
        (D6: its shock, burst and stall processes are continuous-time timelines independent of dt); kept for the public API."""
        return self.u() < -math.expm1(-rate_per_s * dt)


def subseed(seed, i, stream):
    return (seed * 1000003 + i) * 8 + stream


# ---------------------------------------------------------------- theta: a sampled parameter vector with the Params interface
class Theta:
    """Quacks like common.Params for rf_model / power_model / latency_budget (get, rng, prov): the sampled values."""

    def __init__(self, vals, provs=None):
        self.vals = vals
        self._provs = provs or {}

    def get(self, key):
        try:
            return self.vals[key]
        except KeyError:
            raise common.ParamError("unknown parameter: " + key)

    def rng(self, key):
        v = self.get(key)
        return v, v, v

    def prov(self, key):
        return self._provs.get(key, "SAMPLED")


class Space:
    """Base params (params.json) + degrade priors (params.degrade.json) -> sampleable dimensions.

    dims: list of (key, Dist, provenance). Base params listed in "base_sampled" with min < max become triangular(min, typ, max).
    pinned: keys forced to a value for this scenario (scenario "set"); they stop being dimensions.
    """

    def __init__(self, base, deg, pinned=None, dist_overrides=None):
        self.base, self.deg = base, deg
        self.defaults = {k: l["value"] for k, l in base.leaves.items()}
        self.defaults.update({k: l["value"] for k, l in deg.leaves.items()})
        self.provs = {k: l["provenance"] for k, l in base.leaves.items()}
        self.provs.update({k: l["provenance"] for k, l in deg.leaves.items()})
        pinned = dict(pinned or {})
        dist_overrides = dict(dist_overrides or {})
        base_dist = dict(deg.doc.get("base_dist", {}))  # contour-specific priors of base params (params.degrade.json "base_dist")
        for k, v in base_dist.items():
            dist_overrides.setdefault(k, v)
        for k, v in pinned.items():
            if k not in self.defaults:
                raise common.ParamError("scenario sets unknown parameter: " + k)
            self.defaults[k] = v
        self.pinned = set(pinned)
        self.dims = []
        for key in sorted(deg.leaves):
            if key in self.pinned:
                continue
            d = Dist(dist_overrides[key], deg.leaves[key]["value"]) if key in dist_overrides else deg.dist(key)
            if d.varies:
                self.dims.append((key, d, deg.leaves[key]["provenance"]))
        for key in deg.doc.get("base_sampled", []):
            if key in self.pinned or key not in base.leaves:
                continue
            lf = base.leaves[key]
            lo, v, hi = lf.get("min", lf["value"]), lf["value"], lf.get("max", lf["value"])
            if key in dist_overrides:
                d = Dist(dist_overrides[key], v)
            elif lo < hi and base.prov(key) != "MEASURED_HW":
                d = Dist({"kind": "triangular", "lo": lo, "mode": v, "hi": hi}, v)
            else:
                continue
            if d.varies:
                self.dims.append((key, d, lf["provenance"]))
        self.keys = [k for k, _d, _p in self.dims]
        self.dist_of = {k: d for k, d, _p in self.dims}
        self.prov_of = {k: p for k, _d, p in self.dims}

    def restrict(self, prefixes):
        """Keep only the dimensions whose key starts with one of the prefixes (e.g. ("gpio.",) for the button engine)."""
        self.dims = [d for d in self.dims if d[0].startswith(tuple(prefixes))]
        self.keys = [k for k, _d, _p in self.dims]
        self.dist_of = {k: d for k, d, _p in self.dims}
        self.prov_of = {k: p for k, _d, p in self.dims}
        return self

    def theta(self, us):
        """us: list of uniforms (one per dim, in self.dims order) -> Theta."""
        v = dict(self.defaults)
        for (key, d, _p), u in zip(self.dims, us):
            v[key] = d.ppf(u)
        return Theta(v, self.provs)

    def median_theta(self):
        return self.theta([0.5] * len(self.dims))

    def sample_us(self, rng):
        return [rng.u() for _ in self.dims]

    def prov_counts(self):
        c = {}
        for _k, _d, p in self.dims:
            c[p] = c.get(p, 0) + 1
        return c
