#!/usr/bin/env python3
"""FC-loss watchdog for gs-mavlink (docs/GS-MAVLINK.md): mavp2p stays ALIVE when the FC (or the radio link to it) goes away, so systemd's
Restart=on-failure never fires. This process registers at the router's GCS UDP port as a harmless client, watches for HEARTBEATs of the FC,
writes a state file and, when the FC stays silent for a long time, asks systemd to restart gs-mavlink (rate limited).

Safety: the only MAVLink it ever sends is its own HEARTBEAT (MAV_TYPE_ONBOARD_CONTROLLER, autopilot INVALID, sysid WD_HB_SYSID). That is not a GCS
for the FC: ArduPilot's GCS failsafe counts heartbeats of MAV_GCS_SYSID only, and sysid 255/3/125 are refused here. It never sends commands.
The restart argv is fixed ("systemctl restart gs-mavlink.service"); it is NOT read from a config file (--restart-argv-json exists for tests).
Stdlib only. Exit: 0 on SIGTERM/SIGINT, 2 bad arguments.
"""
import argparse
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time

HEARTBEAT_ID, HB_CRC_EXTRA = 0, 50          # cfg-ok: MAVLink common.xml message id and CRC_EXTRA of HEARTBEAT
MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID = 18, 8   # cfg-ok: MAVLink enums
MAV_TYPE_GCS = 6                            # cfg-ok: MAVLink enum
FORBIDDEN_SYSIDS = (255, 3, 125)            # cfg-ok: GCS default, wfb-ng injected, mavp2p heartbeat default


def crc_x25(data, crc=0xFFFF):
    for b in data:
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def build_heartbeat(sysid, seq=0):
    payload = struct.pack("<IBBBBB", 0, MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID, 0, 4, 3)   # cfg-ok: base_mode 0, STANDBY, version 3
    hdr = struct.pack("<BBBBBB", len(payload), 0, 0, seq & 0xFF, sysid, 1) + bytes([HEARTBEAT_ID, 0, 0])
    crc = crc_x25(hdr + payload)
    crc = crc_x25(bytes([HB_CRC_EXTRA]), crc)
    return b"\xfd" + hdr + payload + struct.pack("<H", crc)


def _boundary(buf, j):
    """True when position j is the end of the buffer or the start of another frame (frames are back to back in a datagram)."""
    return j >= len(buf) or buf[j] in (0xFD, 0xFE)


def parse_heartbeats(buf):
    """Yield (sysid, compid, mav_type, autopilot) of every valid HEARTBEAT (MAVLink 1 and 2, CRC checked) found in buf; garbage is skipped."""
    i, n = 0, len(buf)
    while i < n:
        m = buf[i]
        if m == 0xFD and i + 12 <= n:                 # MAVLink 2 (the signature, if any, follows the CRC)
            ln, incompat = buf[i + 1], buf[i + 2]
            total = 12 + ln + (13 if incompat & 1 else 0)
            if i + total > n:
                break
            msgid = buf[i + 7] | (buf[i + 8] << 8) | (buf[i + 9] << 16)
            if msgid == HEARTBEAT_ID:
                body = buf[i + 1:i + 10 + ln]
                crc = crc_x25(bytes([HB_CRC_EXTRA]), crc_x25(body))
                if crc == struct.unpack("<H", buf[i + 10 + ln:i + 12 + ln])[0]:
                    pl = buf[i + 10:i + 10 + ln] + b"\x00" * (9 - ln)   # v2 truncates trailing zero bytes
                    yield buf[i + 5], buf[i + 6], pl[4], pl[5]
                else:
                    i += 1                                  # a bad HEARTBEAT: resynchronise byte by byte
                    continue
            elif not _boundary(buf, i + total):
                i += 1                                      # not a frame (the next byte is no frame start): garbage that looked like a header
                continue
            i += total
        elif m == 0xFE and i + 8 <= n:                # MAVLink 1
            ln = buf[i + 1]
            total = 8 + ln
            if i + total > n:
                break
            if buf[i + 5] == HEARTBEAT_ID and ln == 9:    # cfg-ok: HEARTBEAT payload length in MAVLink 1
                crc = crc_x25(bytes([HB_CRC_EXTRA]), crc_x25(buf[i + 1:i + 6 + ln]))
                if crc == struct.unpack("<H", buf[i + 6 + ln:i + 8 + ln])[0]:
                    yield buf[i + 3], buf[i + 4], buf[i + 6 + 4], buf[i + 6 + 5]
                else:
                    i += 1
                    continue
            elif not _boundary(buf, i + total):
                i += 1
                continue
            i += total
        else:
            i += 1


class Watch:
    """State machine (pure, time passed in): waiting -> up <-> lost; restart only after restart_after_s of silence, rate limited."""

    def __init__(self, t0, loss_s, restart_after_s, max_restarts_per_h):
        self.t0, self.loss_s, self.restart_after_s, self.max_per_h = t0, loss_s, restart_after_s, max_restarts_per_h
        self.last_hb = None
        self.state = "waiting"
        self.lost_since = None
        self.restarts = []          # timestamps of the restarts asked for
        self.restart_pending = False

    def heartbeat(self, now):
        self.last_hb = now
        if self.state != "up":
            self.state, self.lost_since, self.restart_pending = "up", None, False
            return "fc-up"
        return None

    def silence_start(self):
        return self.last_hb if self.last_hb is not None else self.t0

    def tick(self, now):
        """-> list of events: 'fc-lost', 'restart' (the caller performs it), 'restart-suppressed'."""
        ev = []
        silent = now - self.silence_start()
        if self.state != "lost" and silent >= self.loss_s:
            self.state, self.lost_since = "lost", self.silence_start() + self.loss_s
            ev.append("fc-lost")
        if self.state == "lost" and not self.restart_pending and silent >= self.restart_after_s:
            self.restarts = [t for t in self.restarts if now - t < 3600]     # cfg-ok: one hour window
            if len(self.restarts) < self.max_per_h:
                self.restarts.append(now)
                self.restart_pending = True
                ev.append("restart")
            else:
                self.restart_pending = True       # do not log the suppression every tick
                ev.append("restart-suppressed")
        return ev

    def snapshot(self, now):
        return {"state": self.state, "last_hb_age_s": None if self.last_hb is None else round(now - self.last_hb, 2),
                "lost_since_age_s": None if self.lost_since is None else round(now - self.lost_since, 2),
                "restarts_last_hour": len([t for t in self.restarts if now - t < 3600])}   # cfg-ok: one hour window


