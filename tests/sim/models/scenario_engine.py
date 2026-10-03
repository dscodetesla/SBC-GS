#!/usr/bin/env python3
"""Deterministic Monte-Carlo scenario engine over the calibratable models (stdlib only, seeded random.Random).

A MODEL, not a proof. Every draw samples all parameter priors (params.degrade.json + the min/typ/max of selected params.json
entries as triangular priors), runs ONE time-stepped session (degrade_model.run_session) or one button campaign (gpio_bounce) and
reports percentiles over draws. The priors are SYNTH/UNMEASURED planning numbers: results rank risks and design tests, they do
not predict a real flight (docs/SIM-SCENARIOS.md, honesty section).

  scenario_engine.py list
  scenario_engine.py run <scenario|file.json> [--n 200] [--seed 1] [--antithetic] [--set key=val ...] [--dist key='{"kind":...}']
  scenario_engine.py sensitivity <scenario> [--r 6] [--seed 1]              Morris elementary effects (mu*, sigma) per output
  scenario_engine.py bounce [--kind long|single] [--seed 1] [--scenario button_bouncy_switch]   JSON bounce trace
  scenario_engine.py events <scenario> [--seed 1]                            one session as sbc-gs-degrade-events/1 JSON
  scenario_engine.py catalog-check                                           validate scenarios/catalog.json
  scenario_engine.py doc-tables                                              markdown tables for docs/SIM-SCENARIOS.md

Overrides without code changes: DEGRADE_PARAMS / DEGRADE_MEASURED / DEGRADE_SET (priors.py), MODEL_PARAMS / MODEL_MEASURED /
MODEL_SET (common.py, base params), `--set` (pins a parameter to a value), `--dist` (replaces a prior), scenario files.
Scenario file: {"name", "description", "kind": "link"|"button", "cfg": {...}, "set": {key: value}, "dist": {key: dist spec}}.
Antithetic: draw 2j+1 reuses the uniforms of draw 2j as 1-u (parameters AND process noise); n should be even.
"""
import argparse
import glob
import json
import math
import os
import sys

import common
import degrade_model as dm
import gpio_bounce
import priors
from priors import Rng, subseed

HERE = os.path.dirname(os.path.abspath(__file__))
SCEN_DIR = os.path.join(HERE, "scenarios")
DEFAULT_SET = {"rf.loss_model": "ge", "rf.fading_model": "rician"}
LINK_OUTPUTS = (("residual", "frac"), ("margin_db", "dB"), ("range_m", "m"), ("g2g_ms", "ms"), ("availability", "frac"), ("ttff_s", "s"))
BUTTON_OUTPUTS = (("false_event", "frac"), ("false_event_single", "frac"), ("false_event_long", "frac"), ("single_extra", "frac"),
                  ("long_as_single", "frac"), ("long_missed", "frac"))
BUTTON_MODES = ("button_false_event",)
QS = (5, 50, 95, 99)
SENS_LINK = ("residual", "margin_db", "range_m", "g2g_ms", "availability", "ttff_s")


def list_scenarios():
    out = {}
    for p in sorted(glob.glob(os.path.join(SCEN_DIR, "*.json"))):
        if os.path.basename(p) == "catalog.json":
            continue
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        out[d["name"]] = d
    return out


def load_scenario(name_or_path):
    if os.path.isfile(name_or_path):
        with open(name_or_path, encoding="utf-8") as f:
            return json.load(f)
    sc = list_scenarios()
    if name_or_path not in sc:
        raise common.ParamError("unknown scenario %r (have: %s)" % (name_or_path, ", ".join(sorted(sc))))
    return sc[name_or_path]


