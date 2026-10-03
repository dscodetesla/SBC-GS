"""Scenario events -> channel impairment schedule -> real-time replay plan (pure logic, stdlib only, deterministic).

Input is the output of the stochastic engine (tests/sim/models/scenario_engine.py `events`, schema sbc-gs-degrade-events/1) plus the
model's own loss function. Output is (1) a MODEL-time schedule of segments {t0,t1,silent,loss,delay_ms,jitter_ms,tags} and (2) a
REAL-time replay plan: bad intervals (adapter drop = full silence, burst, shock, stall) are kept, idle stretches are elided and long
silences are truncated to a cap, so a 600 s model session replays in ~20 s of wall clock. Elision/truncation is always reported.

All numbers produced here are SYNTH/UNMEASURED model output (docs/SIM-SCENARIOS.md honesty section); nothing here is a measurement.
"""
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.normpath(os.path.join(HERE, "..", "models"))

# Tunables of the replay (model seconds unless noted). Defaults are SYNTH planning values, not facts.
DEFAULTS = {
    "speed": 1.0,                 # >1 scales schedule durations AND the FC-model time constants by 1/speed (documented limits 1..2)
    "rc_override_time": 3.0,      # ArduPilot RC_OVERRIDE_TIME default (SRC docs/MAVLINK-ROUTER.md), model seconds
    "fs_gcs_enable": 1,
    "fs_gcs_timeout": 5.0,        # apm_fc default (APM_FS_GCS_TIMEOUT_S)
    "rc_fs_timeout": 1.0,         # apm_fc default (APM_RC_FS_TIMEOUT_S)
    "receiver_present": False,    # worst case: the wfb-ng override is the only RC source
    "mav_gcs_sysid": 255,
    "bridge_sysid": 255,
    "fc_sysid": 1,
    "deadman_ms": 300,            # registry TX12_DEADMAN_MS
    "hb_period_s": 1.0,           # registry TX12_HB_PERIOD_S
    "rate_hz": 20.0,              # registry TX12_RATE_HZ
    "release_hold_s": 1.0,        # registry TX12_RELEASE_HOLD_S
    "failsafe_throttle_us": 1000,
    "throttle_ch": 3,
    "pre_s": 2.0, "post_s": 3.0, "quiet_s": 2.0,   # window margins around a bad interval; idle gap kept between windows
    "warmup_s": 3.0,              # REAL seconds of clean link before the first window (bridge learns the FC sysid from its heartbeat)
    "silence_cap_s": None,        # None -> max(fs_gcs_timeout, rc_override_time + rc_fs_timeout) + 2.5
    "budget_s": 20.0,             # real-time budget of the whole replay
    "include_bringup": True,      # the initial adapter bring-up delay is a silence [0, t_bringup)
    "joystick_usb": "independent",  # 'shared': an over-current trip also removes the TX12 joystick (INF/UNVERIFIED, docs/SIM-MODELS.md:188)
    "bad_loss": 0.10, "bad_delay_ms": 50.0,   # a segment is "bad" if its loss exceeds the scenario baseline by bad_loss (or delay by bad_delay_ms)
    "steady_min_s": 5.0,          # REAL seconds of baseline link kept in every replay (loss statistics, handshake)
    "video_pps": 150.0, "video_freeze_gap_s": 0.25,
}


def sched_scale_keys():
    return ("rc_override_time", "fs_gcs_timeout", "rc_fs_timeout")


def effective(P):
    """Real-time constants of the software under test (model constants / speed)."""
    p = dict(DEFAULTS)
    p.update(P or {})
    s = float(p["speed"])
    if not 1.0 <= s <= 2.0:
        raise ValueError("speed must be within 1..2 (the TX12 bridge safety bounds forbid scaling further: deadman >= 50 ms, hold >= 0.5 s)")
    p["rc_override_time_real"] = p["rc_override_time"] / s
    p["fs_gcs_timeout_real"] = p["fs_gcs_timeout"] / s
    p["rc_fs_timeout_real"] = p["rc_fs_timeout"] / s
    p["deadman_ms_real"] = max(50, int(round(p["deadman_ms"] / s)))
    p["hb_period_real"] = max(0.2, p["hb_period_s"] / s)
    p["release_hold_real"] = max(0.5, p["release_hold_s"] / s)
    if p["silence_cap_s"] is None:
        p["silence_cap_s"] = max(p["fs_gcs_timeout"], p["rc_override_time"] + p["rc_fs_timeout"]) + 2.5
    return p


