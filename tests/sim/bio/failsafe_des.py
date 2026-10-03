#!/usr/bin/env python3
"""Discrete-event model of the failsafe timer chain as threshold/refractory units (stdlib, deterministic, no I/O).

Why a second formulation: tests/sim/apm_model.py is a tick-driven, polled model of the ArduPilot side. Here the same
chain is written as event-driven deadline units (a unit is "kicked" by input, fires when the deadline passes without
a kick, clears on the next kick, optional refractory hold-down), which also lets us add the pieces apm_model does not
have: the tx12_bridge dead-man/release state machine, the ELRS receiver failsafe and the alink heartbeat fallback.
The FC-side rules are re-encoded from the SAME sources (docs/MAVLINK-ROUTER.md); agreement with apm_model on shared
event streams (cross_check) is a consistency check of two encodings, NOT evidence about real ArduPilot.
Timer values come from params.json (provenance there). Bridge arithmetic is in integer milliseconds.
"""
import heapq
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RC_RELEASE, RC_IGNORE = 0, 65535
MODE_MAP = {1: "RTL", 2: "RTL", 3: "RTL", 4: "LAND", 5: "LAND"}   # same INF map as apm_model.py


def load_timers(path=None):
    with open(path or os.path.join(HERE, "params.json")) as f:
        return {k: v["value"] for k, v in json.load(f)["timers"].items()}


class Engine:
    def __init__(self):
        self.q, self.seq, self.now = [], 0, 0.0

    def at(self, t, fn):
        self.seq += 1
        heapq.heappush(self.q, (t, self.seq, fn))

    def run(self, until):
        while self.q and self.q[0][0] <= until:
            t, _s, fn = heapq.heappop(self.q)
            self.now = t
            fn(t)
        self.now = until


class Unit:
    """Threshold unit: fires `timeout` after the last kick unless kicked again; clears on the next kick.
    refractory: minimum time between two firings (a fire due earlier is delayed to prev_fire + refractory)."""

    def __init__(self, eng, name, timeout, on_fire=None, on_clear=None, refractory=0.0):
        self.eng, self.name, self.timeout = eng, name, timeout
        self.on_fire, self.on_clear, self.refractory = on_fire, on_clear, refractory
        self.ver, self.fired, self.last_fire, self.armed_t = 0, False, None, None

    def kick(self, t):
        self.ver += 1
        self.armed_t = t
        if self.fired:
            self.fired = False
            if self.on_clear:
                self.on_clear(t)
        due = t + self.timeout
        if self.last_fire is not None:
            due = max(due, self.last_fire + self.refractory)
        v = self.ver
        self.eng.at(due, lambda tt, v=v: self._expire(tt, v))

    def cancel(self):
        self.ver += 1

    def _expire(self, t, v):
        if v != self.ver or self.fired:
            return
        self.fired, self.last_fire = True, t
        if self.on_fire:
            self.on_fire(t)


