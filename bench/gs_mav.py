#!/usr/bin/env python3
"""GS side: MAVLink telemetry monitor + optional RC-over-MAVLink emulator.

RC emulation sends RC_CHANNELS_OVERRIDE (channels 1-4 sweep, 5-7 centred,
channel 8 carries a time token). The air-side emulator echoes the values back
as RC_CHANNELS, which yields a round-trip time for the whole MAVLink chain.

Default matches wfb-ng gs_mavlink `connect://127.0.0.1:14550`: we listen on that
UDP port and reply to whoever sends to us.

SAFETY: this is bench tooling. Do not connect it to a real vehicle with
propellers fitted. On exit it releases all channels (0 = release, per ArduPilot
docs) a few times.
"""
import argparse
import math
import statistics
import sys
import time

from pymavlink import mavutil

MAV = mavutil.mavlink
TOKEN_MOD = 60000


def token(now):
    return int(now * 1000) % TOKEN_MOD + 1   # 1..60000, never 0 / 65535


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--conn", default="udpin:127.0.0.1:14550")
    ap.add_argument("--rc", choices=["off", "sweep", "hold"], default="off")
    ap.add_argument("--rate", type=float, default=20.0, help="RC override rate, Hz")
    ap.add_argument("--duration", type=float, default=0, help="0 = until Ctrl+C")
    ap.add_argument("--confirm-props-off", action="store_true",
                    help="REQUIRED with --rc sweep|hold: you confirm no propellers are fitted and no battery/ESC is connected")
    ap.add_argument("--selftest", action="store_true",
                    help="exit 1 unless heartbeat (and RC echo if --rc on) was seen")
    a = ap.parse_args()
    if a.rc != "off" and not a.confirm_props_off:
        ap.error("--rc sweep|hold sends RC overrides: add --confirm-props-off (bench only, propellers removed)")

    m = mavutil.mavlink_connection(a.conn, source_system=255, source_component=190)
    t0 = time.monotonic()
    counts, rtts = {}, []
    target = None
    last_hb = last_rc = last_print = 0.0
    seen_hb = False

    print(f"[gs_mav] {a.conn} rc={a.rc}", flush=True)
    try:
        while True:
            now = time.monotonic()
            if a.duration and now - t0 > a.duration:
                break
            if now - last_hb >= 1.0:
                last_hb = now
                m.mav.heartbeat_send(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, MAV.MAV_STATE_ACTIVE)
            if a.rc != "off" and target is not None and now - last_rc >= 1.0 / a.rate:
                last_rc = now
                ph = now - t0
                if a.rc == "sweep":
                    ch = [int(1500 + 400 * math.sin(ph + i)) for i in range(4)]
                else:
                    ch = [1500] * 4
                m.mav.rc_channels_override_send(target, 1, *ch, 1500, 1500, 1500, token(time.time()))
            while True:
                msg = m.recv_match(blocking=False)
                if msg is None:
                    break
                t = msg.get_type()
                counts[t] = counts.get(t, 0) + 1
                if t == "HEARTBEAT" and msg.get_srcSystem() != 255:
                    seen_hb = True
                    target = msg.get_srcSystem()
                elif t == "RC_CHANNELS" and msg.chan8_raw not in (0, 65535):
                    rtts.append((token(time.time()) - msg.chan8_raw) % TOKEN_MOD)
                elif t == "STATUSTEXT":
                    print(f"[gs_mav] STATUSTEXT: {msg.text}", flush=True)
            if now - last_print >= 2.0:
                last_print = now
                rtt = (f"RTT ms min/avg/max = {min(rtts[-40:])}/{statistics.mean(rtts[-40:]):.0f}/{max(rtts[-40:])}"
                       if rtts else "RTT n/a")
                print(f"[gs_mav] +{now - t0:5.1f}s sysid={target} msgs={dict(sorted(counts.items()))} {rtt}", flush=True)
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        if a.rc != "off" and target is not None:
            for _ in range(5):          # release channels
                m.mav.rc_channels_override_send(target, 1, 0, 0, 0, 0, 0, 0, 0, 0)
                time.sleep(0.05)

    if a.selftest:
        ok = seen_hb and (a.rc == "off" or len(rtts) > 0)
        print(f"[gs_mav] selftest {'PASS' if ok else 'FAIL'}: heartbeat={seen_hb} rc_echoes={len(rtts)}", flush=True)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