# ---------------------------------------------------------------- events -> effects
def derive_effects(events, T, joystick="independent"):
    """Walk the engine events; return dict(silences, bursts, shocks, stalls, applied, ignored)."""
    ev = sorted(events, key=lambda e: e["t_s"])        # stable: same-instant order is the engine's
    bu_t = 4.0
    for e in ev:
        if e["kind"] == "bringup" and e.get("ok") and e.get("t_s_total"):
            bu_t = float(e["t_s_total"])
            break
    sil, bursts, shocks, stalls = [], [], [], []
    open_drop = None          # (t, cause)
    open_shut = None
    burst_t = None
    latched = False
    applied, ignored = {}, {}

    def cnt(d, k):
        d[k] = d.get(k, 0) + 1

    for e in ev:
        k, t = e["kind"], float(e["t_s"])
        if k == "bringup":
            cnt(applied, k)
            if t == 0.0:
                sil.append((0.0, T if not e.get("ok") else min(T, float(e["t_s_total"])), "bringup" if e.get("ok") else "bringup_fail", False))
            elif not e.get("ok", True):
                sil.append((t, T, "bringup_fail", False))
        elif k == "usb_drop":
            cnt(applied, k)
            if open_drop is None:
                open_drop = (t, "usb_" + str(e.get("reason", "drop")))
        elif k == "usb_latched":
            cnt(applied, k)
            latched = True
        elif k == "usb_stuck":
            cnt(applied, k)
            latched = True
            if open_drop is None:
                open_drop = (t, "usb_stuck")
        elif k == "usb_return":
            cnt(applied, k)
            if open_drop is not None:
                end = T if latched else min(T, t + bu_t)
                sil.append((open_drop[0], end, open_drop[1], open_drop[1] == "usb_overcurrent" and joystick == "shared"))
                open_drop = None
        elif k == "thermal_shutdown":
            cnt(applied, k)
            open_shut = t
        elif k == "thermal_resume":
            cnt(applied, k)
            if open_shut is not None:
                sil.append((open_shut, min(T, t), "thermal_shutdown", False))
                open_shut = None
        elif k == "burst_on":
            cnt(applied, k)
            burst_t = t
        elif k == "burst_off":
            cnt(applied, k)
            if burst_t is not None:
                bursts.append((burst_t, min(T, t)))
                burst_t = None
        elif k == "shock":
            cnt(applied, k)
            shocks.append((t, float(e.get("excess_db", 0.0))))
        elif k == "stall":
            cnt(applied, k)
            stalls.append((t, float(e.get("backlog_ms", 0.0))))
        else:
            cnt(ignored, k)       # throttled / undervoltage / thermal_derate / creep_reset / ...: no effect on the channel
    if open_drop is not None:
        sil.append((open_drop[0], T, open_drop[1], open_drop[1] == "usb_overcurrent" and joystick == "shared"))
    if open_shut is not None:
        sil.append((open_shut, T, "thermal_shutdown", False))
    if burst_t is not None:
        bursts.append((burst_t, T))
    return {"silences": [s for s in sil if s[1] > s[0]], "bursts": bursts, "shocks": shocks, "stalls": stalls,
            "applied": applied, "ignored": ignored, "bringup_s": bu_t}