def write_state(path, snap):
    if not path:
        return
    d = os.path.dirname(path)
    try:
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        print(f"gs-mavlink-wd: cannot write the state file {path}: {e}", file=sys.stderr)


def log(msg):
    print(f"gs-mavlink-wd: {msg}", file=sys.stderr, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="host:port of a GCS UDP server port of the router (e.g. 127.0.0.1:14560)")
    ap.add_argument("--fc-sysid", type=int, required=True)
    ap.add_argument("--loss-s", type=float, required=True)
    ap.add_argument("--restart-after-s", type=float, required=True)
    ap.add_argument("--max-restarts-per-h", type=int, required=True)
    ap.add_argument("--hb-sysid", type=int, required=True)
    ap.add_argument("--state-file", default="")
    ap.add_argument("--serial-dev", default="", help="optional: report 'device-missing' in the log when this node does not exist")
    ap.add_argument("--restart-argv-json", default='["systemctl", "restart", "gs-mavlink.service"]', help=argparse.SUPPRESS)
    ap.add_argument("--run-for-s", type=float, default=0.0, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if not 1 <= a.fc_sysid <= 255 or a.fc_sysid in (a.hb_sysid,):                  # cfg-ok: sysid range
        ap.error("--fc-sysid must be 1..255 and differ from --hb-sysid")
    if not 1 <= a.hb_sysid <= 255 or a.hb_sysid in FORBIDDEN_SYSIDS:                 # cfg-ok: sysid range
        ap.error(f"--hb-sysid must be 1..255 and not one of {FORBIDDEN_SYSIDS} (GCS / wfb-ng / router heartbeat)")
    if a.loss_s <= 0 or a.restart_after_s < a.loss_s or a.max_restarts_per_h < 0:
        ap.error("need 0 < loss-s <= restart-after-s and max-restarts-per-h >= 0")
    try:
        argv_restart = json.loads(a.restart_argv_json)
        assert isinstance(argv_restart, list) and argv_restart and all(isinstance(x, str) for x in argv_restart)
    except (ValueError, AssertionError):
        ap.error("--restart-argv-json must be a JSON list of strings")
    host, _, port = a.target.rpartition(":")
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:                # cfg-ok: port range
        ap.error("--target must be host:port")
    dest = (host, int(port))

    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(v=True))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    now = time.monotonic()
    w = Watch(now, a.loss_s, a.restart_after_s, a.max_restarts_per_h)
    seq, next_hb, next_state = 0, now, now
    end = now + a.run_for_s if a.run_for_s > 0 else None
    log(f"watching FC sysid {a.fc_sysid} via {a.target}; lost after {a.loss_s:g} s, restart after {a.restart_after_s:g} s (max {a.max_restarts_per_h}/h)")
    while not stop["v"] and (end is None or time.monotonic() < end):
        now = time.monotonic()
        if now >= next_hb:      # keeps this client registered at the router and tells the FC side nothing but 'I exist'
            try:
                sock.sendto(build_heartbeat(a.hb_sysid, seq), dest)
            except OSError:
                pass
            seq, next_hb = seq + 1, now + 1.0
        r, _, _ = select.select([sock], [], [], 0.2)
        if r:
            try:
                data, _addr = sock.recvfrom(4096)       # cfg-ok: max datagram
            except OSError:
                data = b""
            for sysid, compid, mtype, ap_id in parse_heartbeats(data):
                if sysid == a.fc_sysid and compid == 1 and mtype != MAV_TYPE_GCS and ap_id != MAV_AUTOPILOT_INVALID:   # cfg-ok: autopilot component 1
                    if w.heartbeat(time.monotonic()) == "fc-up":
                        log("FC heartbeat present")
        now = time.monotonic()
        for e in w.tick(now):
            if e == "fc-lost":
                miss = f" (device {a.serial_dev} does not exist)" if a.serial_dev and not os.path.exists(a.serial_dev) else ""
                log(f"FC lost: no heartbeat for {now - w.silence_start():.1f} s{miss}")
            elif e == "restart":
                log(f"FC still silent: restarting the router ({' '.join(argv_restart)})")
                try:
                    subprocess.run(argv_restart, timeout=30, check=False)        # cfg-ok: seconds
                except (OSError, subprocess.TimeoutExpired) as ex:
                    log(f"restart command failed: {ex}")
            elif e == "restart-suppressed":
                log(f"FC still silent but {a.max_restarts_per_h} restarts in the last hour were already done: not restarting again")
        if now >= next_state:
            write_state(a.state_file, w.snapshot(now))
            next_state = now + 1.0
    write_state(a.state_file, w.snapshot(time.monotonic()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
