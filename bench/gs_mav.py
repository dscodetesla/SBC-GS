#!/usr/bin/env python3
"""GS side: MAVLink telemetry monitor + optional RC-over-MAVLink emulator.

RC emulation sends RC_CHANNELS_OVERRIDE (channels 1-4 sweep, 5-7 centred,
channel 8 carries a time token). The air-side emulator echoes the values back
as RC_CHANNELS, which yields a round-trip time for the whole MAVLink chain.

Default matches wfb-ng gs_mavlink `connect://127.0.0.1:14550`: we listen on that
UDP port and reply to whoever sends to us.

Tunables (rate, neutral/sweep values, periods, sysid) are registry keys GSMAV_* (config/registry.tsv, docs/CONFIG.md);
CLI flag > environment SBC_GS_GSMAV_* > /config/sbc-gs.env > profile > default. SAFETY keys have hard bounds (--i-know).

SAFETY: this is bench tooling. Do not connect it to a real vehicle with
propellers fitted. On exit it releases all channels (0 = release, per ArduPilot
docs) a few times.
"""
import argparse
import importlib.util
import math
import os
import signal
import statistics
import sys
import time

from pymavlink import mavutil

MAV = mavutil.mavlink
TOKEN_MOD = 60000   # cfg-ok: RTT token modulus, both ends are this file
IGNORE = 65535      # cfg-ok: ArduPilot: field is ignored


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


def token(now):
    return int(now * 1000) % TOKEN_MOD + 1   # 1..60000, never 0 / 65535


def parse_args(argv=None):
    cfg = CFGLIB.load(["gs_mav"], argv=sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--conn", default=cfg["GSMAV_CONN"])
    ap.add_argument("--rc", choices=["off", "sweep", "hold"], default="off")
    ap.add_argument("--rate", type=float, default=cfg["GSMAV_RATE_HZ"], help="RC override rate, Hz")
    ap.add_argument("--duration", type=float, default=0, help="0 = until Ctrl+C")
    ap.add_argument("--confirm-props-off", action="store_true",
                    help="REQUIRED with --rc sweep|hold: you confirm no propellers are fitted and no battery/ESC is connected")
    ap.add_argument("--real-fc", action="store_true",
                    help="real flight controller: override ONLY channels 1-4 (5-8 = 65535 = ignore) and disable the RTT token")
    ap.add_argument("--selftest", action="store_true",
                    help="exit 1 unless heartbeat (and RC echo if --rc on) was seen")
    ap.add_argument("--i-know", action="store_true",
                    help="allow values beyond the hard bounds of SAFETY keys (config/registry.tsv); logged loudly. Same as SBC_GS_I_KNOW=1")
    a = ap.parse_args(argv)
    if a.rc != "off" and not a.confirm_props_off:
        ap.error("--rc sweep|hold sends RC overrides: add --confirm-props-off (bench only, propellers removed)")
    try:
        cfg.check_cli("GSMAV_RATE_HZ", a.rate)
    except CFGLIB.ConfigError as e:
        ap.error(f"--rate: {e}")
    a.cfg = cfg
    return a


def main():
    a = parse_args()
    cfg = a.cfg
    sysid, comp = cfg["GSMAV_SYSID"], cfg["GSMAV_COMPONENT_ID"]
    hb_period, stat_period, loop_sleep = cfg["GSMAV_HB_PERIOD_S"], cfg["GSMAV_STAT_PERIOD_S"], cfg["GSMAV_LOOP_SLEEP_S"]
    neutral, amp = cfg["GSMAV_NEUTRAL_US"], cfg["GSMAV_SWEEP_AMP_US"]
    rtt_window = cfg["GSMAV_RTT_WINDOW"]

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # run the release-channels cleanup on SIGTERM too
    m = mavutil.mavlink_connection(a.conn, source_system=sysid, source_component=comp)
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
            if now - last_hb >= hb_period:
                last_hb = now
                m.mav.heartbeat_send(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, MAV.MAV_STATE_ACTIVE)
            if a.rc != "off" and target is not None and now - last_rc >= 1.0 / a.rate:
                last_rc = now
                ph = now - t0
                if a.rc == "sweep":
                    ch = [int(neutral + amp * math.sin(ph + i)) for i in range(4)]
                else:
                    ch = [neutral] * 4
                if a.real_fc:   # 65535 = ignore field (ArduPilot docs); keeps mode/aux channels untouched
                    m.mav.rc_channels_override_send(target, 1, *ch, IGNORE, IGNORE, IGNORE, IGNORE)
                else:
                    m.mav.rc_channels_override_send(target, 1, *ch, neutral, neutral, neutral, token(time.time()))
            while True:
                msg = m.recv_match(blocking=False)
                if msg is None:
                    break
                t = msg.get_type()
                counts[t] = counts.get(t, 0) + 1
                if t == "HEARTBEAT" and msg.get_srcSystem() != sysid:
                    seen_hb = True
                    target = msg.get_srcSystem()
                elif t == "RC_CHANNELS" and msg.chan8_raw not in (0, IGNORE):
                    rtts.append((token(time.time()) - msg.chan8_raw) % TOKEN_MOD)
                elif t == "STATUSTEXT":
                    print(f"[gs_mav] STATUSTEXT: {msg.text}", flush=True)
            if now - last_print >= stat_period:
                last_print = now
                rtt = (f"RTT ms min/avg/max = {min(rtts[-rtt_window:])}/{statistics.mean(rtts[-rtt_window:]):.0f}/{max(rtts[-rtt_window:])}"
                       if rtts else "RTT n/a")
                print(f"[gs_mav] +{now - t0:5.1f}s sysid={target} msgs={dict(sorted(counts.items()))} {rtt}", flush=True)
            time.sleep(loop_sleep)
    except KeyboardInterrupt:
        pass
    finally:
        if a.rc != "off" and target is not None:
            for _ in range(cfg["GSMAV_RELEASE_REPEATS"]):          # release channels
                m.mav.rc_channels_override_send(target, 1, 0, 0, 0, 0, 0, 0, 0, 0)   # 0 = release
                time.sleep(cfg["GSMAV_RELEASE_INTERVAL_S"])

    if a.selftest:
        ok = seen_hb and (a.rc == "off" or len(rtts) > 0)
        print(f"[gs_mav] selftest {'PASS' if ok else 'FAIL'}: heartbeat={seen_hb} rc_echoes={len(rtts)}", flush=True)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
