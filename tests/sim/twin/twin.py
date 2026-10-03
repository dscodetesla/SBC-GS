#!/usr/bin/env python3
"""Session replay digital twin: scenario-engine events -> channel impairment schedule -> the REAL project programs -> system-level numbers.

  twin.py run <scenario> --seed N [--duration S] [--speed X] [--budget B] [--json FILE]   one replay, table + JSON (sbc-gs-twin/1)
  twin.py matrix [--scenarios a,b] [--seeds 1,2,3] [--jobs 3] [--json FILE]                 several scenarios x seeds
  twin.py plan <scenario> --seed N                                                          schedule + replay plan only (no processes)
  twin.py contracts                                                                         list the threshold contracts

What is applied to what (docs/SIM-TWIN.md): the schedule drives a UDP hub (channel.py, air_relay.py decision rule) between
bench/tx12_bridge.py and tests/sim/apm_fc.py, and between tests/sim/udp_probe.py and its receiver. The numbers are SYNTH model output
pushed through real code; the FC is the apm_model.py MODEL, not ArduPilot. Never connect any of this to an aircraft.
Exit codes: 0 all contracts PASS/NA, 1 a contract FAILED, 2 usage error, 77 skipped (no pymavlink).
"""
import argparse
import concurrent.futures
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contracts  # noqa: E402
import schedule  # noqa: E402

SCHEMA = "sbc-gs-twin/1"
DEFAULT_MATRIX = (("pi5_3a_weak_psu", (1, 2)), ("nominal_pi5_5a_150m", (1, 2)), ("hot_day_closed_case", (3,)), ("elrs_usb3_desense_edge", (3,)))
PARAM_KEYS = ("speed", "budget_s", "rc_override_time", "fs_gcs_enable", "fs_gcs_timeout", "rc_fs_timeout", "receiver_present", "mav_gcs_sysid",
              "bridge_sysid", "fc_sysid", "deadman_ms", "hb_period_s", "rate_hz", "release_hold_s", "silence_cap_s", "joystick_usb",
              "include_bringup", "pre_s", "post_s", "quiet_s", "warmup_s", "video_pps")


def build(scenario, seed, duration=None, over=None):
    P = schedule.effective(over)
    fc = schedule.forecast(scenario, seed, duration, P["joystick_usb"], P["include_bringup"])
    plan = schedule.make_plan(fc["segs"], fc["T"], fc["base"], P, fc["fx"])
    return fc, P, plan


def divergences(fc, P, res, plan):
    """Where the model forecast and the real programs disagree (reported, not hidden). Returns a list of {id, text}."""
    out = []
    m = fc["model"]
    sched_sil = sum(s["t1"] - s["t0"] for s in fc["segs"] if s["silent"]) - (fc["fx"]["bringup_s"] if P["include_bringup"] else 0.0)
    out.append({"id": "D1", "kind": "quantisation", "text": "engine down_s=%.1f s (slice dt=%.0f s) vs event-derived silence %.1f s: the engine counts whole slices as down" % (
        m["down_s"], fc["dt"], sched_sil)})
    causes = {"usb_dropout": "usb_spontaneous", "usb_trip": "usb_overcurrent", "thermal_shutdown": "thermal_shutdown"}
    tagset = {t for s in fc["segs"] for t in s["tags"]}
    for flag, tag in causes.items():
        if (flag in m["flags"]) != (("silence:" + tag) in tagset):
            if flag == "usb_trip" and "usb_latched" in m["flags"]:
                continue
            out.append({"id": "D2", "kind": "flag-vs-schedule", "text": "engine flag %s=%s but schedule has silence:%s=%s" % (
                flag, flag in m["flags"], tag, ("silence:" + tag) in tagset)})
    for o in res["outages"]:
        if not contracts.judged(o):
            continue
        for key, got in (("override_expired", o["override_expired_after_s"]), ("gcs_failsafe", o["gcs_failsafe_after_s"]),
                         ("radio_failsafe", o["radio_failsafe_after_s"])):
            want = o["forecast"][key]
            if want is not None and want != (got is not None):
                out.append({"id": "D3", "kind": "forecast-vs-real", "text": "outage %s r0=%.1f: forecast %s=%s, real programs %s" % (o["tags"], o["r0"], key, want, got is not None)})
    ql, n = res["video"]["quiet_loss"], res["video"]["quiet_n"]
    if ql is not None and n:
        p = fc["base"]["loss"]
        sd = (max(p * (1 - p), 1e-9) / n) ** 0.5
        if abs(ql - p) > 4 * sd + 2.0 / n:
            out.append({"id": "D4", "kind": "loss", "text": "quiet-link video loss %.4f vs model residual %.4f (n=%d, 4 sigma = %.4f)" % (ql, p, n, 4 * sd)})
    est = res["rc"]["control_established"]
    s_av = m["availability"]
    if s_av >= 0.5 and not est:
        out.append({"id": "D6", "kind": "availability", "text": "engine availability=%.2f but the real chain never established control in the replay" % s_av})
    if res["bridge"]["deadman_log"] and not res["deadman"]:
        out.append({"id": "D5", "kind": "bridge", "text": "bridge dead-man fired without an injected joystick stall at %s" % res["bridge"]["deadman_log"]})
    lk = [o for o in res["outages"] if o["override_expired_after_s"] is not None]
    if lk and not res["bridge"]["released_any"]:
        out.append({"id": "I1", "kind": "design", "text": "link silence does not make the bridge release channels (it cannot know): the FC alone expires the override after RC_OVERRIDE_TIME"})
    return out


