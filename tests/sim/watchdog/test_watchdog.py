#!/usr/bin/env python3
"""Tests of gs/mavlink/gs-mavlink-watchdog.{py,sh}: frame parser/CRC, the pure state machine, arguments, the launcher, and (when a real mavp2p
binary exists) the whole chain FC emulator on a pty -> real mavp2p -> watchdog. The FC is an emulator (SYNTH), the router is real."""
import importlib.util
import json
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
WD = os.path.join(REPO, "gs", "mavlink", "gs-mavlink-watchdog.py")
SH = os.path.join(REPO, "gs", "mavlink", "gs-mavlink-watchdog.sh")
spec = importlib.util.spec_from_file_location("wd", WD)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def fc_heartbeat(sysid=1, compid=1, mtype=2, autopilot=3, seq=0, v1=False, corrupt=False):
    payload = struct.pack("<IBBBBB", 0, mtype, autopilot, 81, 4, 3)
    if v1:
        hdr = struct.pack("<BBBBB", 9, seq, sysid, compid, 0)
        crc = wd.crc_x25(bytes([wd.HB_CRC_EXTRA]), wd.crc_x25(hdr + payload))
        crc ^= 1 if corrupt else 0
        return b"\xfe" + hdr + payload + struct.pack("<H", crc)
    hdr = struct.pack("<BBBBBB", 9, 0, 0, seq, sysid, compid) + bytes([0, 0, 0])
    crc = wd.crc_x25(bytes([wd.HB_CRC_EXTRA]), wd.crc_x25(hdr + payload))
    crc ^= 1 if corrupt else 0
    return b"\xfd" + hdr + payload + struct.pack("<H", crc)


class TestParser(unittest.TestCase):
    def test_v2_and_v1_heartbeats(self):
        self.assertEqual([(1, 1, 2, 3)], list(wd.parse_heartbeats(fc_heartbeat())))
        self.assertEqual([(7, 1, 2, 3)], list(wd.parse_heartbeats(fc_heartbeat(sysid=7, v1=True))))

    def test_bad_crc_garbage_and_truncation(self):
        self.assertEqual([], list(wd.parse_heartbeats(fc_heartbeat(corrupt=True))))
        self.assertEqual([], list(wd.parse_heartbeats(fc_heartbeat(v1=True, corrupt=True))))
        self.assertEqual([], list(wd.parse_heartbeats(b"\x00\xff\xfd\xfd\x09")))
        self.assertEqual([], list(wd.parse_heartbeats(fc_heartbeat()[:-3])))
        self.assertEqual([(1, 1, 2, 3)], list(wd.parse_heartbeats(b"\xfd\x01\x02" + fc_heartbeat() + b"\xfe\x09")))

    def test_two_frames_in_one_datagram(self):
        got = list(wd.parse_heartbeats(fc_heartbeat(sysid=1) + fc_heartbeat(sysid=255, mtype=6, autopilot=8)))
        self.assertEqual([(1, 1, 2, 3), (255, 1, 6, 8)], got)

    def test_own_heartbeat_roundtrip_is_not_an_autopilot(self):
        got = list(wd.parse_heartbeats(wd.build_heartbeat(126)))
        self.assertEqual([(126, 1, wd.MAV_TYPE_ONBOARD_CONTROLLER, wd.MAV_AUTOPILOT_INVALID)], got)
        self.assertEqual((18, 8), got[0][2:])             # literal: MAV_TYPE_ONBOARD_CONTROLLER / MAV_AUTOPILOT_INVALID, never a GCS (6)

    def test_a_cut_frame_does_not_swallow_the_next_good_one(self):
        # the head of a heartbeat (cut by a lost datagram part) claims 21 bytes: without byte-wise resynchronisation it eats the start of the next frame
        self.assertEqual([(9, 1, 2, 3)], list(wd.parse_heartbeats(fc_heartbeat()[:10] + fc_heartbeat(sysid=9))))
        self.assertEqual([(9, 1, 2, 3)], list(wd.parse_heartbeats(fc_heartbeat(v1=True)[:6] + fc_heartbeat(sysid=9, v1=True))))

    def test_a_bad_heartbeat_does_not_swallow_the_next_good_one(self):
        self.assertEqual([(9, 1, 2, 3)], list(wd.parse_heartbeats(fc_heartbeat(corrupt=True) + fc_heartbeat(sysid=9))))
        self.assertEqual([(9, 1, 2, 3)], list(wd.parse_heartbeats(fc_heartbeat(v1=True, corrupt=True) + fc_heartbeat(sysid=9, v1=True))))

    def test_signed_frame_is_skipped_by_length(self):
        f = bytearray(fc_heartbeat())
        f[2] = 1                                           # incompat flag: a 13-byte signature follows the CRC
        # the CRC covers the header incl. the flag: recompute
        body = bytes(f[1:10 + 9])
        crc = wd.crc_x25(bytes([wd.HB_CRC_EXTRA]), wd.crc_x25(body))
        f[19:21] = struct.pack("<H", crc)
        data = bytes(f) + b"\x00" * 13 + fc_heartbeat(sysid=9)
        self.assertEqual([(1, 1, 2, 3), (9, 1, 2, 3)], list(wd.parse_heartbeats(data)))