class Engine:
    def __init__(self, scenario, sets=None, dists=None, base=None, deg=None, cfg_over=None):
        self.sc = scenario
        self.kind = scenario.get("kind", "link")
        self.cfg = dm.make_cfg(scenario.get("cfg"))
        self.cfg.update(cfg_over or {})
        self.base = base or common.load()
        self.deg = deg or priors.load_degrade()
        pinned = dict(DEFAULT_SET)
        pinned.update(scenario.get("set", {}))
        pinned.update(sets or {})
        dd = dict(scenario.get("dist", {}))
        dd.update(dists or {})
        self.space = priors.Space(self.base, self.deg, pinned, dd)
        if self.kind == "button":
            self.space.restrict(("gpio.",))
        self.outputs = BUTTON_OUTPUTS if self.kind == "button" else LINK_OUTPUTS
        self.modes = BUTTON_MODES if self.kind == "button" else dm.FAIL_MODES

    def evaluate(self, theta, rng):
        if self.kind == "button":
            p = gpio_bounce.button_stats(theta, rng, int(self.cfg.get("presses_each", 30)))
            p["flags"] = {"button_false_event": p["false_event"] > 0.05}
            return p
        return dm.run_session(theta, self.cfg, rng)

    def draw(self, i, seed, anti):
        j = i // 2 if anti else i
        odd = anti and (i % 2 == 1)
        ru, rp = Rng(subseed(seed, j, 0), odd), Rng(subseed(seed, j, 1), odd)
        return self.space.theta(self.space.sample_us(ru)), rp

    def run(self, n, seed, anti=False):
        res = []
        for i in range(n):
            th, rp = self.draw(i, seed, anti)
            res.append(self.evaluate(th, rp))
        return res


def summarize(eng, res):
    out = {"outputs": {}, "modes": {}}
    for key, unit in eng.outputs:
        xs = [r[key] for r in res]
        out["outputs"][key] = {"unit": unit, "p": {q: dm.pctl(xs, q) for q in QS}, "mean": sum(xs) / len(xs)}
    for m in eng.modes:
        out["modes"][m] = sum(1 for r in res if r["flags"][m]) / len(res)
    if eng.kind == "link":
        out["no_failure"] = sum(1 for r in res if r["ttff_censored"]) / len(res)
        out["bringup_s_p50"] = dm.pctl([r["bringup_s"] for r in res], 50)
    return out


def fmt_report(eng, n, seed, anti, summ):
    sp = eng.space
    c = sp.prov_counts()
    cfg = eng.cfg
    head = ["# scenario_engine: %s kind=%s n=%d seed=%d antithetic=%d" % (eng.sc["name"], eng.kind, n, seed, int(anti))]
    if eng.kind == "link":
        head.append("# session: distance=%.0fm duration=%.0fs dt=%.0fs board=%s psu=%.1fA usb_max_current=%d codec=%s spec: residual<=%.3g g2g<=%.0fms freeze<=%.2g"
                    % (cfg["distance_m"], cfg["duration_s"], cfg["dt_s"], cfg["board"], cfg["psu_a"], int(cfg["usb_max_current"]), cfg["codec"],
                       cfg["spec"]["residual"], cfg["spec"]["g2g_ms"], cfg["spec"]["freeze"]))
    head.append("# priors: %d sampled dims (%s); pinned: %s" % (len(sp.dims), " ".join("%s=%d" % (k, c[k]) for k in sorted(c)),
                                                                 ",".join(sorted(sp.pinned)) or "-"))
    out = head + ["output,unit,p5,p50,p95,p99,mean"]
    for key, unit in eng.outputs:
        o = summ["outputs"][key]
        out.append("%s,%s,%s" % (key, unit, ",".join("%.4g" % o["p"][q] for q in QS) + ",%.4g" % o["mean"]))
    if eng.kind == "link":
        out.append("P(no failure in session)=%.3f  bring-up p50=%.1fs" % (summ["no_failure"], summ["bringup_s_p50"]))
    out.append("failure_mode,probability")
    for m in eng.modes:
        out.append("%s,%.3f" % (m, summ["modes"][m]))
    out.append("# SYNTH/UNMEASURED priors: planning numbers, NOT predictions; low-is-bad outputs (margin, range, availability) read p5, high-is-bad read p95/p99")
    return out