class FcDes:
    """FC-side chain: per-channel override expiry -> 'no override seen' -> RC_FS_TIMEOUT -> radio failsafe;
    GCS heartbeat unit -> GCS failsafe. Output events use the same texts as apm_model.ApmModel."""

    def __init__(self, eng, T, *, fs_gcs_enable=0, armed=True, receiver_present=False, mav_gcs_sysid=255, fs_thr_enable=1):
        self.eng, self.T = eng, T
        self.fs_gcs_enable, self.armed, self.receiver_present = fs_gcs_enable, armed, receiver_present
        self.sysid, self.fs_thr_enable = mav_gcs_sysid, fs_thr_enable
        self.events, self.mode, self.gcs_fs, self.rc_fs = [], "STABILIZE", False, False
        self.ovr_expiry = [None] * 8        # absolute expiry time per channel (None = not overridden)
        self.gcs_u = Unit(eng, "gcs_hb", T["fs_gcs_timeout_s"], self._gcs_fire, self._gcs_clear)
        self.none_u = Unit(eng, "no_override", T["rc_override_time_s"], self._none_fire)
        self.rcfs_u = Unit(eng, "rc_fs", T["rc_fs_timeout_s"], self._rcfs_fire)

    def log(self, t, s):
        self.events.append((round(t, 6), s))

    def overridden(self, t):
        return any(e is not None and t <= e for e in self.ovr_expiry)

    # --- inputs
    def heartbeat(self, sysid, t):
        if sysid == self.sysid:                 # only the MAV_GCS_SYSID sender counts (SITL: sysid 125 does not)
            self.gcs_u.kick(t)

    manual_control = heartbeat

    def override(self, sysid, chans, t):
        if sysid != self.sysid or self.T["rc_override_time_s"] <= 0:
            return False
        for i, v in enumerate(chans[:8]):
            if v == RC_IGNORE:
                continue
            self.ovr_expiry[i] = None if v == RC_RELEASE else t + self.T["rc_override_time_s"]
        self.rcfs_u.cancel()
        self.none_u.kick(t)                 # any accepted frame (even all-release) restarts the chain
        if self.rc_fs and self.overridden(t):
            self.rc_fs = False
            self.log(t, "Radio Failsafe Cleared")
        return True

    # --- unit callbacks
    def _gcs_fire(self, t):
        if not self.fs_gcs_enable:
            return
        self.gcs_fs = True
        if self.armed:
            self.mode = MODE_MAP.get(int(self.fs_gcs_enable), "RTL")
        self.log(t, "GCS Failsafe")

    def _gcs_clear(self, t):
        if self.gcs_fs:
            self.gcs_fs = False
            self.log(t, "GCS Failsafe Cleared")   # the mode change is NOT undone

    def _none_fire(self, t):                       # stage A: overrides gone for RC_OVERRIDE_TIME
        self.rcfs_u.kick(t)

    def _rcfs_fire(self, t):                       # stage B: and RC_FS_TIMEOUT more
        if self.armed and not self.receiver_present and not self.rc_fs and not self.overridden(t):
            self.rc_fs = True
            if self.fs_thr_enable:
                self.mode = "RTL"
            self.log(t, "Radio Failsafe")


def bridge_events(T, t_stop_ms, *, kind="stop", t_end_ms=60000, t_resume_ms=None, sysid=255):
    """Frames/heartbeats the tx12_bridge would emit. Mirrors bench/tx12_bridge.py main loop (REPO) in integer ms.
    kind='stop'  : the stick source stops at t_stop_ms; bridge process lives on (dead-man path; heartbeat continues).
    kind='kill'  : the bridge process dies at t_stop_ms (no exit frames, no heartbeat; SIGKILL/power loss).
    t_resume_ms  : for kind='kill', a new bridge starts at that time (sticks flow again)."""
    tick = int(round(1000.0 / T["bridge_rate_hz"]))
    dm, hold = int(T["deadman_ms"]), int(round(T["release_hold_s"] * 1000))
    nfs, hb = int(T["throttle_fs_frames"]), int(round(T["bridge_hb_period_s"] * 1000))
    ev, state, t_rel, rel_n, last_hb = [], "ACTIVE", 0, 0, -10 ** 9
    sticks, fsf, rel = [1500, 1500, 1500, 1500] + [RC_IGNORE] * 4, [1500, 1500, 1000, 1500] + [RC_IGNORE] * 4, [RC_RELEASE] * 8
    for ms in range(0, t_end_ms + 1, tick):
        if kind == "kill" and t_stop_ms < ms and (t_resume_ms is None or ms < t_resume_ms):
            continue
        if ms - last_hb >= hb:
            last_hb = ms
            ev.append((ms / 1000.0, "hb", sysid, None))
        fresh = ms <= t_stop_ms or (kind == "kill" and t_resume_ms is not None and ms >= t_resume_ms)
        if kind == "kill" and t_resume_ms is not None and ms >= t_resume_ms:
            state = "ACTIVE" if state != "ACTIVE" else state
        if state == "ACTIVE" and not (ms - t_stop_ms <= dm or fresh):
            state, t_rel, rel_n = "RELEASE", ms, 0
        elif state in ("SILENT", "RELEASE") and fresh and kind != "kill":
            state = "ACTIVE"
        if state == "ACTIVE":
            ev.append((ms / 1000.0, "ovr", sysid, sticks))
        elif state == "RELEASE":
            if ms - t_rel >= hold:
                state = "SILENT"
            else:
                ev.append((ms / 1000.0, "ovr", sysid, fsf if rel_n < nfs else rel))
                rel_n += 1
    return ev