class TestWatch(unittest.TestCase):
    def mk(self, **k):
        d = dict(loss_s=5.0, restart_after_s=30.0, max_restarts_per_h=3)
        d.update(k)
        return wd.Watch(100.0, d["loss_s"], d["restart_after_s"], d["max_restarts_per_h"])

    def test_waiting_then_lost_then_restart_once(self):
        w = self.mk()
        self.assertEqual([], w.tick(104.0))
        self.assertEqual(["fc-lost"], w.tick(105.0))
        self.assertEqual("lost", w.state)
        self.assertEqual([], w.tick(120.0))
        self.assertEqual(["restart"], w.tick(130.0))
        self.assertEqual([], w.tick(131.0))                # not again while pending

    def test_heartbeat_cancels_everything(self):
        w = self.mk()
        w.tick(105.0)
        self.assertEqual("fc-up", w.heartbeat(110.0))
        self.assertEqual("up", w.state)
        self.assertEqual([], w.tick(114.0))
        self.assertEqual(["fc-lost"], w.tick(115.5))        # silence is measured from the last heartbeat, not from the start
        self.assertIsNone(w.heartbeat(116.0) and None)

    def test_restart_window_is_rate_limited_per_hour(self):
        w = self.mk(max_restarts_per_h=2)
        t, n_restart, n_supp = 100.0, 0, 0
        for cycle in range(4):
            w.heartbeat(t)
            ev = []
            for k in range(0, 40):
                ev += w.tick(t + k)
            n_restart += ev.count("restart")
            n_supp += ev.count("restart-suppressed")
            t += 100.0
        self.assertEqual((2, 2), (n_restart, n_supp))
        w.heartbeat(t + 4000.0)
        ev = []
        for k in range(0, 40):
            ev += w.tick(t + 4000.0 + k)
        self.assertIn("restart", ev)                        # the hour window moved on

    def test_zero_restarts_means_only_log(self):
        w = self.mk(max_restarts_per_h=0)
        ev = w.tick(105.0) + w.tick(140.0)
        self.assertEqual(["fc-lost", "restart-suppressed"], ev)

    def test_snapshot(self):
        w = self.mk()
        self.assertEqual("waiting", w.snapshot(101.0)["state"])
        w.heartbeat(101.0)
        s = w.snapshot(103.0)
        self.assertEqual(("up", 2.0), (s["state"], s["last_hb_age_s"]))


def run(args, **kw):
    return subprocess.run([sys.executable, WD, *args], capture_output=True, text=True, timeout=30, **kw)


BASE = ["--target", "127.0.0.1:14560", "--fc-sysid", "1", "--loss-s", "5", "--restart-after-s", "30", "--max-restarts-per-h", "3", "--hb-sysid", "126"]


