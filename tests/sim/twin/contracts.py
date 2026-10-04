"""Threshold contracts: invariants of the whole chain that are EVALUATED on every twin run (and tested in test_twin.py).

Each contract is fn(result, P, ctx) -> (status, detail), status in PASS / FAIL / NA. `result` is analysis.analyze() output, P the effective
constants (schedule.effective), ctx = {"plan", "forecast", ...}. Thresholds that are not derived from the software's own constants are
SYNTH tolerances (marked) and live in TOL so a test can see them.
"""
import analysis

TOL = {
    "restore_s": 1.0,           # SYNTH: control must be back within this after the link returns (bridge sends at 20 Hz, tick <= 50 ms)
    "video_recover_s": 1.0,     # SYNTH
    "freeze_fraction": 0.9,     # the video gap must cover at least this share of the played silence (the hub really cut the stream)
    "noctl_slack_s": 0.5,       # SYNTH: |measured - forecast| RC-without-control seconds, on top of the measured restore time
    "deadman_lo_s": 0.1, "deadman_hi_s": 0.4,   # SYNTH: dead-man detection window around TX12_DEADMAN_MS
    "hold_slack_s": 0.4,        # SYNTH
    "probe_count": 3,           # in-flight datagrams at the end of the run
    "throttle_fs_frames_min": 1,
}


def judged(o):
    """Outages that have a before-state to compare with: the initial bring-up silence has none (reported separately as startup latency)."""
    return not any(t.startswith("silence:bringup") for t in o["tags"])


def _fmt(items, n=3):
    return "; ".join(str(i) for i in items[:n]) + (" ..." if len(items) > n else "")


def c_single_writer(r, P, ctx):
    b = r["bridge"]
    if b["rc_frames"] == 0:
        return "NA", "bridge sent no RC frames"
    bad = [s for s in b["sysids"] if s != P["bridge_sysid"]]
    if bad:
        return "FAIL", "frames with sysid %s != bridge sysid %d" % (bad, P["bridge_sysid"])
    if P["mav_gcs_sysid"] != P["bridge_sysid"]:
        return "FAIL", "FC MAV_GCS_SYSID=%d != bridge sysid=%d (the FC would ignore every override)" % (P["mav_gcs_sysid"], P["bridge_sysid"])
    if r["fc"]["ignored_override"]:
        return "FAIL", "FC ignored %d overrides as 'not a GCS'" % r["fc"]["ignored_override"]
    return "PASS", "%d RC frames, sysids=%s" % (b["rc_frames"], b["sysids"])


def c_target(r, P, ctx):
    b = r["bridge"]
    if b["rc_frames"] == 0:
        return "NA", "bridge sent no RC frames"
    if b["targets"] != [P["fc_sysid"]]:
        return "FAIL", "RC frames addressed to %s, FC sysid is %d" % (b["targets"], P["fc_sysid"])
    return "PASS", "all frames target_system=%d" % P["fc_sysid"]


def c_hub_silence(r, P, ctx):
    h = r["hub"]
    n = h["delivered_in_silence"] + h["video_delivered_in_silence"]
    return ("FAIL", "%d packets crossed the hub while the schedule said silence" % n) if n else ("PASS", "nothing delivered during silence")


def c_override_expiry(r, P, ctx):
    e = r["override_expiry"]
    miss = [g for g in e["required"] if g["got"] is None]
    if miss:
        return "FAIL", "no 'RC override expired' within RC_OVERRIDE_TIME(%.2fs)+tol after a gap: %s" % (P["rc_override_time_real"], _fmt(miss))
    if e["spurious"]:
        return "FAIL", "override expiry without a matching gap at t=%s" % e["spurious"]
    return "PASS", "%d gap(s) > RC_OVERRIDE_TIME all expired on time" % len(e["required"])


def c_gcs_failsafe(r, P, ctx):
    g = r["gcs_failsafe"]
    if not P["fs_gcs_enable"]:
        fired = [t for t, x in r["fc"]["events"] if x.startswith("GCS Failsafe") and not x.startswith("GCS Failsafe Cleared")]
        return ("FAIL", "FS_GCS_ENABLE=0 but a GCS failsafe fired at %s" % fired) if fired else ("PASS", "FS_GCS_ENABLE=0: no GCS failsafe (as modelled)")
    miss = [x for x in g["required"] if x["got"] is None]
    if miss:
        return "FAIL", "FS_GCS_ENABLE=1 but no GCS failsafe within FS_GCS_TIMEOUT(%.2fs)+tol: %s" % (P["fs_gcs_timeout_real"], _fmt(miss))
    if g["spurious"]:
        return "FAIL", "GCS failsafe without a heartbeat gap at t=%s" % g["spurious"]
    return "PASS", "%d heartbeat gap(s) > FS_GCS_TIMEOUT all tripped by +%.2fs" % (len(g["required"]), analysis.TOL_EVENT + 0.25)