# ---------------------------------------------------------------- effects -> model-time segments
def build_segments(T, fx, base, loss_fn, shock_tau_s=20.0, catchup_ms_per_s=20.0, include_bringup=True):
    """base = {loss, delay_ms, jitter_ms}; loss_fn(noise_extra_db, burst) -> residual loss (the model's own chain)."""
    iv = []   # (a, b, kind, value)
    for a, b, cause, joy in fx["silences"]:
        if cause.startswith("bringup") and not include_bringup:
            continue
        iv.append((a, b, "silence", cause + ("+joystick" if joy else "")))
    for a, b in fx["bursts"]:
        iv.append((a, b, "burst", loss_fn(0.0, True)))
    for t, ex in fx["shocks"]:
        s = 0.0
        while s < 60.0:
            e = ex * math.exp(-s / max(shock_tau_s, 1e-3))
            if e < 0.3:
                break
            lo = loss_fn(e, False)
            if lo - base["loss"] < 1e-3:
                break
            iv.append((t + s, min(T, t + s + 1.0), "shock", lo))
            s += 1.0
    for t, bl in fx["stalls"]:
        win = max(0.5, bl / max(catchup_ms_per_s, 1e-6))
        iv.append((t, min(T, t + win), "stall", bl))
    pts = sorted({0.0, float(T)} | {max(0.0, min(float(T), x)) for a, b, _k, _v in iv for x in (a, b)})
    segs = []
    for x, y in zip(pts, pts[1:]):
        if y - x < 1e-9:
            continue
        mid = (x + y) / 2.0
        act = [i for i in iv if i[0] <= mid < i[1]]
        silent = any(i[2] == "silence" for i in act)
        loss = base["loss"]
        tags = []
        for i in act:
            if i[2] in ("burst", "shock"):
                loss = 1.0 - (1.0 - loss) * (1.0 - float(i[3]))
            tags.append(i[2] if i[2] != "silence" else "silence:" + i[3])
        delay = base["delay_ms"] + sum(float(i[3]) for i in act if i[2] == "stall")
        seg = {"t0": x, "t1": y, "silent": silent, "loss": 1.0 if silent else min(1.0, max(0.0, loss)),
               "delay_ms": delay, "jitter_ms": base["jitter_ms"], "tags": sorted(set(tags))}
        if segs and all(segs[-1][k] == seg[k] for k in ("silent", "loss", "delay_ms", "jitter_ms", "tags")) and abs(segs[-1]["t1"] - x) < 1e-9:
            segs[-1]["t1"] = y
        else:
            segs.append(seg)
    return segs


