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
import math
import time

from pymavlink import mavutil

MAV = mavutil.mavlink


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--conn", default="udpout:127.0.0.1:14550")
    ap.add_argument("--sysid", type=int, default=1, help="avoid 3 (wfb-ng injects sysid 3)")
    ap.add_argument("--rc-override-time", type=float, default=3.0, help="RC_OVERRIDE_TIME (docs default 3 s)")
    ap.add_argument("--gcs-timeout", type=float, default=5.0, help="FS_GCS_TIMEOUT (docs default 5 s)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run forever)")
    return ap.parse_args()


def main():
    a = parse_args()
    m = mavutil.mavlink_connection(a.conn, source_system=a.sysid, source_component=1)
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
        if now - last_slow >= 1.0:
            last_slow = now
            m.mav.heartbeat_send(MAV.MAV_TYPE_QUADROTOR, MAV.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                 MAV.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 0, MAV.MAV_STATE_ACTIVE)
            m.mav.sys_status_send(0, 0, 0, 100, 15800, 1200, 87, 0, 0, 0, 0, 0, 0)
        if now - last_fast >= 0.1:
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
                m.mav.rc_channels_send(ms(), 8, *rc, *([65535] * 10), 255)
            elif t == "MANUAL_CONTROL":
                counts["manual_control"] += 1
            elif t == "HEARTBEAT" and msg.get_srcSystem() == 255:
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

        if now - last_report >= 5.0:
            last_report = now
            print(f"[fake_fc] rx counts {counts} rc={rc if rc_active else 'inactive'}", flush=True)
        time.sleep(0.005)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