def run_des(T, events, until, **fc_kw):
    eng = Engine()
    fc = FcDes(eng, T, **fc_kw)
    for ev in events:
        t, kind, sysid, chans = ev
        if kind == "hb":
            eng.at(t, lambda tt, s=sysid: fc.heartbeat(s, tt))
        elif kind == "manual":
            eng.at(t, lambda tt, s=sysid: fc.manual_control(s, tt))
        else:
            eng.at(t, lambda tt, s=sysid, c=chans: fc.override(s, c, tt))
    eng.run(until)
    return fc


def run_apm(T, events, until, step=0.01, **fc_kw):
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    from apm_model import ApmModel
    m = ApmModel(rc_override_time=T["rc_override_time_s"], rc_fs_timeout=T["rc_fs_timeout_s"],
                 fs_gcs_timeout=T["fs_gcs_timeout_s"], **fc_kw)
    evs = sorted(events, key=lambda e: e[0])
    i, k = 0, 0
    while k * step <= until + 1e-9:
        t = round(k * step, 6)
        while i < len(evs) and evs[i][0] <= t + 1e-9:
            et, kind, sysid, chans = evs[i]
            if kind == "hb":
                m.on_heartbeat(sysid, et)
            elif kind == "manual":
                m.on_manual_control(sysid, et)
            else:
                m.on_rc_override(sysid, chans, et)
            i += 1
        m.tick(t)
        k += 1
    return m


def scenarios(T):
    """name -> (events, until, fc kwargs). Shared by DES and apm_model."""
    en = T["fs_gcs_enable"]
    return {
        "A_stop_noRX": (bridge_events(T, 10000), 30.0, dict(fs_gcs_enable=en, receiver_present=False)),
        "B_stop_RX": (bridge_events(T, 10000), 30.0, dict(fs_gcs_enable=en, receiver_present=True)),
        "C_kill_noRX": (bridge_events(T, 10000, kind="kill"), 30.0, dict(fs_gcs_enable=en, receiver_present=False)),
        "D_kill_RX": (bridge_events(T, 10000, kind="kill"), 30.0, dict(fs_gcs_enable=en, receiver_present=True)),
        "E_kill_noRX_LAND": (bridge_events(T, 10000, kind="kill"), 30.0, dict(fs_gcs_enable=5, receiver_present=False)),
        "F_kill_resume_early": (bridge_events(T, 10000, kind="kill", t_resume_ms=12500), 30.0, dict(fs_gcs_enable=en, receiver_present=False)),
        "G_kill_resume_late": (bridge_events(T, 10000, kind="kill", t_resume_ms=16000), 30.0, dict(fs_gcs_enable=en, receiver_present=False)),
        "H_wrong_sysid": (bridge_events(T, 10000, kind="kill", sysid=77), 30.0, dict(fs_gcs_enable=en, receiver_present=False)),
        "I_stop_noRX_fsgcs0": (bridge_events(T, 10000), 30.0, dict(fs_gcs_enable=0, receiver_present=False)),
    }