# ---------------------------------------------------------------- Morris elementary effects
def morris(eng, r, seed, outputs):
    """Morris screening in quantile space. Returns {output: [(mu_star, sigma, key, prov)] sorted desc}, noise {output: std}."""
    sp = eng.space
    k = len(sp.dims)
    lev = [0.02 + 0.96 * x for x in (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)]
    ee = {o: [[] for _ in range(k)] for o in outputs}
    rr = Rng(subseed(seed, 0, 5))
    for traj in range(r):
        x = [int(rr.u() * 4) % 4 for _ in range(k)]  # level index 0..3
        sd = subseed(seed, traj, 6)
        order = sorted(range(k), key=lambda _i: rr.u())

        def f(xs):
            return eng.evaluate(sp.theta([lev[i] for i in xs]), Rng(sd))
        cur = f(x)
        for i in order:
            up_ok, dn_ok = x[i] + 2 <= 3, x[i] - 2 >= 0
            if up_ok and (not dn_ok or rr.u() < 0.5):
                nx = x[i] + 2
            elif dn_ok:
                nx = x[i] - 2
            else:
                nx = 3 if x[i] < 2 else 0
            x2 = list(x)
            x2[i] = nx
            new = f(x2)
            for o in outputs:
                ee[o][i].append((new[o] - cur[o]) / (lev[nx] - lev[x[i]]))
            x, cur = x2, new
    table = {}
    for o in outputs:
        rows = []
        for i, key in enumerate(sp.keys):
            e = ee[o][i]
            mu_star = sum(abs(v) for v in e) / len(e)
            m = sum(e) / len(e)
            sig = math.sqrt(sum((v - m) ** 2 for v in e) / max(len(e) - 1, 1))
            # snap platform-dependent floating-point noise (libm/rounding differ between machines): equal effects must tie
            # (then ordered by key) and a sigma that is only rounding error is exactly 0, so the golden files are portable
            mu_star = float("%.9g" % mu_star)
            sig = 0.0 if sig < 1e-9 * max(1.0, mu_star) else float("%.9g" % sig)
            rows.append((mu_star, sig, key, sp.prov_of[key]))
        rows.sort(key=lambda t: (-t[0], t[2]))
        table[o] = rows
    # stochastic noise at the median parameters: spread over process seeds
    med = sp.median_theta()
    outs = [eng.evaluate(med, Rng(subseed(seed, s, 7))) for s in range(24)]
    noise = {}
    for o in outputs:
        xs = [d[o] for d in outs]
        m = sum(xs) / len(xs)
        noise[o] = math.sqrt(sum((v - m) ** 2 for v in xs) / (len(xs) - 1))
    return table, noise


def prior_spread(eng, outputs, seed, n=24):
    """Std of each output over prior draws (to compare the process noise and mu* with)."""
    res = eng.run(n, seed + 11)
    spread = {}
    for o in outputs:
        xs = [d[o] for d in res]
        m = sum(xs) / len(xs)
        spread[o] = math.sqrt(sum((v - m) ** 2 for v in xs) / (len(xs) - 1))
    return spread


def fmt_sensitivity(eng, r, seed, top=8):
    outputs = [o for o, _u in eng.outputs] if eng.kind == "button" else list(SENS_LINK)
    table, noise = morris(eng, r, seed, outputs)
    out = ["# sensitivity (Morris elementary effects in prior-quantile space): scenario=%s kind=%s dims=%d r=%d seed=%d"
           % (eng.sc["name"], eng.kind, len(eng.space.dims), r, seed),
           "# mu* = mean |effect| of moving one prior across ~2/3 of its quantile range, in OUTPUT units; share = mu*/sum(mu*); sigma = nonlinearity/interactions",
           "# noise = std of the output over 24 process seeds at the median parameters (what chance alone does)"]
    measure_first = {}
    for o in outputs:
        rows = table[o]
        tot = sum(t[0] for t in rows) or 1.0
        out.append("")
        out.append("## output %s (noise std %.4g)" % (o, noise[o]))
        out.append("rank,parameter,provenance,mu_star,sigma,share_pct")
        for i, (mu, sg, key, prov) in enumerate(rows[:top]):
            out.append("%d,%s,%s,%.4g,%.4g,%.1f" % (i + 1, key, prov, mu, sg, 100.0 * mu / tot))
        for mu, _sg, key, prov in rows:
            if prov in ("UNMEASURED", "SYNTH"):
                measure_first[key] = measure_first.get(key, 0.0) + 100.0 * mu / tot
    out.append("")
    out.append("## measure first (UNMEASURED/SYNTH parameters ranked by summed share over %d outputs)" % len(outputs))
    out.append("rank,parameter,provenance,summed_share_pct")
    for i, (key, sh) in enumerate(sorted(measure_first.items(), key=lambda t: (-t[1], t[0]))[:12]):
        out.append("%d,%s,%s,%.1f" % (i + 1, key, eng.space.prov_of[key], sh))
    return out, table