def digest(segs):
    blob = json.dumps([[round(s["t0"], 4), round(s["t1"], 4), s["silent"], round(s["loss"], 6), round(s["delay_ms"], 3),
                        round(s["jitter_ms"], 3), s["tags"]] for s in segs], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- model-time -> real-time plan
def _bad_groups(segs, base, P):
    groups = []
    for s in segs:
        bad = s["silent"] or (s["loss"] - base["loss"]) >= P["bad_loss"] or (s["delay_ms"] - base["delay_ms"]) >= P["bad_delay_ms"]
        if not bad:
            continue
        if groups and abs(groups[-1]["m1"] - s["t0"]) < 1e-9:
            g = groups[-1]
            g["m1"] = s["t1"]
            g["silent"] = g["silent"] or s["silent"]
            g["tags"] = sorted(set(g["tags"]) | set(s["tags"]))
        else:
            groups.append({"m0": s["t0"], "m1": s["t1"], "silent": s["silent"], "tags": list(s["tags"])})
    return groups


def rr_end(segs, r):
    return max([r] + [x["r1"] for x in segs])


def make_plan(segs, T, base, P, fx=None):
    """Return the replay plan. P = effective(...) dict. Real time = model time / speed inside kept ranges."""
    sp = P["speed"]
    cap = P["silence_cap_s"]
    groups = _bad_groups(segs, base, P)
    for g in groups:
        ln = g["m1"] - g["m0"]
        g["model_len"] = ln
        g["played_len"] = min(ln, cap)
        g["truncated"] = ln - g["played_len"]
        g["skip"] = (g["m0"] + g["played_len"], g["m1"]) if g["truncated"] > 1e-9 else None
        g["score"] = g["played_len"] * (2.0 if g["silent"] else 1.0)
    # windows around groups
    wins = []
    for g in groups:
        w0, w1 = max(0.0, g["m0"] - P["pre_s"]), min(T, g["m0"] + g["played_len"] + P["post_s"])
        if wins and w0 - wins[-1]["m1"] < P["quiet_s"]:
            wins[-1]["m1"] = max(wins[-1]["m1"], w1)
            wins[-1]["groups"].append(g)
        else:
            wins.append({"m0": w0, "m1": w1, "groups": [g]})
    # the post margin of a truncated group must start after its skipped part: recompute m1 from the cut point
    for w in wins:
        for g in w["groups"]:
            end_kept = g["m1"] if g["skip"] else g["m0"] + g["played_len"]
            w["m1"] = max(w["m1"], min(T, end_kept + P["post_s"]))

    def layout(ws):
        r = 0.0
        plan_segs, outs, elided = [], [], 0.0
        first_silent_at0 = bool(ws) and ws[0]["m0"] == 0.0 and segs and segs[0]["silent"]
        if not first_silent_at0:
            plan_segs.append({"r0": 0.0, "r1": P["warmup_s"], "silent": False, "loss": base["loss"], "delay_ms": base["delay_ms"],
                              "jitter_ms": base["jitter_ms"], "m0": None, "m1": None, "tags": ["warmup"]})
            r = P["warmup_s"]
        for wi, w in enumerate(ws):
            if wi > 0 or (not first_silent_at0):
                gap_m = w["m0"] - (ws[wi - 1]["m1"] if wi > 0 else 0.0)
                elided += max(0.0, gap_m)
                if wi > 0:
                    q = P["quiet_s"] / sp
                    plan_segs.append({"r0": r, "r1": r + q, "silent": False, "loss": base["loss"], "delay_ms": base["delay_ms"],
                                      "jitter_ms": base["jitter_ms"], "m0": ws[wi - 1]["m1"], "m1": w["m0"], "tags": ["elided"]})
                    r += q
            skips = sorted(g["skip"] for g in w["groups"] if g["skip"])
            kept, a = [], w["m0"]
            for s0, s1 in skips:
                if s0 > a:
                    kept.append((a, s0))
                a = max(a, s1)
            if w["m1"] > a:
                kept.append((a, w["m1"]))
            # map model->real piecewise
            mapping = []   # (m_a, m_b, r_a)
            rr = r
            for ka, kb in kept:
                mapping.append((ka, kb, rr))
                rr += (kb - ka) / sp
            for s in segs:
                for ka, kb, ra in mapping:
                    x, y = max(s["t0"], ka), min(s["t1"], kb)
                    if y - x < 1e-9:
                        continue
                    ps = {"r0": ra + (x - ka) / sp, "r1": ra + (y - ka) / sp, "silent": s["silent"], "loss": s["loss"],
                          "delay_ms": s["delay_ms"], "jitter_ms": s["jitter_ms"], "m0": x, "m1": y, "tags": s["tags"]}
                    plan_segs.append(ps)
            for g in w["groups"]:
                def m2r(m):
                    for ka, kb, ra in mapping:
                        if ka - 1e-9 <= m <= kb + 1e-9:
                            return ra + (m - ka) / sp
                    return None
                r0 = m2r(g["m0"])
                r1 = m2r(g["skip"][0] if g["skip"] else g["m1"])
                if r1 is None or r0 is None:
                    r0 = r0 if r0 is not None else r
                    r1 = r0 + g["played_len"] / sp
                outs.append({"m0": g["m0"], "m1": g["m1"], "model_len": g["model_len"], "played_len_model": g["played_len"],
                             "truncated_model_s": g["truncated"], "r0": r0, "r1": r1, "silent": g["silent"], "tags": g["tags"]})
            r = rr
        merged = []
        for s in plan_segs:
            if merged and merged[-1]["tags"] == s["tags"] and abs(merged[-1]["r1"] - s["r0"]) < 1e-9 and merged[-1]["silent"] == s["silent"] \
                    and merged[-1]["loss"] == s["loss"] and merged[-1]["delay_ms"] == s["delay_ms"] and (merged[-1]["m1"] == s["m0"]):
                merged[-1]["r1"] = s["r1"]
                merged[-1]["m1"] = s["m1"]
            else:
                merged.append(dict(s))
        tail = (T - ws[-1]["m1"]) if ws else T
        quiet_real = sum(x["r1"] - x["r0"] for x in merged if all(t in ("warmup", "elided") for t in x["tags"]) or not x["tags"])
        if quiet_real < P["steady_min_s"]:      # always keep some clean-link time so the baseline loss/handshake is measured
            need = P["steady_min_s"] - quiet_real
            merged.append({"r0": rr_end(merged, r), "r1": rr_end(merged, r) + need, "silent": False, "loss": base["loss"], "delay_ms": base["delay_ms"],
                           "jitter_ms": base["jitter_ms"], "m0": None, "m1": None, "tags": ["steady"]})
            r = rr_end(merged, r) + need
        return merged, outs, elided + max(0.0, tail), r

    # budget: drop the lowest-scoring windows (the bring-up window at t=0 is kept if requested) until it fits
    keep = list(wins)
    skipped = []
    plan_segs, outs, elided, real = layout(keep)
    while real > P["budget_s"] and len(keep) > 1:
        cand = [w for w in keep if not (w["m0"] == 0.0 and segs and segs[0]["silent"])] or keep
        worst = min(cand, key=lambda w: (sum(g["score"] for g in w["groups"]), -w["m0"]))   # ties: drop the later window
        keep.remove(worst)
        skipped.append({"m0": worst["m0"], "m1": worst["m1"], "reason": "real-time budget"})
        plan_segs, outs, elided, real = layout(keep)
    replayed_model = sum(s["m1"] - s["m0"] for s in plan_segs if s["m0"] is not None and "elided" not in s["tags"])
    trunc = sum(o["truncated_model_s"] for o in outs)
    return {"base": dict(base), "segs": plan_segs, "outages": [o for o in outs if o["silent"]], "bad": outs, "real_s": real, "elided_model_s": elided, "truncated_model_s": trunc,
            "replayed_model_s": replayed_model, "skipped": skipped, "windows": [[w["m0"], w["m1"]] for w in keep], "model_s": T}


def seg_at(plan_segs, t):
    """Segment of the real-time plan at real time t (None past the end)."""
    lo, hi = 0, len(plan_segs) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s = plan_segs[mid]
        if t < s["r0"]:
            hi = mid - 1
        elif t >= s["r1"]:
            lo = mid + 1
        else:
            return s
    return None


def input_stalls(plan):
    """Real-time intervals where the TX12 joystick is gone (only with joystick_usb=shared)."""
    return [(o["r0"], o["r1"]) for o in plan["outages"] if any(t.endswith("+joystick") for t in o["tags"])]


# ---------------------------------------------------------------- model forecast (calls the project's engine)
def forecast(scenario, seed, duration=None, joystick="independent", include_bringup=True):
    """Run ONE session of the scenario engine (same draw as `scenario_engine.py events --seed N`) and derive the channel schedule."""
    if MODELS not in sys.path:
        sys.path.insert(0, MODELS)
    import common
    import degrade_model as dm
    import relay_from_model as rfm
    import scenario_engine as se
    over = {"duration_s": duration} if duration else {}
    eng = se.Engine(se.load_scenario(scenario), cfg_over=over)
    th, rp = eng.draw(0, seed, False)
    ev = []
    out = dm.run_session(th, eng.cfg, rp, ev)
    th2, rp2 = eng.draw(0, seed, False)
    plan = dm.prepare(th2, eng.cfg, rp2)
    d = eng.cfg["distance_m"]
    g = plan["th"].get
    st0 = {"plin": g("rf.tx_power_dbm"), "p1db": plan["p1db0"], "evm_shift": 0.0, "ant": 0.0, "shadow": 0.0, "noise_extra": 0.0, "burst": False}

    def loss_fn(noise_extra, burst):
        st = dict(st0)
        st["noise_extra"], st["burst"] = noise_extra, burst
        return min(1.0, max(0.0, dm.residual_of(plan, dm.link_eval(plan, d, st)["per"])))

    seg = rfm.segment(plan["th"], d, plan["mcs"], plan["k"], plan["n"])
    base = {"loss": loss_fn(0.0, False), "delay_ms": seg["delay_ms"], "jitter_ms": seg["jitter_ms"]}
    T = float(eng.cfg["duration_s"])
    fx = derive_effects(ev, T, joystick)
    segs = build_segments(T, fx, base, loss_fn, shock_tau_s=g("proc.shock_tau_s"), catchup_ms_per_s=g("timing.catchup_ms_per_s"),
                          include_bringup=include_bringup)
    flags = sorted(k for k, v in out["flags"].items() if v)
    summ = {k: out[k] for k in ("residual", "availability", "down_s", "ttff_s", "freeze", "g2g_ms", "bringup_s") if k in out}
    summ["flags"] = flags
    return {"scenario": scenario, "seed": seed, "T": T, "dt": eng.cfg["dt_s"], "events": ev, "model": summ, "fx": fx, "base": base,
            "segs": segs, "digest": digest(segs), "distance_m": d}