def c_radio_failsafe(r, P, ctx):
    if P["receiver_present"]:
        return "NA", "a physical receiver is present: override loss is not a radio failsafe"
    miss = [x for x in r["radio_failsafe"]["required"] if x["got"] is None]
    if miss:
        return "FAIL", "override-only RC but no Radio Failsafe after RC_OVERRIDE_TIME+RC_FS_TIMEOUT: %s" % _fmt(miss)
    return "PASS", "%d gap(s) > RC_OVERRIDE_TIME+RC_FS_TIMEOUT all raised the radio failsafe" % len(r["radio_failsafe"]["required"])


def c_oracle(r, P, ctx):
    o = r["oracle"]
    if o["missing"] or o["extra"] or o["max_dt"] > analysis.TOL_EVENT:
        return "FAIL", "apm_fc.py events differ from apm_model.ApmModel on the same delivered frames: missing=%s extra=%s max_dt=%.3f" % (
            o["missing"], o["extra"], o["max_dt"])
    return "PASS", "%d events identical to the model (max dt %.0f ms)" % (o["pairs"], o["max_dt"] * 1000)


def c_recovery(r, P, ctx):
    bad = []
    n = 0
    for o in r["outages"]:
        if o["r1"] + TOL["restore_s"] > r["t_end"] or not judged(o):
            continue
        n += 1
        c = o["control_restored_after_s"]
        if c is None or c > TOL["restore_s"]:
            bad.append((o["tags"], o["r1"], c))
    if not n:
        return "NA", "no outage with a full recovery window"
    return ("FAIL", "control not restored within %.1fs after the link returned: %s" % (TOL["restore_s"], _fmt(bad))) if bad else \
        ("PASS", "%d outage(s): control back within %.1fs" % (n, TOL["restore_s"]))


def c_video_freeze(r, P, ctx):
    ql = r["video"]["quiet_loss"]
    bad, n = [], 0
    for o in r["outages"]:
        if o["r1"] + TOL["video_recover_s"] > r["t_end"] or not judged(o):
            continue
        n += 1
        fz, rec = o["video_freeze_s"], o["video_recover_after_s"]
        if fz is None or fz < TOL["freeze_fraction"] * o["played_real_s"]:
            bad.append(("no freeze for a silence", o["tags"], fz, o["played_real_s"]))
        elif ql is not None and ql < 0.5 and (rec is None or rec > TOL["video_recover_s"]):
            bad.append(("video recovery slow", o["tags"], rec))
    if not n:
        return "NA", "no outage with a full recovery window"
    return ("FAIL", _fmt(bad)) if bad else ("PASS", "%d outage(s): freeze covers the silence, video back within %.1fs" % (n, TOL["video_recover_s"]))


def c_probe(r, P, ctx):
    p = r["video"]["probe"]
    if not p:
        return "NA", "udp_probe.py printed no PROBE line"
    v = r["video"]
    d1, d2 = abs(p["recv"] - v["total_delivered"]), abs(p["sent"] - v["total_sent"])
    if d1 > TOL["probe_count"] or d2 > TOL["probe_count"]:
        return "FAIL", "udp_probe recv/sent=%d/%d but hub tap delivered/sent=%d/%d" % (p["recv"], p["sent"], v["total_delivered"], v["total_sent"])
    return "PASS", "udp_probe %d/%d == hub tap %d/%d" % (p["recv"], p["sent"], v["total_delivered"], v["total_sent"])


def c_no_control_forecast(r, P, ctx):
    bad, n = [], 0
    for o in r["outages"]:
        if o["r1"] + TOL["restore_s"] > r["t_end"] or not judged(o) or any(t.endswith("+joystick") for t in o["tags"]):
            continue                                            # with the joystick gone there is no operator input to lose
        n += 1
        slack = (o["control_restored_after_s"] or TOL["restore_s"]) + TOL["noctl_slack_s"]
        if abs(o["no_control_s"] - o["forecast_no_control_s"]) > slack:
            bad.append((o["tags"], "measured %.2f vs forecast %.2f" % (o["no_control_s"], o["forecast_no_control_s"])))
    if not n:
        return "NA", "no outage with a full recovery window"
    return ("FAIL", _fmt(bad)) if bad else ("PASS", "%d outage(s): RC-without-control seconds match the RC_OVERRIDE_TIME forecast" % n)


def c_fc_forecast(r, P, ctx):
    bad, n = [], 0
    for o in r["outages"]:
        if o["r1"] + TOL["restore_s"] > r["t_end"] or not judged(o):
            continue
        for key, got in (("override_expired", o["override_expired_after_s"]), ("gcs_failsafe", o["gcs_failsafe_after_s"]),
                         ("radio_failsafe", o["radio_failsafe_after_s"])):
            want = o["forecast"][key]
            if want is None:
                continue
            n += 1
            if want != (got is not None):
                bad.append((o["tags"], key, "forecast %s, measured %s" % (want, got)))
    if not n:
        return "NA", "no decidable forecast"
    return ("FAIL", _fmt(bad)) if bad else ("PASS", "%d forecast flags agree with the real programs" % n)