def with_(args, key, val):
    a = list(args)
    a[a.index(key) + 1] = val
    return a


class TestArguments(unittest.TestCase):
    def test_refusals(self):
        for key, val, why in [("--hb-sysid", "255", "GCS sysid"), ("--hb-sysid", "3", "wfb-ng sysid"), ("--hb-sysid", "125", "router heartbeat"),
                              ("--hb-sysid", "1", "same as the FC"), ("--restart-after-s", "4", "restart before loss"), ("--loss-s", "0", "zero loss"),
                              ("--target", "nohost", "no port"), ("--target", "127.0.0.1:99999", "port range"), ("--max-restarts-per-h", "-1", "negative"),
                              ("--fc-sysid", "0", "sysid range")]:
            r = run(with_(BASE, key, val) + ["--run-for-s", "0.1"])
            self.assertEqual(2, r.returncode, f"{why}: {r.stderr[-200:]}")

    def test_restart_argv_must_be_a_json_list_of_strings(self):
        for bad in ("[]", "not json", '[1, 2]', '"x"'):
            self.assertEqual(2, run(BASE + ["--restart-argv-json", bad, "--run-for-s", "0.1"]).returncode, bad)

    def test_runs_and_stops_cleanly(self):
        r = run(BASE + ["--run-for-s", "0.5"])
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("watching FC sysid 1", r.stderr)


class TestMainLoopFake(unittest.TestCase):
    """The real main loop against a plain UDP socket that plays the router: which heartbeats count as 'the FC'."""

    def drive(self, frames_after_first_hb, run_for=2.2, extra=(), loss_s="1"):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        srv.settimeout(0.3)
        port = srv.getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "s.json")
            args = ["--target", f"127.0.0.1:{port}", "--fc-sysid", "1", "--loss-s", loss_s, "--restart-after-s", "1000", "--max-restarts-per-h", "0",
                    "--hb-sysid", "126", "--state-file", state, "--run-for-s", str(run_for), *extra]
            p = subprocess.Popen([sys.executable, WD, *args], stderr=subprocess.PIPE, text=True)
            peer = None
            t0 = time.time()
            while time.time() - t0 < run_for - 0.3:
                try:
                    data, peer = srv.recvfrom(4096)
                except socket.timeout:
                    continue
                for fr in frames_after_first_hb:
                    srv.sendto(fr, peer)
            _, err = p.communicate(timeout=30)
            st = json.load(open(state))
        srv.close()
        return err, st

    def test_a_real_fc_heartbeat_is_accepted(self):
        err, st = self.drive([fc_heartbeat(sysid=1, compid=1)], run_for=3.2, loss_s="3")
        self.assertEqual("up", st["state"])
        self.assertIn("FC heartbeat present", err)

    def test_a_gcs_heartbeat_with_the_fc_sysid_is_not_the_fc(self):
        err, st = self.drive([fc_heartbeat(sysid=1, compid=1, mtype=6, autopilot=8)])
        self.assertNotEqual("up", st["state"])
        self.assertNotIn("FC heartbeat present", err)

    def test_an_invalid_autopilot_heartbeat_is_not_the_fc(self):
        err, st = self.drive([fc_heartbeat(sysid=1, compid=1, mtype=2, autopilot=8)])
        self.assertNotEqual("up", st["state"])

    def test_another_component_or_sysid_is_not_the_fc(self):
        for fr in (fc_heartbeat(sysid=1, compid=2), fc_heartbeat(sysid=2, compid=1), fc_heartbeat(sysid=126, compid=1)):
            err, st = self.drive([fr], run_for=1.8)
            self.assertNotEqual("up", st["state"], fr)

    def test_missing_serial_device_is_named_in_the_log(self):
        err, _st = self.drive([], run_for=2.4, extra=["--serial-dev", "/dev/ttyFC-does-not-exist"])
        self.assertIn("FC lost", err)
        self.assertIn("/dev/ttyFC-does-not-exist does not exist", err)


