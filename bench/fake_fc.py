#!/usr/bin/env python3
"""Air node: minimal ArduPilot-like flight-controller EMULATOR (not ArduPilot).

Sends telemetry (HEARTBEAT, SYS_STATUS, ATTITUDE, GPS_RAW_INT, VFR_HUD,
GLOBAL_POSITION_INT), receives RC_CHANNELS_OVERRIDE / MANUAL_CONTROL and echoes
the received channels back as RC_CHANNELS so the ground side can measure the
round-trip. It also logs two failsafe-style conditions modelled on ArduPilot
docs (RC override timeout, GCS heartbeat timeout). Behaviour is an emulation of
the documented timeouts only.

Default connection matches wfb-ng drone_mavlink `listen://0.0.0.0:14550`:
this process sends to 127.0.0.1:14550 and wfb-ng replies to our source port.
"""
import argparse
import importlib.util
import math
import os
import sys
import time

from pymavlink import mavutil

MAV = mavutil.mavlink
IGNORE = 65535   # cfg-ok: ArduPilot: field is ignored


def _load_cfg_module():
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(here, "..", "config"), os.path.join(here, "config")):
        p = os.path.join(d, "load.py")
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("sbc_gs_load", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("config/load.py not found (expected ../config next to bench/)")


CFGLIB = _load_cfg_module()


def parse_args(argv=None):
    cfg = CFGLIB.load(["fake_fc"], argv=sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] + "\nDefaults come from config/registry.tsv (FAKEFC_*, FC_SYSID; docs/CONFIG.md).")
    ap.add_argument("--conn", default=cfg["FAKEFC_CONN"])
    ap.add_argument("--sysid", type=int, default=cfg["FC_SYSID"], help="avoid 3 (wfb-ng injects sysid 3)")
    ap.add_argument("--rc-override-time", type=float, default=cfg["FAKEFC_RC_OVERRIDE_TIME_S"], help="RC_OVERRIDE_TIME (docs default 3 s)")
    ap.add_argument("--gcs-timeout", type=float, default=cfg["FAKEFC_GCS_TIMEOUT_S"], help="FS_GCS_TIMEOUT (docs default 5 s)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run forever)")
    a = ap.parse_args(argv)
    a.cfg = cfg
    return a


def main():
    a = parse_args()
    cfg = a.cfg
    gcs_sysid, slow_s, fast_s = cfg["FAKEFC_GCS_SYSID"], cfg["FAKEFC_SLOW_PERIOD_S"], cfg["FAKEFC_FAST_PERIOD_S"]
    report_s, loop_sleep = cfg["FAKEFC_REPORT_PERIOD_S"], cfg["FAKEFC_LOOP_SLEEP_S"]
    m = mavutil.mavlink_connection(a.conn, source_system=a.sysid, source_component=cfg["FAKEFC_COMPONENT_ID"])
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000) & 0xFFFFFFFF

    rc = [0] * 8            # last override values (chan1..8)
    last_rc = None          # monotonic time of last override
    rc_active = False
    last_gcs_hb = None
    gcs_fs = False
    last_slow = last_fast = 0.0
    counts = {"rc_override": 0, "manual_control": 0, "gcs_hb": 0}
    last_report = t0

    print(f"[fake_fc] {a.conn} sysid={a.sysid}", flush=True)
    while True:
        now = time.monotonic()
        if a.duration and now - t0 > a.duration:
            break

        # --- telemetry ---
        if now - last_slow >= slow_s:
            last_slow = now
            m.mav.heartbeat_send(MAV.MAV_TYPE_QUADROTOR, MAV.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                 MAV.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 0, MAV.MAV_STATE_ACTIVE)
            m.mav.sys_status_send(0, 0, 0, 100, 15800, 1200, 87, 0, 0, 0, 0, 0, 0)
        if now - last_fast >= fast_s:
            last_fast = now
            ph = now - t0
            m.mav.attitude_send(ms(), 0.3 * math.sin(ph), 0.2 * math.sin(ph * 0.7),
                                (ph * 0.2) % (2 * math.pi), 0, 0, 0)
            m.mav.gps_raw_int_send(int(time.time() * 1e6), 3, 500000000, 300000000, 100000,
                                   100, 100, 0, 0, 10)
            m.mav.vfr_hud_send(0, 0, int(math.degrees(ph * 0.2)) % 360, 0, 100.0, 0)
            m.mav.global_position_int_send(ms(), 500000000, 300000000, 100000, 0, 0, 0, 0, 0, 0)

        # --- incoming ---
        while True:
            msg = m.recv_match(blocking=False)
            if msg is None:
                break
            t = msg.get_type()
            if t == "RC_CHANNELS_OVERRIDE":
                counts["rc_override"] += 1
                rc = [msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                      msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw]
                last_rc = time.monotonic()
                if not rc_active:
                    print("[fake_fc] RC override started", flush=True)
                rc_active = True
                m.mav.rc_channels_send(ms(), 8, *rc, *([IGNORE] * 10), 255)
            elif t == "MANUAL_CONTROL":
                counts["manual_control"] += 1
            elif t == "HEARTBEAT" and msg.get_srcSystem() == gcs_sysid:
                counts["gcs_hb"] += 1
                last_gcs_hb = time.monotonic()
                if gcs_fs:
                    print("[fake_fc] GCS heartbeat is back (ArduPilot would stay in failsafe)", flush=True)
                    gcs_fs = False

        # --- failsafe-style conditions (emulated from documented timeouts) ---
        if rc_active and last_rc is not None and now - last_rc > a.rc_override_time:
            print(f"[fake_fc] RC override lost > {a.rc_override_time}s: regular RC would resume / RC failsafe", flush=True)
            m.mav.statustext_send(MAV.MAV_SEVERITY_WARNING, b"RC override timeout")
            rc_active = False
        if last_gcs_hb is not None and not gcs_fs and now - last_gcs_hb > a.gcs_timeout:
            print(f"[fake_fc] no GCS heartbeat > {a.gcs_timeout}s: GCS failsafe would trigger", flush=True)
            m.mav.statustext_send(MAV.MAV_SEVERITY_WARNING, b"GCS failsafe")
            gcs_fs = True

        if now - last_report >= report_s:
            last_report = now
            print(f"[fake_fc] rx counts {counts} rc={rc if rc_active else 'inactive'}", flush=True)
        time.sleep(loop_sleep)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
