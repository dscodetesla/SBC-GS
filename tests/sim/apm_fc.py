#!/usr/bin/env python3
"""UDP front end for apm_model.ApmModel: an ArduPilot-semantics FC stand-in (NOT ArduPilot, NOT for real vehicles).

Compared with bench/fake_fc.py it adds: GCS-sysid filter (MAV_GCS_SYSID/_HI), FS_GCS_ENABLE (default 0!), sticky
failsafe mode, MANUAL_CONTROL counted as GCS liveness, 0/65535 channel semantics, per-channel expiry with fall-back
to a stale 'regular RC' value, RC_OVERRIDE_TIME 0 / negative, radio failsafe when the bridge is the only RC source.
Events are printed as 'EVENT <t>s <text>' (t = seconds since start) so a test can grep them.
Connection default matches wfb-ng drone_mavlink: send to udpout:127.0.0.1:14550.
"""
import argparse
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simcfg  # noqa: E402
from apm_model import ApmModel  # noqa: E402
from pymavlink import mavutil  # noqa: E402

MAV = mavutil.mavlink


def main():
    cfg = simcfg.load(["sim"])   # APM_* keys, docs/CONFIG.md
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--conn", default=cfg["APM_CONN"])
    ap.add_argument("--sysid", type=int, default=cfg["APM_SYSID"])
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--rc-override-time", type=float, default=cfg["APM_RC_OVERRIDE_TIME_S"])
    ap.add_argument("--fs-gcs-enable", type=int, default=cfg["APM_FS_GCS_ENABLE"])
    ap.add_argument("--fs-gcs-timeout", type=float, default=cfg["APM_FS_GCS_TIMEOUT_S"])
    ap.add_argument("--rc-fs-timeout", type=float, default=cfg["APM_RC_FS_TIMEOUT_S"])
    ap.add_argument("--mav-gcs-sysid", type=int, default=cfg["APM_MAV_GCS_SYSID"])
    ap.add_argument("--mav-gcs-sysid-hi", type=int, default=cfg["APM_MAV_GCS_SYSID_HI"])
    ap.add_argument("--disarmed", action="store_true")
    ap.add_argument("--receiver-present", action="store_true", help="a physical RC receiver exists (override loss is then not a radio failsafe)")
    a = ap.parse_args()
    fc = ApmModel(rc_override_time=a.rc_override_time, fs_gcs_enable=a.fs_gcs_enable, fs_gcs_timeout=a.fs_gcs_timeout,
                  mav_gcs_sysid=a.mav_gcs_sysid, mav_gcs_sysid_hi=a.mav_gcs_sysid_hi, armed=not a.disarmed,
                  receiver_present=a.receiver_present, rc_fs_timeout=a.rc_fs_timeout)
    m = mavutil.mavlink_connection(a.conn, source_system=a.sysid, source_component=1)
    t0 = time.monotonic()
    shown = 0
    last_hb = last_rc = 0.0
    print(f"[apm_fc] {a.conn} sysid={a.sysid} model={vars(a)}", flush=True)
    while True:
        t = time.monotonic() - t0
        if a.duration and t > a.duration:
            break
        while True:
            msg = m.recv_match(blocking=False)
            if msg is None:
                break
            ty, src = msg.get_type(), msg.get_srcSystem()
            if ty == "HEARTBEAT":
                fc.on_heartbeat(src, t)
            elif ty == "MANUAL_CONTROL":
                fc.on_manual_control(src, t)
            elif ty == "RC_CHANNELS_OVERRIDE":
                if msg.target_system not in (0, a.sysid):
                    continue   # not for us (the router would not have delivered it)
                was = fc.overridden(t)
                fc.on_rc_override(src, [msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                                        msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw], t)
                if fc.overridden(t) and not was:
                    fc.events.append((t, "RC override started"))
        was_over = fc.overridden(t)
        fc.tick(t)
        if getattr(fc, "_was_over", False) and not fc.overridden(t):
            fc.events.append((t, "RC override expired: regular RC (stale %s) is back" % fc.rc_input[:4]))
        fc._was_over = fc.overridden(t)
        for ev in fc.events[shown:]:
            print(f"EVENT {ev[0]:.2f}s {ev[1]}", flush=True)
            m.mav.statustext_send(MAV.MAV_SEVERITY_WARNING, ev[1].encode()[:50])
        shown = len(fc.events)
        if t - last_hb >= 1.0:
            last_hb = t
            m.mav.heartbeat_send(MAV.MAV_TYPE_QUADROTOR, MAV.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                 MAV.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (0 if a.disarmed else MAV.MAV_MODE_FLAG_SAFETY_ARMED), 0, MAV.MAV_STATE_ACTIVE)
        if t - last_rc >= 0.05:
            last_rc = t
            ch = [fc.channel(i, t) for i in range(8)]
            m.mav.rc_channels_send(int(t * 1000) & 0xFFFFFFFF, 8, *ch, *([65535] * 10), 255)
        time.sleep(0.005)
    print(f"[apm_fc] end mode={fc.mode} gcs_failsafe={fc.gcs_failsafe} rc_failsafe={fc.rc_failsafe}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