def extrapolate(fc, P, plan, res):
    """Whole-session numbers in MODEL seconds: what the replay measured scaled up, plus what was truncated/skipped (labelled)."""
    sp = P["speed"]
    sil = [(s["t0"], s["t1"]) for s in fc["segs"] if s["silent"] and not any(t == "silence:bringup" for t in s["tags"])]
    model_sil = sum(b - a for a, b in sil)
    replayed = sum(o["played_real_s"] * sp for o in res["outages"] if not any(t.startswith("silence:bringup") for t in o["tags"]))
    ro = P["rc_override_time"]
    fore_nc = sum(max(0.0, (b - a) - ro) for a, b in sil)
    meas_nc = sum(o["no_control_s"] * sp for o in res["outages"] if not any(t.startswith("silence:bringup") for t in o["tags"]))
    trunc_ext = sum(o["truncated_model_s"] for o in res["outages"] if o["override_expired_after_s"] is not None)
    return {"silent_model_s": round(model_sil, 2), "silent_replayed_model_s": round(replayed, 2),
            "rc_no_control_forecast_model_s": round(fore_nc, 2), "rc_no_control_measured_replayed_model_s": round(meas_nc, 2),
            "rc_no_control_truncated_ext_model_s": round(trunc_ext, 2),
            "note": "measured covers only replayed silences; the remainder is extrapolated from the replayed ones (INF), not measured"}


def run_one(scenario, seed, duration=None, over=None, py=None, with_plan=None):
    import analysis
    import harness
    fc, P, plan = with_plan or build(scenario, seed, duration, over)
    py = py or os.environ.get("TWIN_PY") or sys.executable
    t0 = time.monotonic()
    trace = harness.run_plan(plan, P, seed, py=py)
    res = analysis.analyze(trace, plan, P)
    ctx = {"plan": plan, "forecast": fc, "include_bringup": P["include_bringup"]}
    cs = contracts.evaluate(res, P, ctx)
    dv = divergences(fc, P, res, plan)
    return {
        "schema": SCHEMA, "scenario": scenario, "seed": seed, "wall_s": round(time.monotonic() - t0, 2),
        "params": {k: P[k] for k in PARAM_KEYS},
        "model": {"digest": fc["digest"], "summary": fc["model"], "base_channel": fc["base"], "distance_m": fc["distance_m"], "T": fc["T"],
                  "events_applied": fc["fx"]["applied"], "events_not_applied": fc["fx"]["ignored"]},
        "plan": {"real_s": round(plan["real_s"], 2), "model_s": plan["model_s"], "replayed_model_s": round(plan["replayed_model_s"], 2),
                 "elided_model_s": round(plan["elided_model_s"], 2), "truncated_model_s": round(plan["truncated_model_s"], 2), "skipped": plan["skipped"], "windows": plan["windows"],
                 "outages": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in o.items()} for o in plan["outages"]]},
        "measured": res, "extrapolated": extrapolate(fc, P, plan, res), "divergence": dv, "contracts": cs, "verdict": contracts.verdict(cs),
    }


