"""Trace -> system-level measurements (pure functions; pymavlink only to decode the tapped MAVLink bytes).

Time base of every number: REAL seconds since the plan start (the channel's t=0). Model seconds appear only where named *_model_*.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TOL_EVENT = 0.20      # SYNTH: allowed lag between the model-predicted and the observed FC event (5 ms loop + pipe + scheduler)
EV_RE = re.compile(r"^EVENT\s+([0-9.]+)s\s+(.*)$")
KINDS = ("RC override started", "RC override expired", "GCS Failsafe Cleared", "GCS Failsafe", "Radio Failsafe Cleared", "Radio Failsafe",
         "override from sysid")


def ev_kind(text):
    for k in KINDS:
        if text.startswith(k):
            return k
    return text.split(":")[0]


def classify(chans, thr_ch, fs_us):
    if all(c == 0 for c in chans):
        return "RELEASE"
    if chans[thr_ch - 1] == fs_us and all(c == 0 for i, c in enumerate(chans) if i != thr_ch - 1):
        return "FSTHR"
    return "STICK"


def decode(recs, thr_ch=3, fs_us=1000):
    """Tapped datagrams -> frame dicts {type, sys, t_in, t_out, dropped, silent, chans, target, cls}."""
    from pymavlink import mavutil
    mav = mavutil.mavlink.MAVLink(None)
    out = []
    for r in recs:
        try:
            msgs = mav.parse_buffer(r["data"]) or []
        except Exception:      # noqa: BLE001  (corrupt frame: ignore, like a real parser)
            msgs = []
        for m in msgs:
            ty = m.get_type()
            f = {"type": ty, "sys": m.get_srcSystem(), "t_in": r["t_in"], "t_out": r["t_out"], "dropped": r["dropped"], "silent": r["silent"]}
            if ty in ("RC_CHANNELS_OVERRIDE", "RC_CHANNELS"):
                f["chans"] = [getattr(m, "chan%d_raw" % i) for i in range(1, 9)]
                f["target"] = getattr(m, "target_system", None)
                if ty == "RC_CHANNELS_OVERRIDE":
                    f["cls"] = classify(f["chans"], thr_ch, fs_us)
            out.append(f)
    return out


def fc_events(lines):
    out = []
    for t, ln in lines:
        m = EV_RE.match(ln)
        if m:
            out.append((t, m.group(2)))
    return out


def delivered(frames, ty, cls=None):
    return sorted(f["t_out"] for f in frames if f["type"] == ty and not f["dropped"] and f["t_out"] is not None and (cls is None or f.get("cls") in cls))


def gaps(times, t_end):
    """[(start, end_or_None)] between consecutive delivery times; the tail gap ends at None."""
    g = [(a, b) for a, b in zip(times, times[1:])]
    if times:
        g.append((times[-1], None))
    return g


def oracle(frames_g2f, P, t_end):
    """Replay the DELIVERED frames through apm_model.ApmModel (the model apm_fc.py wraps) on a virtual clock; returns [(t, text)]."""
    from apm_model import ApmModel
    fc = ApmModel(rc_override_time=P["rc_override_time_real"], fs_gcs_enable=int(P["fs_gcs_enable"]), fs_gcs_timeout=P["fs_gcs_timeout_real"],
                  mav_gcs_sysid=P["mav_gcs_sysid"], mav_gcs_sysid_hi=0, armed=True, receiver_present=P["receiver_present"],
                  rc_fs_timeout=P["rc_fs_timeout_real"])
    ev = sorted((f["t_out"], i, f) for i, f in enumerate(frames_g2f) if not f["dropped"] and f["t_out"] is not None
                and f["type"] in ("HEARTBEAT", "RC_CHANNELS_OVERRIDE"))
    out, k, was = [], 0, False
    t = 0.0
    while t < t_end:
        while k < len(ev) and ev[k][0] <= t:
            f = ev[k][2]
            k += 1
            if f["type"] == "HEARTBEAT":
                fc.on_heartbeat(f["sys"], t)
            else:
                if f["target"] not in (0, P["fc_sysid"]):
                    continue
                o = fc.overridden(t)
                fc.on_rc_override(f["sys"], f["chans"], t)
                if fc.overridden(t) and not o:
                    fc.events.append((t, "RC override started"))
        fc.tick(t)
        if was and not fc.overridden(t):
            fc.events.append((t, "RC override expired"))
        was = fc.overridden(t)
        t += 0.005
    return list(fc.events)


def match_events(expected, actual, tol=TOL_EVENT):
    """Greedy per-kind in-order matching. Returns (pairs, missing, extra) with pairs = [(kind, t_exp, t_act)]."""
    pairs, missing = [], []
    pool = [(t, ev_kind(x)) for t, x in actual]
    for t, x in expected:
        k = ev_kind(x)
        best = None
        for i, (ta, ka) in enumerate(pool):
            if ka == k and abs(ta - t) <= tol + 0.0 and (best is None or abs(ta - t) < abs(pool[best][0] - t)):
                best = i
        if best is None:
            missing.append((k, t))
        else:
            pairs.append((k, t, pool[best][0]))
            pool.pop(best)
    extra = [(k, t) for t, k in pool]
    return pairs, missing, extra


def measure(intervals):
    return sum(max(0.0, b - a) for a, b in intervals)


def subtract(base, cut):
    """base minus cut, both lists of (a,b)."""
    out = []
    for a, b in base:
        cur = [(a, b)]
        for c, d in cut:
            nxt = []
            for x, y in cur:
                if d <= x or c >= y:
                    nxt.append((x, y))
                else:
                    if c > x:
                        nxt.append((x, c))
                    if d < y:
                        nxt.append((d, y))
            cur = nxt
        out += cur
    return out


def override_intervals(events, t_end):
    iv, start = [], None
    for t, x in events:
        k = ev_kind(x)
        if k == "RC override started" and start is None:
            start = t
        elif k == "RC override expired" and start is not None:
            iv.append((start, t))
            start = None
    if start is not None:
        iv.append((start, t_end))
    return iv


def forecast_flags(L, P):
    """Analytic FC-side forecast of ONE silence of real length L (what the model says before running anything): which failsafes appear.
    Returns {flag: True/False/None}; None = within the ambiguity band of the threshold (heartbeat phase, 5 ms loop)."""
    amb = P["hb_period_real"] + 0.35

    def dec(thr):
        if L > thr + amb:
            return True
        if L < thr - 0.35:
            return False
        return None
    out = {"override_expired": dec(P["rc_override_time_real"]),
           "gcs_failsafe": dec(P["fs_gcs_timeout_real"]) if P["fs_gcs_enable"] else False,
           "radio_failsafe": dec(P["rc_override_time_real"] + P["rc_fs_timeout_real"]) if not P["receiver_present"] else False}
    return out


def analyze(trace, plan, P):
    """Everything the contracts and the report need. P = schedule.effective(...)."""
    t_end = plan["real_s"]          # analysis horizon: the plan end (what happens after it is the harness shutting down)
    trace_all = trace
    trace = dict(trace)
    for k in ("g2f", "f2g", "video"):
        trace[k] = [r for r in trace[k] if r["t_in"] <= t_end]
    trace["fc_lines"] = [(t, ln) for t, ln in trace["fc_lines"] if t <= t_end]
    trace["bridge_lines"] = [(t, ln) for t, ln in trace["bridge_lines"] if t <= t_end]
    thr, fsu = P["throttle_ch"], P["failsafe_throttle_us"]
    g2f = decode(trace["g2f"], thr, fsu)
    f2g = decode(trace["f2g"], thr, fsu)
    ev = fc_events(trace["fc_lines"])
    blog = [(t, ln) for t, ln in trace["bridge_lines"]]
    res = {"t_end": t_end}
    # ---- bridge side (frames as emitted: hub ingress)
    br = [f for f in g2f if f["type"] == "RC_CHANNELS_OVERRIDE"]
    hb = [f for f in g2f if f["type"] == "HEARTBEAT"]
    res["bridge"] = {
        "rc_frames": len(br), "heartbeats": len(hb), "sysids": sorted({f["sys"] for f in br + hb}),
        "targets": sorted({f["target"] for f in br}), "frames_by_class": {c: sum(1 for f in br if f["cls"] == c) for c in ("STICK", "FSTHR", "RELEASE")},
        "deadman_log": [round(t, 3) for t, ln in blog if "DEAD-MAN" in ln], "fresh_log": [round(t, 3) for t, ln in blog if "input fresh" in ln],
        "silent_log": [round(t, 3) for t, ln in blog if "release hold done" in ln],
        "released_any": any(f["cls"] == "RELEASE" for f in br),
    }
    # ---- hub self-check: nothing accepted while silent
    res["hub"] = {"delivered_in_silence": sum(1 for f in g2f + f2g if f["silent"] and not f["dropped"]),
                  "video_delivered_in_silence": sum(1 for r in trace["video"] if r["silent"] and not r["dropped"])}
    # ---- FC side
    d_rc = delivered(g2f, "RC_CHANNELS_OVERRIDE")
    d_stick = delivered(g2f, "RC_CHANNELS_OVERRIDE", ("STICK", "FSTHR"))
    d_hb = delivered([f for f in g2f if f["type"] == "HEARTBEAT" and f["sys"] == P["mav_gcs_sysid"]], "HEARTBEAT")
    res["fc"] = {"events": [(round(t, 3), x) for t, x in ev], "ignored_override": sum(1 for _t, x in ev if x.startswith("override from sysid")),
                 "delivered_rc": len(d_rc), "delivered_hb": len(d_hb)}
    # oracle: the model replayed on what was actually delivered
    exp = oracle(g2f, P, t_end)
    pairs, missing, extra = match_events(exp, ev)
    res["oracle"] = {"expected": [(round(t, 3), x) for t, x in exp], "pairs": len(pairs), "missing": [(k, round(t, 3)) for k, t in missing],
                     "extra": [(k, round(t, 3)) for k, t in extra], "max_dt": max([abs(a - b) for _k, a, b in pairs] or [0.0])}
    # ---- gap-based explicit checks
    tol_hi = 0.45
    gcs, spur_g = [], []
    if P["fs_gcs_enable"]:
        for a, b in gaps(d_hb, t_end):
            end = b if b is not None else t_end
            if end - a > P["fs_gcs_timeout_real"] + 0.05 and (b is not None or t_end - a > P["fs_gcs_timeout_real"] + 0.5):
                want = a + P["fs_gcs_timeout_real"]
                got = [t for t, x in ev if x.startswith("GCS Failsafe") and not x.startswith("GCS Failsafe Cleared") and want - 0.1 <= t <= want + tol_hi]
                gcs.append({"gap_start": round(a, 3), "gap_len": round(end - a, 3), "want_by": round(want + tol_hi, 3), "got": round(got[0], 3) if got else None})
    for t, x in ev:
        if x.startswith("GCS Failsafe") and not x.startswith("GCS Failsafe Cleared"):
            if not any(a + P["fs_gcs_timeout_real"] - 0.1 <= t for a, _b in gaps(d_hb, t_end) if t <= (_b or t_end + 1) + 0.2):
                spur_g.append(round(t, 3))
    res["gcs_failsafe"] = {"required": gcs, "spurious": spur_g}
    exp_cls = {(f["t_out"]): f["cls"] for f in br if not f["dropped"] and f["t_out"] is not None}
    exp_list, spur_e = [], []
    for a, b in gaps(d_rc, t_end):
        end = b if b is not None else t_end
        if exp_cls.get(a) in ("STICK", "FSTHR") and end - a > P["rc_override_time_real"] + 0.05 and (b is not None or t_end - a > P["rc_override_time_real"] + 0.5):
            want = a + P["rc_override_time_real"]
            got = [t for t, x in ev if x.startswith("RC override expired") and want - 0.1 <= t <= want + tol_hi]
            exp_list.append({"gap_start": round(a, 3), "gap_len": round(end - a, 3), "want_by": round(want + tol_hi, 3), "got": round(got[0], 3) if got else None})
    rel_t = [f["t_out"] for f in br if f["cls"] == "RELEASE" and not f["dropped"] and f["t_out"] is not None]
    for t, x in ev:
        if x.startswith("RC override expired"):
            ok = any(a + P["rc_override_time_real"] - 0.1 <= t <= (b if b is not None else t_end + 1) + 0.3 for a, b in gaps(d_rc, t_end)) \
                or any(0 <= t - r <= 0.2 for r in rel_t)
            if not ok:
                spur_e.append(round(t, 3))
    res["override_expiry"] = {"required": exp_list, "spurious": spur_e}
    rad, spur_r = [], []
    if not P["receiver_present"]:
        thr_t = P["rc_override_time_real"] + P["rc_fs_timeout_real"]
        for a, b in gaps(d_rc, t_end):
            end = b if b is not None else t_end
            if end - a > thr_t + 0.05 and (b is not None or t_end - a > thr_t + 0.5):
                want = a + thr_t
                got = [t for t, x in ev if x.startswith("Radio Failsafe") and not x.startswith("Radio Failsafe Cleared") and want - 0.1 <= t <= want + tol_hi]
                rad.append({"gap_start": round(a, 3), "gap_len": round(end - a, 3), "want_by": round(want + tol_hi, 3), "got": round(got[0], 3) if got else None})
    res["radio_failsafe"] = {"required": rad}
    # ---- RC control (operator intent vs what the FC sees)
    stalls = [tuple(s) for s in trace["input_stalls"]]
    op = subtract([(0.0, t_end)], stalls)
    ov = override_intervals(ev, t_end)
    first = ov[0][0] if ov else None
    base = [(max(a, first), b) for a, b in op if b > first] if first is not None else list(op)
    noctl_iv = subtract(base, ov)
    res["rc"] = {"control_established": first is not None, "startup_s": first, "operator_active_s": round(measure(base), 3), "no_control_s": round(measure(noctl_iv), 3),
                 "no_control_iv": [(round(a, 3), round(b, 3)) for a, b in noctl_iv if b - a > 1e-3]}
    # ---- video
    vid = trace["video"]
    d_vid = sorted(r["t_out"] for r in vid if not r["dropped"] and r["t_out"] is not None)
    freezes = [(a, b, b - a) for a, b in zip(d_vid, d_vid[1:]) if b - a > P["video_freeze_gap_s"]]
    base_p = plan.get("base", {}).get("loss")
    quiet = [r for r in vid if (r["p"] == base_p if base_p is not None else not r["silent"])]
    probe = {}
    for ln in trace["probe_lines"]:
        m = re.match(r"PROBE sent=(\d+) recv=(\d+)", ln)
        if m:
            probe = {"sent": int(m.group(1)), "recv": int(m.group(2))}
    allv = trace_all["video"]
    res["video"] = {"total_sent": len(allv), "total_delivered": sum(1 for r in allv if not r["dropped"] and r["t_out"] is not None), "sent": len(vid), "delivered": len(d_vid), "freezes": [(round(a, 3), round(b, 3), round(ln, 3)) for a, b, ln in freezes],
                    "quiet_loss": (sum(1 for r in quiet if r["dropped"]) / len(quiet)) if quiet else None, "quiet_n": len(quiet), "probe": probe,
                    "first_delivery": d_vid[0] if d_vid else None}
    # ---- per outage (silences of the plan)
    outs = []
    bound = 1.0
    for o in plan["outages"]:
        r0, r1 = o["r0"], o["r1"]
        L = r1 - r0
        pre = [t for t in d_rc if t <= r0 + 0.25]
        lastrc = pre[-1] if pre else None
        after = [t for t in d_stick if t >= r1]
        restore = (after[0] - r1) if after else None
        wnd_ev = [(t, x) for t, x in ev if r0 - 0.1 <= t <= r1 + bound + 2.0]

        def first_of(prefix, excl=None):
            c = [t for t, x in wnd_ev if x.startswith(prefix) and not (excl and x.startswith(excl)) and t >= r0]
            return round(c[0] - r0, 3) if c else None
        vb = [t for t in d_vid if t <= r0 + 0.1]
        va = [t for t in d_vid if t >= r1]
        nc = measure([(max(a, r0), min(b, r1 + bound)) for a, b in noctl_iv if b > r0 and a < r1 + bound])
        outs.append({"tags": o["tags"], "r0": round(r0, 3), "r1": round(r1, 3), "played_real_s": round(L, 3), "model_len_s": round(o["model_len"], 3),
                     "truncated_model_s": round(o["truncated_model_s"], 3), "last_rc_before": round(lastrc, 3) if lastrc is not None else None,
                     "override_expired_after_s": first_of("RC override expired"), "gcs_failsafe_after_s": first_of("GCS Failsafe", "GCS Failsafe Cleared"),
                     "radio_failsafe_after_s": first_of("Radio Failsafe", "Radio Failsafe Cleared"),
                     "control_restored_after_s": round(restore, 3) if restore is not None else None,
                     "video_last_before": vb[-1] if vb else None,
                     "video_freeze_s": round(va[0] - vb[-1], 3) if (va and vb) else None,
                     "video_recover_after_s": round(va[0] - r1, 3) if va else None,
                     "no_control_s": round(nc, 3), "forecast": forecast_flags(L, P),
                     "forecast_no_control_s": round(max(0.0, L - P["rc_override_time_real"]), 3)})
    res["outages"] = outs
    # ---- dead-man analysis for stalls
    dm = []
    for s0, s1 in stalls:
        bl = [f for f in br if s0 - 0.05 <= f["t_in"] <= s1 + 0.05]
        t_dm = [t for t, ln in blog if "DEAD-MAN" in ln and s0 <= t <= s1 + 0.3]
        late = [f for f in bl if t_dm and f["t_in"] > t_dm[0] + 0.1 and f["t_in"] < s1 and f["cls"] == "STICK"]
        rel = [f["t_in"] for f in bl if f["cls"] == "RELEASE" and t_dm and f["t_in"] >= t_dm[0] - 0.05]
        fs = [f["t_in"] for f in bl if f["cls"] == "FSTHR" and t_dm and f["t_in"] >= t_dm[0] - 0.05]
        res_after = [f["t_in"] for f in br if f["cls"] == "STICK" and f["t_in"] >= s1]
        fresh = [t for t, ln in blog if "input fresh" in ln and t >= s1 - 0.05]
        dm.append({"stall": (round(s0, 3), round(s1, 3)), "deadman_at": round(t_dm[0] - s0, 3) if t_dm else None, "throttle_fs_frames": len(fs),
                   "release_frames": len(rel), "release_end": round(max(rel) - s0, 3) if rel else None, "sticks_after_deadman": len(late),
                   "frames_after_release": len([f for f in bl if rel and f["t_in"] > max(rel) + 0.05 and f["t_in"] < s1]),
                   "resumed_after_s": round(res_after[0] - s1, 3) if res_after else None, "fresh_after_s": round(fresh[0] - s1, 3) if fresh else None})
    res["deadman"] = dm
    return res