def c_deadman(r, P, ctx):
    if not r["deadman"]:
        return "NA", "no joystick stall in this run"
    bad = []
    for d in r["deadman"]:
        why = []
        if d["deadman_at"] is None or not (P["deadman_ms_real"] / 1000.0 - TOL["deadman_lo_s"] <= d["deadman_at"] <= P["deadman_ms_real"] / 1000.0 + TOL["deadman_hi_s"]):
            why.append("dead-man at %s (want ~%.2fs)" % (d["deadman_at"], P["deadman_ms_real"] / 1000.0))
        if d["throttle_fs_frames"] < TOL["throttle_fs_frames_min"]:
            why.append("no throttle-failsafe frame")
        if d["release_frames"] < 1:
            why.append("channels not released")
        if d["sticks_after_deadman"]:
            why.append("%d stick frames after dead-man" % d["sticks_after_deadman"])
        if d["release_end"] is not None and d["deadman_at"] is not None and d["release_end"] > d["deadman_at"] + P["release_hold_real"] + TOL["hold_slack_s"]:
            why.append("release frames lasted too long (%.2fs)" % d["release_end"])
        if d["frames_after_release"]:
            why.append("%d frames after the release hold (should be silent)" % d["frames_after_release"])
        ends_in_run = d["stall"][1] < r["t_end"] - 0.1          # a stall that lasts to the end of the replay has no resume to check
        if ends_in_run and (d["resumed_after_s"] is None or d["resumed_after_s"] > 0.6):
            why.append("sticks did not resume after the input came back (%s)" % d["resumed_after_s"])
        if why:
            bad.append((d["stall"], why))
    return ("FAIL", _fmt(bad)) if bad else ("PASS", "%d stall(s): throttle failsafe, release, silence, resume" % len(r["deadman"]))


def c_model_down(r, P, ctx):
    f = ctx.get("forecast")
    if not f:
        return "NA", "no model forecast (synthetic plan)"
    m = f["model"]
    sched = sum(s["t1"] - s["t0"] for s in f["segs"] if s["silent"])
    bu = f["fx"]["bringup_s"] if ctx.get("include_bringup", True) else 0.0
    n = len(f["fx"]["silences"])
    diff = abs(m["down_s"] - (sched - bu))
    budget = f["dt"] * (n + 1)
    return ("FAIL", "model down_s=%.1f vs event-derived silence %.1f (diff %.1f > %.1f)" % (m["down_s"], sched - bu, diff, budget)) if diff > budget else \
        ("PASS", "model down_s=%.1f, events explain %.1f (|diff| %.1f <= %.1f slice-quantisation budget)" % (m["down_s"], sched - bu, diff, budget))


def c_budget(r, P, ctx):
    plan = ctx.get("plan")
    if not plan:
        return "NA", "no plan"
    return ("FAIL", "plan %.1fs exceeds the %.1fs real-time budget" % (plan["real_s"], P["budget_s"])) if plan["real_s"] > P["budget_s"] + 1e-6 else \
        ("PASS", "plan %.1fs <= budget %.1fs" % (plan["real_s"], P["budget_s"]))


CONTRACTS = [
    ("K01", "single writer: every RC override carries the bridge sysid == FC MAV_GCS_SYSID", "safety", c_single_writer),
    ("K02", "RC override is addressed only to the FC sysid", "safety", c_target),
    ("K03", "no packet crosses the link during a silence interval", "hub", c_hub_silence),
    ("K04", "after silence > RC_OVERRIDE_TIME the FC drops the override (and never earlier/without a gap)", "safety", c_override_expiry),
    ("K05", "FS_GCS_ENABLE=1: GCS failsafe within FS_GCS_TIMEOUT of the last heartbeat; none without a gap", "safety", c_gcs_failsafe),
    ("K06", "override-only RC: Radio Failsafe after RC_OVERRIDE_TIME+RC_FS_TIMEOUT", "safety", c_radio_failsafe),
    ("K07", "apm_fc.py behaves like apm_model.ApmModel on the frames actually delivered", "model", c_oracle),
    ("K08", "control is restored within the bound after the link returns", "recovery", c_recovery),
    ("K09", "video freezes for the whole silence and recovers within the bound", "recovery", c_video_freeze),
    ("K10", "udp_probe.py counters equal the hub tap (the instrument sees what the hub did)", "hub", c_probe),
    ("K11", "RC-without-control seconds match the RC_OVERRIDE_TIME forecast", "divergence", c_no_control_forecast),
    ("K12", "analytic FC forecast (expiry/GCS/radio failsafe per outage) equals the real programs", "divergence", c_fc_forecast),
    ("K13", "dead-man: throttle failsafe, release, silence on joystick loss; resume afterwards", "safety", c_deadman),
    ("K14", "model down_s is explained by the event-derived silence (slice quantisation budget)", "divergence", c_model_down),
    ("K15", "replay fits the real-time budget", "plan", c_budget),
]


def evaluate(result, P, ctx=None):
    ctx = ctx or {}
    out = []
    for cid, title, tag, fn in CONTRACTS:
        st, det = fn(result, P, ctx)
        out.append({"id": cid, "title": title, "class": tag, "status": st, "detail": det})
    return out


def verdict(cs):
    return "FAIL" if any(c["status"] == "FAIL" for c in cs) else "PASS"
