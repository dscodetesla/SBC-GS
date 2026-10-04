#!/usr/bin/env python3
"""Bench-day bridge: sensitivity ranking -> measurement sheet -> measured overlay -> what-if.

  calib.py sheet [--top N]            ranked "measure first" list (Morris golden files), prior value, provenance
  calib.py template [--top N] FILE    write a measured-overlay template {key: null} to fill in on the bench
  calib.py whatif FILE [--scenario S] [--n N] [--seed K]
                                      run the scenario with and without the overlay; show how the outputs move

Overlay keys are the model keys (e.g. power.devices.rtl8812_tx_a, hw.ant_null_mean_db). Keys of params.json go
through MODEL_MEASURED, keys of params.degrade.json through DEGRADE_MEASURED; this tool splits them. A null value is
ignored (not yet measured). Everything printed from priors is SYNTH/UNMEASURED; a measured key becomes MEASURED_HW only
if the overlay carries a "source" (a bare number is accepted but flagged).
"""
import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def ranking():
    """sum of share_pct per parameter over all outputs of all golden sensitivity files of link scenarios"""
    tot, prov = {}, {}
    for path in sorted(glob.glob(os.path.join(HERE, "golden", "sensitivity_*.txt"))):
        if "button" in os.path.basename(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip() or line.startswith("##") or line.startswith("rank,"):
                    continue
                row = next(csv.reader([line]))
                if len(row) < 6:
                    continue
                tot[row[1]] = tot.get(row[1], 0.0) + float(row[5])
                prov[row[1]] = row[2]
    return sorted(tot.items(), key=lambda kv: -kv[1]), prov


def leaves():
    """{key: (value, unit, file, calibration_step)} from both parameter files (keys as the engine names them)"""
    sys.path.insert(0, HERE)
    import common
    out = {}
    for fn in ("params.json", "params.degrade.json"):
        with open(os.path.join(HERE, fn), encoding="utf-8") as f:
            doc = json.load(f)
        for sec, body in doc["sections"].items():
            steps = body.get("calibration", [])
            for k, leaf in common.iter_leaves(body.get("params", {}), sec + "."):
                step = next((c for c in steps if k in c.get("params", [])), None)
                out[k] = (leaf.get("value"), leaf.get("unit", ""), fn, step)
    return out


def cmd_sheet(a):
    rank, prov = ranking()
    lv = leaves()
    print("# measure-first sheet: ranking is the MODEL's sensitivity (not real-world severity); share = summed % over link outputs")
    print("rank,parameter,provenance,prior,unit,share_sum_pct,tool,how_to_measure")
    for i, (k, s) in enumerate(rank[: a.top], 1):
        v = lv.get(k, ("?", "", "", None))
        st = v[3] or {}
        how = (st.get("measure") or "").replace(",", ";")
        print(f"{i},{k},{prov.get(k, '?')},{v[0]},{v[1]},{s:.1f},{(st.get('tool') or '').replace(',', ';')},{how}")


def cmd_template(a):
    rank, _ = ranking()
    tpl = {k: None for k, _ in rank[: a.top]}
    with open(a.file, "w", encoding="utf-8") as f:
        json.dump(tpl, f, indent=1)
        f.write("\n")
    print(f"wrote {a.file}: fill numbers (or {{\"value\": x, \"source\": \"meter 2026-10-06\"}}); null = not measured")


def split_overlay(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    base_keys = {k for k, v in leaves().items() if v[2] == "params.json"}
    model, degrade, flagged = {}, {}, []
    for k, v in doc.items():
        if v is None:
            continue
        if not (isinstance(v, dict) and v.get("source")):
            flagged.append(k)
        (model if k in base_keys else degrade)[k] = v
    return model, degrade, flagged


def run(scn, n, seed, env):
    e = dict(os.environ)
    e.update(env)
    p = subprocess.run([sys.executable, os.path.join(HERE, "scenario_engine.py"), "run", scn, "--n", str(n), "--seed", str(seed)],
                       capture_output=True, text=True, env=e)
    if p.returncode:
        sys.exit(p.stderr.strip() or "scenario_engine failed")
    rows = {}
    for line in p.stdout.splitlines():
        if line and not line.startswith("#") and "," in line and line.split(",")[0] in ("residual", "margin_db", "range_m", "g2g_ms", "availability"):
            c = line.split(",")
            rows[c[0]] = (float(c[2]), float(c[3]), float(c[4]))
    return rows


def cmd_whatif(a):
    model, degrade, flagged = split_overlay(a.file)
    if not model and not degrade:
        sys.exit("overlay has no measured values (all null)")
    env = {}
    tmps = []
    for name, d in (("MODEL_MEASURED", model), ("DEGRADE_MEASURED", degrade)):
        if d:
            t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            json.dump(d, t)
            t.close()
            tmps.append(t.name)
            env[name] = t.name
    try:
        before = run(a.scenario, a.n, a.seed, {})
        after = run(a.scenario, a.n, a.seed, env)
    finally:
        for t in tmps:
            os.unlink(t)
    print(f"# what-if {a.scenario} n={a.n} seed={a.seed}; measured keys: {len(model) + len(degrade)}")
    if flagged:
        print("# WARNING: no 'source' for: " + ",".join(flagged) + " (treated as value, not logged as MEASURED_HW)")
    print("output,p5_before,p5_after,p50_before,p50_after,p95_before,p95_after,spread_p95-p5_before,spread_after")
    for k in before:
        b, af = before[k], after.get(k, before[k])
        print(f"{k},{b[0]:.4g},{af[0]:.4g},{b[1]:.4g},{af[1]:.4g},{b[2]:.4g},{af[2]:.4g},{b[2] - b[0]:.4g},{af[2] - af[0]:.4g}")
    print("# outputs stay modelled (SYNTH/UNMEASURED for every non-measured key); a measured key can move the spread either way")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet")
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(fn=cmd_sheet)
    t = sub.add_parser("template")
    t.add_argument("file")
    t.add_argument("--top", type=int, default=15)
    t.set_defaults(fn=cmd_template)
    w = sub.add_parser("whatif")
    w.add_argument("file")
    w.add_argument("--scenario", default="nominal_pi5_5a_150m")
    w.add_argument("--n", type=int, default=30)
    w.add_argument("--seed", type=int, default=1)
    w.set_defaults(fn=cmd_whatif)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