def cross_check(T, tol=0.011):
    """Run every shared scenario in both encodings. Returns (rows, all_ok)."""
    rows, ok = [], True
    for name, (ev, until, kw) in scenarios(T).items():
        d, a = run_des(T, ev, until, **kw), run_apm(T, ev, until, **kw)
        de = d.events
        ae = [e for e in a.events if not e[1].startswith("override from")]   # apm_model also logs ignored overrides
        same = len(de) == len(ae) and all(x[1] == y[1] and abs(x[0] - y[0]) <= tol for x, y in zip(de, ae)) and d.mode == a.mode
        ok &= same
        rows.append((name, same, d.events, a.events, d.mode, a.mode))
    return rows, ok


def bridge_facts(T, t_stop_ms=10000):
    """Analytic checks of the bridge state machine (integer ms): dead-man time and frame counts."""
    ev = bridge_events(T, t_stop_ms)
    ovr = [e for e in ev if e[1] == "ovr"]
    after = [e for e in ovr if e[0] > t_stop_ms / 1000.0]
    fs = [e for e in after if e[3][2] == 1000]
    rel = [e for e in after if e[3][0] == RC_RELEASE]
    return {"first_frame_after_stop_s": after[0][0], "throttle_fs_frames": len(fs), "release_frames": len(rel),
            "last_frame_s": ovr[-1][0], "heartbeats_after_stop": sum(1 for e in ev if e[1] == "hb" and e[0] > t_stop_ms / 1000.0 and e[0] <= 30.0)}


def time_to_failsafe_table(T):
    """Seconds from 'control lost' to the first failsafe, per loss pattern (SYNTH for the ELRS term)."""
    out = {}
    # backup only (no receiver): from the last frame
    fc = run_des(T, bridge_events(T, 10000, kind="kill"), 40.0, fs_gcs_enable=T["fs_gcs_enable"], receiver_present=False)
    out["bridge_killed_noRX_radio_fs"] = next(t for t, s in fc.events if s == "Radio Failsafe") - 10.0
    out["bridge_killed_GCS_fs"] = next(t for t, s in fc.events if s == "GCS Failsafe") - 10.0
    fc = run_des(T, bridge_events(T, 10000), 40.0, fs_gcs_enable=T["fs_gcs_enable"], receiver_present=False)
    out["sticks_stop_noRX_radio_fs"] = next(t for t, s in fc.events if s == "Radio Failsafe") - 10.0
    out["sticks_stop_GCS_fs_fires"] = any(s == "GCS Failsafe" for _t, s in fc.events)
    # primary: ELRS link lost -> RX failsafe (SYNTH) -> FC RC_FS_TIMEOUT
    out["elrs_loss_to_fc_radio_fs_SYNTH"] = T["elrs_failsafe_s"] + T["rc_fs_timeout_s"]
    # alink: GS heartbeat lost -> fallback profile after fallback_ms
    eng = Engine()
    fired = []
    u = Unit(eng, "alink", T["alink_fallback_ms"] / 1000.0, on_fire=lambda t: fired.append(t))
    t = 0.0
    while t <= 5.0 + 1e-9:
        eng.at(t, u.kick)
        t = round(t + 0.1, 6)
    eng.run(10.0)
    out["alink_fallback_after_gs_silence_s"] = round(fired[0] - 5.0, 6) if fired else None
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}


def report(T=None):
    T = T or load_timers()
    rows, ok = cross_check(T)
    out = ["# DES vs apm_model cross-check on shared event streams (tolerance 0.011 s = one 0.01 s apm tick)"]
    for name, same, de, ae, dm, am in rows:
        out.append("%-22s %s des=%s mode=%s" % (name, "AGREE" if same else "DISAGREE", [(round(t, 2), s) for t, s in de], dm))
        if not same:
            out.append("%-22s       apm=%s mode=%s" % ("", [(round(t, 2), s) for t, s in ae], am))
    out.append("all agree: %s" % ok)
    out.append("# bridge state machine facts (stick source stops at 10.000 s)")
    for k, v in sorted(bridge_facts(T).items()):
        out.append("%s = %s" % (k, v))
    out.append("# time from control loss to failsafe (s)")
    for k, v in sorted(time_to_failsafe_table(T).items()):
        out.append("%s = %s" % (k, v))
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(report(), end="")