def fmt_report(r):
    L = []
    p, m = r["plan"], r["model"]
    L.append("# twin: %s seed=%d speed=%.1f  model=%.0fs replayed %.0fs (elided %.0fs, truncated %.0fs, skipped %d window(s)) real=%.1fs wall=%.1fs digest=%s" % (
        r["scenario"], r["seed"], r["params"]["speed"], p["model_s"], p["replayed_model_s"], p["elided_model_s"], p["truncated_model_s"], len(p["skipped"]), p["real_s"], r["wall_s"], m["digest"]))
    s = m["summary"]
    L.append("# model forecast: availability=%.2f down_s=%.0f residual=%.3g flags=%s (SYNTH/UNMEASURED priors)" % (
        s["availability"], s["down_s"], s["residual"], ",".join(s["flags"]) or "-"))
    L.append("# FC=apm_fc.py (model) RC_OVERRIDE_TIME=%.2fs FS_GCS_ENABLE=%d FS_GCS_TIMEOUT=%.2fs RC_FS_TIMEOUT=%.2fs receiver=%s  bridge dead-man=%dms joystick_usb=%s" % (
        r["params"]["rc_override_time"] / r["params"]["speed"], r["params"]["fs_gcs_enable"], r["params"]["fs_gcs_timeout"] / r["params"]["speed"],
        r["params"]["rc_fs_timeout"] / r["params"]["speed"], r["params"]["receiver_present"], int(r["params"]["deadman_ms"] / r["params"]["speed"]), r["params"]["joystick_usb"]))
    L.append("outage,real_s,model_s,trunc_model_s,expired_after_s,gcs_fs_after_s,radio_fs_after_s,restored_after_s,video_freeze_s,video_back_after_s,rc_noctl_s,forecast_noctl_s")
    for o in r["measured"]["outages"]:
        f = lambda v: "-" if v is None else "%.2f" % v  # noqa: E731
        L.append("%s@%.1f,%.2f,%.1f,%.1f,%s,%s,%s,%s,%s,%s,%.2f,%.2f" % (",".join(o["tags"]).replace(",", "+"), o["r0"], o["played_real_s"], o["model_len_s"], o["truncated_model_s"],
                 f(o["override_expired_after_s"]), f(o["gcs_failsafe_after_s"]), f(o["radio_failsafe_after_s"]), f(o["control_restored_after_s"]),
                 f(o["video_freeze_s"]), f(o["video_recover_after_s"]), o["no_control_s"], o["forecast_no_control_s"]))
    rc, v = r["measured"]["rc"], r["measured"]["video"]
    L.append("RC: operator_active=%.2fs no_control=%.2fs (startup %s)  video: %d/%d delivered, %d freeze(s), quiet loss %s  bridge: %d frames, dead-man %d, released=%s" % (
        rc["operator_active_s"], rc["no_control_s"], "never" if rc["startup_s"] is None else "%.2fs" % rc["startup_s"], v["delivered"], v["sent"], len(v["freezes"]),
        "-" if v["quiet_loss"] is None else "%.4f" % v["quiet_loss"], r["measured"]["bridge"]["rc_frames"], len(r["measured"]["bridge"]["deadman_log"]), r["measured"]["bridge"]["released_any"]))
    e = r["extrapolated"]
    L.append("session (model s): silent=%.1f replayed=%.1f | RC without control: forecast=%.1f measured(replayed)=%.1f +truncated=%.1f" % (
        e["silent_model_s"], e["silent_replayed_model_s"], e["rc_no_control_forecast_model_s"], e["rc_no_control_measured_replayed_model_s"], e["rc_no_control_truncated_ext_model_s"]))
    L.append("contract,class,status,detail")
    for c in r["contracts"]:
        L.append("%s,%s,%s,%s" % (c["id"], c["class"], c["status"], c["detail"]))
    for d in r["divergence"]:
        L.append("DIVERGENCE %s [%s] %s" % (d["id"], d["kind"], d["text"]))
    L.append("VERDICT %s" % r["verdict"])
    return L


def parse_over(a):
    over = {"speed": a.speed, "budget_s": a.budget}
    for k in ("rc_override_time", "fs_gcs_timeout", "rc_fs_timeout", "mav_gcs_sysid", "bridge_sysid", "deadman_ms", "silence_cap_s"):
        v = getattr(a, k, None)
        if v is not None:
            over[k] = v
    if getattr(a, "fs_gcs_enable", None) is not None:
        over["fs_gcs_enable"] = a.fs_gcs_enable
    if getattr(a, "receiver_present", False):
        over["receiver_present"] = True
    if getattr(a, "joystick_usb", None):
        over["joystick_usb"] = a.joystick_usb
    if getattr(a, "no_bringup", False):
        over["include_bringup"] = False
    return over


