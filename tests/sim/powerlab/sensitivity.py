#!/usr/bin/env python3
"""Which bench-day measurement channel narrows the power/degradation model most? (one-at-a-time swing through the scenario engine)

  sensitivity.py [--n 200] [--seed 1] [--jobs 4] [--scenarios a,b,c] [--json OUT.json] [--quick]

For every power-related parameter (grouped by the instrument channel that measures it) the engine is run with the parameter pinned at
the 5 % and at the 95 % point of ITS PRIOR (the same distribution the engine samples; parameters the engine does not sample get the
INF range written below) with common random numbers (same seed, antithetic pairs). The swing of each output between the two runs is the
parameter's contribution; shares are normalised per output and scenario and averaged. A second pair of runs pins the parameter at its
prior median +- the INSTRUMENT ERROR (assumption, SYNTH) to estimate how much of the swing the measurement removes.

A MODEL-conditional study (SYNTH/UNMEASURED priors): it ranks where measuring pays off in the model, it does not say what real hardware
does. Interactions are ignored (one at a time). Swings below the Monte-Carlo noise floor (seed 1 vs seed 2 at the prior medians) are flagged.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "..", "models")
sys.path.insert(0, MODELS)
import common  # noqa: E402
import priors  # noqa: E402

LO_Q, HI_Q = 0.05, 0.95
ENGINE = os.path.join(MODELS, "scenario_engine.py")
SCENARIOS = ("nominal_pi5_5a_150m", "pi5_3a_weak_psu", "hot_day_closed_case")
OUTPUTS = ("availability", "residual", "usb_trip", "usb_latched", "usb_dropout", "undervoltage", "soc_throttle")

# channel -> [(key, instrument relative error (SYNTH assumption), INF range (lo, hi) for keys the engine does not sample, or None)]
CHANNELS = {
    "V5: EXT5V_V (pmic_read_adc) + inline PSU-side current": [
        ("power.cable_resistance_ohm", 0.10, None), ("power.psu_nominal_v", 0.01, (4.9, 5.25))],
    "Vdongle: voltage at the dongle end (inline meter V)": [("usb.cable_r_ohm", 0.20, None)],
    "Iusb: dongle current idle/RX/TX (inline meter, separate 5 V)": [
        ("power.devices.rtl8812_tx_a", 0.03, None), ("power.devices.rtl8812_rx_a", 0.03, None),
        ("power.devices.rtl8812_idle_a", 0.03, None), ("power.devices.fc_usb_a", 0.05, None), ("power.devices.fan_a", 0.05, None)],
    "Ipeak: TX pulse factor (scope, not a USB meter)": [("power.tx_peak_factor", 0.10, None)],
    "Iboard: board current at the PSU (inline meter)": [
        ("power.boards.pi5.board_load_a", 0.03, None), ("power.boards.pi5.board_idle_a", 0.03, (0.6, 1.0))],
    "T: SoC temperature (measure_temp) + get_throttled bit 3": [("hw.soc_rise_c", 0.07, None), ("hw.soc_soft_limit_c", 0.01, None)],
    "events: dmesg + EXT5V_V brackets (bench PSU ramp)": [
        ("power.usb_dropout_v", 0.01, None), ("power.undervolt_threshold_v", 0.005, (4.40, 4.86)),
        ("power.usb_reenum_s", 0.05, None), ("usb.drop_rate_per_h", 0.5, None)],
    "limiter: Pi 5 current ramp with an electronic load (HW only)": [
        ("usb.pi5_trip_tol", 0.3, None), ("usb.pi5_trip_off_s", 0.3, None), ("usb.pi5_trip_tx_weight", 0.3, None)],
}


def prior_points(sp, base, deg, key, fallback):
    """(lo, median, hi) of the prior the engine samples, or the fallback range with the leaf value as the median"""
    if key in sp.dist_of:
        d = sp.dist_of[key]
        return d.ppf(LO_Q), d.ppf(0.5), d.ppf(HI_Q), True
    leaf = (base.leaves.get(key) or deg.leaves.get(key))
    if leaf is None:
        return None
    lo, hi = fallback if fallback else (leaf.get("min", leaf["value"]), leaf.get("max", leaf["value"]))
    return lo, leaf["value"], hi, False


def parse_engine(text):
    out = {}
    for line in text.splitlines():
        c = line.split(",")
        if c[0] in ("availability", "residual") and len(c) >= 7:
            out[c[0]] = float(c[6])  # mean
        elif c[0] in OUTPUTS and len(c) == 2:
            out[c[0]] = float(c[1])
    return out


def run_engine(scn, sets, n, seed):
    cmd = [sys.executable, ENGINE, "run", scn, "--n", str(n), "--seed", str(seed), "--antithetic"]
    for k, v in sets.items():
        cmd += ["--set", "%s=%r" % (k, v)]
    p = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "scenario_engine failed")
    return parse_engine(p.stdout)


def shares(swings):
    """swings {(param, scenario): {output: abs swing}} -> {param: mean over (scenario, output with any swing) of the share in %}"""
    params = sorted({p for p, _s in swings})
    scen = sorted({s for _p, s in swings})
    acc = dict.fromkeys(params, 0.0)
    cells = 0
    for s in scen:
        for o in OUTPUTS:
            tot = sum(swings[(p, s)].get(o, 0.0) for p in params if (p, s) in swings)
            if tot <= 0:
                continue
            cells += 1
            for p in params:
                acc[p] += 100.0 * swings.get((p, s), {}).get(o, 0.0) / tot
    return {p: (a / cells if cells else 0.0) for p, a in acc.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--json")
    ap.add_argument("--quick", action="store_true", help="three parameters, one scenario, n=40 (test mode)")
    a = ap.parse_args(argv)
    scen = a.scenarios.split(",")
    base, deg = common.load(), priors.load_degrade()
    sp = priors.Space(base, deg)
    items = [(ch, k, e, fb) for ch, lst in CHANNELS.items() for k, e, fb in lst]
    if a.quick:
        items = [x for x in items if x[1] in ("power.devices.rtl8812_tx_a", "power.cable_resistance_ohm", "usb.cable_r_ohm")]
        scen, a.n = scen[:1], 40
    pts = {}
    for ch, k, e, fb in items:
        p = prior_points(sp, base, deg, k, fb)
        if p is None:
            print("sensitivity: unknown key %s (model changed?)" % k, file=sys.stderr)
            return 2
        pts[k] = p
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        def submit(tag, s, sets, seed):
            jobs[(tag, s)] = ex.submit(run_engine, s, sets, a.n, seed)
        for s in scen:
            submit(("base", 1), s, {}, a.seed)
            submit(("base", 2), s, {}, a.seed + 1)
            for ch, k, e, fb in items:
                lo, med, hi, _sampled = pts[k]
                submit((k, "lo"), s, {k: lo}, a.seed)
                submit((k, "hi"), s, {k: hi}, a.seed)
                submit((k, "mlo"), s, {k: med * (1 - e)}, a.seed)
                submit((k, "mhi"), s, {k: med * (1 + e)}, a.seed)
        res = {k: f.result() for k, f in jobs.items()}
    floor = {s: {o: abs(res[(("base", 1), s)].get(o, 0.0) - res[(("base", 2), s)].get(o, 0.0)) for o in OUTPUTS} for s in scen}
    swing, after = {}, {}
    for s in scen:
        for ch, k, e, fb in items:
            swing[(k, s)] = {o: abs(res[((k, "hi"), s)].get(o, 0.0) - res[((k, "lo"), s)].get(o, 0.0)) for o in OUTPUTS}
            after[(k, s)] = {o: abs(res[((k, "mhi"), s)].get(o, 0.0) - res[((k, "mlo"), s)].get(o, 0.0)) for o in OUTPUTS}
    sh = shares(swing)
    # residual share after the measurement: shares of the "after" swings expressed against the same totals
    tot = {}
    for s in scen:
        for o in OUTPUTS:
            tot[(s, o)] = sum(swing[(k, s)][o] for _c, k, _e, _f in items)
    resid = {}
    for _c, k, _e, _f in items:
        v, cells = 0.0, 0
        for s in scen:
            for o in OUTPUTS:
                if tot[(s, o)] > 0:
                    cells += 1
                    v += 100.0 * after[(k, s)][o] / tot[(s, o)]
        resid[k] = v / cells if cells else 0.0
    rows = []
    for ch, k, e, fb in items:
        lo, med, hi, sampled = pts[k]
        noisy = all(swing[(k, s)][o] <= floor[s][o] for s in scen for o in OUTPUTS if swing[(k, s)][o] > 0) if any(swing[(k, s)][o] > 0 for s in scen for o in OUTPUTS) else True
        rows.append({"channel": ch, "key": k, "lo": lo, "median": med, "hi": hi, "engine_samples_it": sampled, "instrument_rel_error": e, "share_pct": sh[k],
                     "share_after_pct": resid[k], "within_mc_noise": noisy})
    rows.sort(key=lambda r: -r["share_pct"])
    chan = {}
    for r in rows:
        c = chan.setdefault(r["channel"], {"share_pct": 0.0, "share_after_pct": 0.0})
        c["share_pct"] += r["share_pct"]
        c["share_after_pct"] += r["share_after_pct"]
    print("# power-lab sensitivity (SYNTH model study): n=%d seed=%d scenarios=%s antithetic, one at a time" % (a.n, a.seed, ",".join(scen)))
    print("# swing = |output(95 % prior point) - output(5 % point)|; share = mean over scenarios and outputs of the swing share; after = share left when pinned at median +- instrument error")
    print("channel,share_pct,share_after_pct,reduction_pct")
    for c, v in sorted(chan.items(), key=lambda kv: -kv[1]["share_pct"]):
        print("%s,%.1f,%.1f,%.0f" % (c.replace(",", ";"), v["share_pct"], v["share_after_pct"], 100.0 * (1 - v["share_after_pct"] / v["share_pct"]) if v["share_pct"] else 0.0))
    print("parameter,channel,lo,median,hi,engine_samples_it,instrument_rel_error,share_pct,share_after_pct,within_mc_noise")
    for r in rows:
        print("%s,%s,%.4g,%.4g,%.4g,%s,%.3g,%.1f,%.1f,%s" % (r["key"], r["channel"].split(":")[0], r["lo"], r["median"], r["hi"], r["engine_samples_it"], r["instrument_rel_error"],
                                                              r["share_pct"], r["share_after_pct"], r["within_mc_noise"]))
    print("# mc noise floor (seed %d vs %d at the medians): %s" % (a.seed, a.seed + 1, "; ".join("%s: %s" % (s, ",".join("%s=%.3g" % (o, floor[s][o]) for o in OUTPUTS)) for s in scen)))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"n": a.n, "seed": a.seed, "scenarios": scen, "rows": rows, "channels": chan, "noise_floor": floor}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