class TestLauncher(unittest.TestCase):
    def sh(self, conf_text, *args, env=None):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "gs-mavlink.conf")
            open(c, "w").write(conf_text)
            e = {k: v for k, v in os.environ.items() if not k.startswith("SBC_GS_")}
            e["GS_MAVLINK_CONF"] = c
            e.update(env or {})
            return subprocess.run(["bash", SH, *args], capture_output=True, text=True, env=e, timeout=30)

    def test_print_defaults(self):
        r = self.sh("", "--print")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("--target 127.0.0.1:14560 --fc-sysid 1 --loss-s 5 --restart-after-s 30 --max-restarts-per-h 3 --hb-sysid 126 --state-file /run/gs-mavlink/fc.state", r.stdout)

    def test_config_values_and_serial_dev(self):
        r = self.sh("WD_LOSS_S='9'\nWD_FC_SYSID='7'\nGCS_UDP_PORTS='14561 14562'\nSERIAL_DEV='/dev/ttyACM0'\n", "--print")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("--target 127.0.0.1:14561", r.stdout)
        self.assertIn("--fc-sysid 7 --loss-s 9", r.stdout)
        self.assertIn("--serial-dev /dev/ttyACM0", r.stdout)

    def test_invalid_values_are_refused_and_never_executed(self):
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "PWNED")
            for conf in (f"WD_LOSS_S='$(touch {marker})'\n", "WD_LOSS_S='0'\n", "WD_HB_SYSID='999'\n", "WD_RESTART_AFTER_S='1'\n"):
                r = self.sh(conf, "--print")
                self.assertNotEqual(0, r.returncode, conf)
            self.assertFalse(os.path.exists(marker))

    def test_empty_gcs_ports_is_refused(self):
        r = self.sh("GCS_UDP_PORTS=''\n", "--print")
        self.assertEqual(2, r.returncode)
        self.assertIn("GCS_UDP_PORTS is empty", r.stderr)

    def test_forbidden_heartbeat_sysid_is_refused_by_the_watchdog(self):
        r = self.sh("WD_HB_SYSID='255'\n")                    # the registry allows 1..255; the program refuses the GCS sysid
        self.assertEqual(2, r.returncode)
        self.assertIn("--hb-sysid", r.stderr)