def add_common(sp):
    sp.add_argument("--speed", type=float, default=2.0, help="1..2: schedule durations and FC time constants are divided by it (1 = real constants)")
    sp.add_argument("--budget", type=float, default=20.0, help="real-time budget of one replay, s")
    sp.add_argument("--rc-override-time", dest="rc_override_time", type=float)
    sp.add_argument("--fs-gcs-enable", dest="fs_gcs_enable", type=int, choices=(0, 1))
    sp.add_argument("--fs-gcs-timeout", dest="fs_gcs_timeout", type=float)
    sp.add_argument("--rc-fs-timeout", dest="rc_fs_timeout", type=float)
    sp.add_argument("--mav-gcs-sysid", dest="mav_gcs_sysid", type=int)
    sp.add_argument("--bridge-sysid", dest="bridge_sysid", type=int)
    sp.add_argument("--deadman-ms", dest="deadman_ms", type=int)
    sp.add_argument("--silence-cap", dest="silence_cap_s", type=float)
    sp.add_argument("--receiver-present", action="store_true")
    sp.add_argument("--joystick-usb", choices=("independent", "shared"))
    sp.add_argument("--no-bringup", action="store_true", help="do not replay the initial adapter bring-up delay")
    sp.add_argument("--duration", type=float, help="model horizon, s (default: the scenario's)")
    sp.add_argument("--json", metavar="FILE")
    sp.add_argument("--py", help="python with pymavlink for the child programs (default: this interpreter or $TWIN_PY)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("run")
    sp.add_argument("scenario")
    sp.add_argument("--seed", type=int, default=1)
    add_common(sp)
    sp = sub.add_parser("plan")
    sp.add_argument("scenario")
    sp.add_argument("--seed", type=int, default=1)
    add_common(sp)
    sp = sub.add_parser("matrix")
    sp.add_argument("--scenarios")
    sp.add_argument("--seeds")
    sp.add_argument("--jobs", type=int, default=3)
    add_common(sp)
    sub.add_parser("contracts")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "contracts":
            for cid, title, cls, _fn in contracts.CONTRACTS:
                print("%s\t%s\t%s" % (cid, cls, title))
            return 0
        over = parse_over(a)
        if a.cmd == "plan":
            fc, P, plan = build(a.scenario, a.seed, a.duration, over)
            print(json.dumps({"digest": fc["digest"], "model": fc["model"], "base": fc["base"], "applied": fc["fx"]["applied"], "ignored": fc["fx"]["ignored"],
                              "real_s": plan["real_s"], "elided_model_s": plan["elided_model_s"], "windows": plan["windows"], "skipped": plan["skipped"],
                              "outages": plan["outages"], "segments": plan["segs"]}, indent=1))
            return 0
        import harness
        py = a.py or os.environ.get("TWIN_PY") or sys.executable
        if not harness.have_pymavlink(py):
            print("SKIP twin: %s has no pymavlink (use --py /opt/sbcvenv/bin/python)" % py)
            return 77
        if a.cmd == "run":
            r = run_one(a.scenario, a.seed, a.duration, over, py)
            print("\n".join(fmt_report(r)))
            if a.json:
                with open(a.json, "w", encoding="utf-8") as f:
                    json.dump(r, f, indent=1, default=str)
            return 1 if r["verdict"] == "FAIL" else 0
        cells = []
        scs = a.scenarios.split(",") if a.scenarios else None
        sds = [int(x) for x in a.seeds.split(",")] if a.seeds else None
        if scs or sds:
            for sc in (scs or [d[0] for d in DEFAULT_MATRIX]):
                for sd in (sds or [1, 2]):
                    cells.append((sc, sd))
        else:
            cells = [(sc, sd) for sc, sds_ in DEFAULT_MATRIX for sd in sds_]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            futs = [ex.submit(run_one, sc, sd, a.duration, over, py) for sc, sd in cells]
            results = [f.result() for f in futs]
        print("scenario,seed,digest,real_s,outages,no_control_s,freezes,fails,divergences,verdict")
        for r in results:
            print("%s,%d,%s,%.1f,%d,%.2f,%d,%s,%d,%s" % (r["scenario"], r["seed"], r["model"]["digest"], r["plan"]["real_s"], len(r["measured"]["outages"]),
                  r["measured"]["rc"]["no_control_s"], len(r["measured"]["video"]["freezes"]), "+".join(c["id"] for c in r["contracts"] if c["status"] == "FAIL") or "-",
                  len(r["divergence"]), r["verdict"]))
        doc = {"schema": SCHEMA, "kind": "matrix", "cells": results,
               "verdict": "FAIL" if any(r["verdict"] == "FAIL" for r in results) else "PASS"}
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=1, default=str)
        print("VERDICT %s" % doc["verdict"])
        return 1 if doc["verdict"] == "FAIL" else 0
    except (ValueError, OSError, KeyError) as e:
        print("twin: error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