# ---------------------------------------------------------------- catalog
CAT_FIELDS = ("id", "title", "category", "contour", "prior_prob", "trigger", "symptom", "detection", "mitigation", "layers",
              "detection_test", "severity", "engine_mode", "false_confidence")
LAYERS = ("models", "smoke.static", "smoke.model", "smoke.mavlink", "smoke.video", "smoke.router", "wfb_veth", "qemu_hwsim",
          "virt.gpio", "virt.usb", "virt.radio", "dkms", "tests.golden", "HW")


def load_catalog():
    with open(os.path.join(SCEN_DIR, "catalog.json"), encoding="utf-8") as f:
        return json.load(f)


def validate_catalog(cat):
    err = []
    ids = set()
    modes = set(dm.FAIL_MODES) | set(BUTTON_MODES)
    for s in cat.get("scenarios", []):
        sid = s.get("id", "?")
        if sid in ids:
            err.append("%s: duplicate id" % sid)
        ids.add(sid)
        for f in CAT_FIELDS:
            if f not in s or s[f] in ("", None, []) and f not in ("engine_mode",):
                err.append("%s: missing %s" % (sid, f))
        pp = s.get("prior_prob", {})
        if pp.get("provenance") != "SYNTH":
            err.append("%s: prior_prob must be labelled SYNTH" % sid)
        v = pp.get("value")
        if not isinstance(v, (int, float)) or not 0.0 < v < 1.0:
            err.append("%s: prior_prob.value must be in (0,1)" % sid)
        if not pp.get("rationale"):
            err.append("%s: prior_prob needs a rationale" % sid)
        for ly in s.get("layers", []):
            if ly not in LAYERS:
                err.append("%s: unknown layer %s" % (sid, ly))
        if s.get("engine_mode") is not None and s["engine_mode"] not in modes:
            err.append("%s: engine_mode %s is not an engine failure mode" % (sid, s["engine_mode"]))
        if not isinstance(s.get("severity"), int) or not 1 <= s["severity"] <= 5:
            err.append("%s: severity must be 1..5" % sid)
        for fc in s.get("false_confidence", []):
            if not (fc.startswith("F") and fc[1:].isdigit() and 1 <= int(fc[1:]) <= 19):
                err.append("%s: bad false_confidence %s" % (sid, fc))
    return err


# ---------------------------------------------------------------- CLI
def doc_tables():
    deg = priors.load_degrade()
    out = ["### Таблиця параметрів `params.degrade.json` (згенеровано `scenario_engine.py doc-tables`; істина в JSON)", "",
           "| Ключ | Одиниця | Розподіл (prior) | Походження | Примітка |", "|---|---|---|---|---|"]
    for key in sorted(deg.leaves):
        lf = deg.leaves[key]
        d = lf.get("dist", {})
        ds = ", ".join("%s=%s" % (a, b) for a, b in d.items() if a != "kind")
        out.append("| `%s` | %s | %s(%s) | %s | %s |" % (key, lf["unit"], d.get("kind", "point"), ds or lf["value"], lf["provenance"],
                                                         lf.get("note", lf.get("source", "")).replace("|", "/")))
    out += ["", "### Каталог позанормальних сценаріїв (`scenarios/catalog.json`)", "",
            "| ID | Сценарій | Контур | p (SYNTH, /1 год) | Тяжкість | Режим рушія | Шари | F |", "|---|---|---|---|---|---|---|---|"]
    for s in load_catalog()["scenarios"]:
        out.append("| %s | %s | %s | %.2f | %d | %s | %s | %s |" % (s["id"], s["title"], s["contour"], s["prior_prob"]["value"], s["severity"],
                                                                 s["engine_mode"] or "-", ", ".join(s["layers"]), ", ".join(s["false_confidence"])))
    out += ["", "### Каталог: тригер, симптом, виявлення, пом'якшення, тест виявлення", "",
            "| ID | Тригер | Симптом | Виявлення | Пом'якшення / fallback | Ідея тесту виявлення (шар) |", "|---|---|---|---|---|---|"]
    for s in load_catalog()["scenarios"]:
        out.append("| %s | %s | %s | %s | %s | %s |" % (s["id"], s["trigger"], s["symptom"], s["detection"], s["mitigation"], s["detection_test"]))
    out += ["", "### Калібрування: який вимір понеділка замінює які пріори", "", "| Запис | Що міряти | Параметри | Команда | Інструмент | Як |", "|---|---|---|---|---|---|"]
    for sec, body in deg_doc_sections().items():
        for c in body["calibration"]:
            out.append("| %s | %s | %s | %s | %s | %s |" % (c["id"], c["measure"], ", ".join("`%s`" % p for p in c["params"]), c["command"].replace("|", "/"),
                                                        c["tool"], c["how"].replace("|", "/")))
    return out