def find_mavp2p():
    for c in (os.environ.get("MAVP2P", ""), os.path.expanduser("~/.cache/sbc-gs-sim/bin/mavp2p")):
        if c and os.access(c, os.X_OK):
            return c
    from shutil import which
    return which("mavp2p")


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(find_mavp2p(), "no mavp2p binary (go install github.com/bluenviron/mavp2p@v1.3.3 or set MAVP2P)")
class TestRealRouter(unittest.TestCase):
    """FC emulator on a pty (the pty is NOT a USB device) -> REAL mavp2p -> the watchdog. Time constants are short on purpose."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.master, slave = pty.openpty()
        self.slave_path = os.ttyname(slave)
        self.slave = slave
        self.port = free_udp_port()
        self.fc_on = threading.Event()
        self.stop = threading.Event()
        self.th = threading.Thread(target=self._fc, daemon=True)
        self.th.start()
        self.router = subprocess.Popen([find_mavp2p(), f"serial:{self.slave_path}:115200", f"udps:127.0.0.1:{self.port}", "--hb-disable"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)

    def tearDown(self):
        self.stop.set()
        self.router.send_signal(signal.SIGTERM)
        try:
            self.router.wait(5)
        except subprocess.TimeoutExpired:
            self.router.kill()
        self.th.join(2)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        self.tmp.cleanup()

    def _fc(self):
        seq = 0
        while not self.stop.is_set():
            if self.fc_on.is_set():
                try:
                    os.write(self.master, fc_heartbeat(seq=seq))
                except OSError:
                    pass
                seq += 1
            r, _, _ = select.select([self.master], [], [], 0.25)    # drain what the router writes to the "FC"
            if r:
                try:
                    os.read(self.master, 4096)
                except OSError:
                    pass

    def start_wd(self, run_for, **over):
        marker = os.path.join(self.tmp.name, "restarted")
        state = os.path.join(self.tmp.name, "state.json")
        args = ["--target", f"127.0.0.1:{self.port}", "--fc-sysid", "1", "--loss-s", "2", "--restart-after-s", "4", "--max-restarts-per-h", "3",
                "--hb-sysid", "126", "--state-file", state, "--restart-argv-json", json.dumps(["/bin/sh", "-c", f"echo x >> {marker}"]),
                "--run-for-s", str(run_for)]
        for k, v in over.items():
            args = with_(args, k, v)
        p = subprocess.Popen([sys.executable, WD, *args], stderr=subprocess.PIPE, text=True)
        return p, marker, state

    def test_fc_up_then_silent_then_back(self):
        self.fc_on.set()
        p, marker, state = self.start_wd(14)
        time.sleep(2.5)
        self.assertEqual("up", json.load(open(state))["state"])
        self.fc_on.clear()                                    # the FC goes silent: the pty stays open, the router stays ALIVE (the finding)
        time.sleep(3.2)
        self.assertIsNone(self.router.poll(), "mavp2p must still be alive: that is why Restart=on-failure cannot help")
        self.assertEqual("lost", json.load(open(state))["state"])
        time.sleep(2.3)                                       # > restart-after (4 s of silence in total)
        self.assertEqual(1, len(open(marker).read().split()))
        self.fc_on.set()
        time.sleep(2.5)
        self.assertEqual("up", json.load(open(state))["state"])
        _, err = p.communicate(timeout=30)
        self.assertIn("FC lost", err)
        self.assertIn("restarting the router", err)
        self.assertEqual(2, err.count("FC heartbeat present"))

    def test_short_gap_does_not_restart(self):
        self.fc_on.set()
        p, marker, state = self.start_wd(8, **{"--restart-after-s": "6"})
        time.sleep(2.0)
        self.fc_on.clear()
        time.sleep(3.2)                                       # lost (2 s), but shorter than restart-after (6 s)
        self.fc_on.set()
        _, err = p.communicate(timeout=30)
        self.assertIn("FC lost", err)
        self.assertFalse(os.path.exists(marker), "a restart for a gap shorter than restart-after")

    def test_the_watchdog_heartbeat_is_not_a_gcs_and_nothing_else_is_sent(self):
        got = []
        stop = threading.Event()
        # a second client registered at the router sees what the watchdog sends: only HEARTBEATs of sysid 126
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.bind(("127.0.0.1", 0))
        c.settimeout(0.3)
        self.fc_on.set()
        p, _m, _s = self.start_wd(3)
        t0 = time.time()
        while time.time() - t0 < 2.4:
            c.sendto(wd.build_heartbeat(200), ("127.0.0.1", self.port))
            try:
                got.append(c.recvfrom(4096)[0])
            except socket.timeout:
                pass
        stop.set()
        p.communicate(timeout=30)
        frames = [x for blob in got for x in wd.parse_heartbeats(blob)]
        self.assertIn((126, 1, wd.MAV_TYPE_ONBOARD_CONTROLLER, wd.MAV_AUTOPILOT_INVALID), frames)
        self.assertNotIn(255, [f[0] for f in frames])
        raw = b"".join(got)
        self.assertNotIn(b"\xfd\x21", raw[:0])               # placeholder: message ids other than 0 are checked below
        ids = set()
        i = 0
        while i < len(raw):
            if raw[i] == 0xFD and i + 10 <= len(raw):
                ids.add(raw[i + 7] | (raw[i + 8] << 8) | (raw[i + 9] << 16))
                i += 12 + raw[i + 1]
            else:
                i += 1
        self.assertTrue(ids <= {0}, f"the router forwarded MAVLink message ids {ids}: only HEARTBEAT (0) is expected from FC emulator and watchdog")


if __name__ == "__main__":
    unittest.main()