def deg_doc_sections():
    return priors.load_degrade_doc()["sections"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("catalog-check")
    sub.add_parser("doc-tables")
    for name in ("run", "sensitivity", "bounce", "events"):
        sp = sub.add_parser(name)
        sp.add_argument("scenario", nargs="?", default="nominal_pi5_5a_150m" if name != "bounce" else "button_nominal")
        sp.add_argument("--seed", type=int, default=1)
        sp.add_argument("--set", action="append", default=[], metavar="KEY=VAL")
        sp.add_argument("--dist", action="append", default=[], metavar="KEY=JSON")
        if name in ("run", "sensitivity", "events"):
            sp.add_argument("--duration", type=float, help="override session length, s")
            sp.add_argument("--dt", type=float, help="override slice length, s")
        if name == "run":
            sp.add_argument("--n", type=int, default=200)
            sp.add_argument("--antithetic", action="store_true")
        if name == "sensitivity":
            sp.add_argument("--r", type=int, default=6)
        if name == "bounce":
            sp.add_argument("--kind", choices=("single", "long"), default="long")
            sp.add_argument("--hold-s", type=float)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            for n, d in list_scenarios().items():
                print("%s\t%s\t%s" % (n, d.get("kind", "link"), d.get("description", "")))
            return 0
        if a.cmd == "doc-tables":
            print("\n".join(doc_tables()))
            return 0
        if a.cmd == "catalog-check":
            err = validate_catalog(load_catalog())
            print("\n".join(err) if err else "catalog OK: %d scenarios" % len(load_catalog()["scenarios"]))
            return 1 if err else 0
        co = {}
        if getattr(a, "duration", None):
            co["duration_s"] = a.duration
        if getattr(a, "dt", None):
            co["dt_s"] = a.dt
        eng = Engine(load_scenario(a.scenario), common.parse_set(a.set), {k: json.loads(v) for k, v in (x.split("=", 1) for x in a.dist)}, cfg_over=co)
        if a.cmd == "run":
            res = eng.run(a.n, a.seed, a.antithetic)
            print("\n".join(fmt_report(eng, a.n, a.seed, a.antithetic, summarize(eng, res))))
        elif a.cmd == "sensitivity":
            print("\n".join(fmt_sensitivity(eng, a.r, a.seed)[0]))
        elif a.cmd == "bounce":
            th = eng.space.median_theta()
            print(json.dumps(gpio_bounce.trace_json(th, eng.sc["name"], a.seed, a.kind, a.hold_s), indent=1))
        elif a.cmd == "events":
            th, rp = eng.draw(0, a.seed, False)
            ev = []
            dm.run_session(th, eng.cfg, rp, ev)
            print(json.dumps({"schema": "sbc-gs-degrade-events/1", "scenario": eng.sc["name"], "seed": a.seed,
                              "duration_s": eng.cfg["duration_s"], "events": sorted(ev, key=lambda e: e["t_s"])}, indent=1))
    except (common.ParamError, OSError, ValueError, KeyError) as e:
        print("scenario_engine: error: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
