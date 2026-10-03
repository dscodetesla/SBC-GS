#!/usr/bin/env python3
"""Tests of the virtual TX12 against the REAL EvdevSource / bench/tx12_bridge.py (docs/SIM-EVDEV.md).

Layers (each class says what it needs; classes that cannot run here SKIP with a reason, they never pass vacuously):
  TestStatic / TestMapping     no device, no pymavlink: example map vs. EdgeTX descriptor/profile, mapping maths against an independent oracle
  TestSourceFake               EvdevSource in this process on vdev.FakeEvdevModule (SYNTH of the python-evdev API)
  TestMainLoopFake             the real bridge process (UDP loopback, pymavlink) with the fake evdev inside it (fakeproc.py): dead-man, exit code, ...
  TestKernelHid                /dev/uhid: the kernel's own hid-input turns the EdgeTX Classic descriptor into evdev (needs the QEMU guest)
  TestBridgeUinput / Uhid      the real bridge process on a real kernel input device made through /dev/uinput or /dev/uhid, real python-evdev (QEMU guest)
Run: tests/sim/evdev/run.sh --check (offline subset) | all (QEMU guest, everything). Nothing here has seen a real TX12 (HW/UNVERIFIED).
"""
import errno
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)

ROOT = os.environ.get("EVDEV_ROOT") or os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BENCH = os.path.join(ROOT, "bench")
BRIDGE = os.path.join(BENCH, "tx12_bridge.py")
EXAMPLE_MAP = os.path.join(BENCH, "tx12_map.example.json")
sys.path[:0] = [BENCH, HERE]
import tx12_bridge as b  # noqa: E402
import vdev  # noqa: E402

GUEST = os.environ.get("EVDEV_GUEST") == "1"
SLOW = 3.0 if GUEST else 1.0                    # QEMU TCG is several times slower than the host
HAVE_MAV = importlib.util.find_spec("pymavlink") is not None
HAVE_EVDEV = importlib.util.find_spec("evdev") is not None
FS_FRAME = (0, 0, 1000, 0, 0, 0, 0, 0)          # throttle failsafe frame (channel 3 = 1000), everything else released
ZERO = (0,) * 8
IGN = 65535
PROFILE = vdev.load_profile()
# Hard-coded expectations (NOT read back from the files under test, so a damaged map cannot make the oracle agree with it)
CFG_X = dict(lo=0, hi=2047, center=1024, deadband=0.02, reverse=False)
CFG_Y = dict(lo=0, hi=2047, center=1024, deadband=0.02, reverse=True)
CFG_Z = dict(lo=0, hi=2047, center=None, deadband=0.0, reverse=False)


def ref_us(raw, lo, hi, center=None, deadband=0.0, reverse=False):
    """Independent oracle of tx12_bridge.map_axis (written separately on purpose)."""
    raw = min(max(raw, lo), hi)
    if center is None:
        n = (raw - lo) / (hi - lo)
        return int(round(1000 + 1000 * (1 - n if reverse else n)))
    if raw == center:
        n = 0.0
    elif raw > center:
        n = (raw - center) / (hi - center)
    else:
        n = -(center - raw) / (center - lo)
    mag = abs(n)
    if mag <= deadband:
        n = 0.0
    elif deadband:
        mag = (mag - deadband) / (1 - deadband)
        n = mag if n > 0 else -mag
    if reverse:
        n = -n
    return int(round(1500 + 500 * n))


def device_skip(backend):
    """None when this machine can run the kernel-device tests for the back end, else the SKIP reason."""
    if not HAVE_MAV:
        return "pymavlink is not installed"
    if not HAVE_EVDEV:
        return "python-evdev is not installed (the tests of the real EvdevSource need it)"
    ok, why = vdev.available(backend)
    return None if ok else "%s: %s" % (backend, why)


# ------------------------------------------------------------------ static / pure

class TestStatic(unittest.TestCase):
    def test_example_map_ranges_match_edgetx_profile(self):
        """bench/tx12_map.example.json must describe what EdgeTX sends: 0..2047, centre 1024 (SRC usb_joystick.cpp), not the old -1024..1024 placeholder."""
        m = b.load_map(EXAMPLE_MAP)
        self.assertTrue(m)
        for name, cfg in m.items():
            self.assertIn(name, PROFILE["axes"], "axis %s is not an EdgeTX Classic axis" % name)
            ax = PROFILE["axes"][name]
            self.assertEqual((cfg["min"], cfg["max"]), (ax["min"], ax["max"]), name)
            if "center" in cfg:
                self.assertEqual(cfg["center"], ax["value"], "%s: centre is the HID value of channel output 0 (1024)" % name)
        guard = b.load_device_filter(read_json(EXAMPLE_MAP))
        self.assertEqual(guard.get("vendor"), PROFILE["vendor"])
        self.assertEqual(guard.get("product"), PROFILE["product"])

    def test_hid_descriptor_is_classic_edgetx(self):
        """The descriptor bytes copied from EdgeTX parse to exactly what the profile (and so the map) assumes."""
        d = vdev.parse_hid_descriptor(vdev.HID_CLASSIC_DESC)
        self.assertEqual(d["buttons"], 24)
        self.assertEqual(d["bits"] // 8, vdev.HID_CLASSIC_REPORT_LEN)
        self.assertEqual([u for u, *_ in d["axes"]], vdev.HID_AXIS_USAGES)
        self.assertTrue(all((lo, hi, bits) == (0, 2047, 16) for _, lo, hi, bits in d["axes"]))
        self.assertEqual([vdev.ABS_NAME[vdev.hid_usage_to_abs(u)] for u, *_ in d["axes"]], list(PROFILE["axes"]))
        self.assertEqual([vdev.hid_button_code(i) for i in range(24)], PROFILE["button_codes"])

    def test_report_layout(self):
        r = vdev.hid_classic_report([0, 2047, 1024, 1, 2, 3, 4, 5000], buttons=0b101)
        self.assertEqual(len(r), 19)
        self.assertEqual(r[:3], bytes([0b101, 0, 0]))
        self.assertEqual([int.from_bytes(r[3 + 2 * i:5 + 2 * i], "little") for i in range(8)], [0, 2047, 1024, 1, 2, 3, 4, 2047])

    def test_struct_sizes_match_the_kernel_abi(self):
        self.assertEqual(vdev.EVENT_SIZE, 24)
        import struct
        self.assertEqual(struct.calcsize(vdev.UINPUT_SETUP_FMT), 92)       # sizeof(struct uinput_setup), checked with gcc
        self.assertEqual(struct.calcsize(vdev.UINPUT_ABS_FMT), 28)         # sizeof(struct uinput_abs_setup)
        self.assertEqual((vdev.UI_DEV_SETUP, vdev.UI_ABS_SETUP, vdev.UI_DEV_CREATE), (0x405C5503, 0x401C5504, 0x5501))

    def test_abs_table_matches_kernel_header(self):
        h = "/usr/include/linux/input-event-codes.h"
        if not os.path.exists(h):
            self.skipTest("no %s" % h)
        import re
        with open(h) as f:
            src = f.read()
        hdr = dict((m.group(1), int(m.group(2), 0)) for m in re.finditer(r"^#define\s+(ABS_\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", src, re.M))
        for k, v in vdev.ABS.items():
            self.assertEqual(hdr.get(k), v, k)


class TestMapping(unittest.TestCase):
    def cfg(self, c):
        d = {"min": c["lo"], "max": c["hi"]}
        if c["center"] is not None:
            d["center"] = c["center"]
        d["deadband"] = c["deadband"]
        d["reverse"] = c["reverse"]
        return d

    def test_full_sweep_stays_in_range_and_is_monotonic(self):
        for c in (CFG_X, CFG_Y, CFG_Z):
            out = [b.map_axis(raw, self.cfg(c)) for raw in range(0, 2048)]
            self.assertTrue(all(1000 <= v <= 2000 for v in out))
            seq = out if not c["reverse"] else out[::-1]
            self.assertTrue(all(x <= y for x, y in zip(seq, seq[1:])), "non-monotonic")
            self.assertEqual(out, [ref_us(r, **c) for r in range(0, 2048)])

    def test_endpoints_and_centre(self):
        x, y, z = (self.cfg(c) for c in (CFG_X, CFG_Y, CFG_Z))
        self.assertEqual([b.map_axis(r, x) for r in (0, 1024, 2047)], [1000, 1500, 2000])
        self.assertEqual([b.map_axis(r, y) for r in (0, 1024, 2047)], [2000, 1500, 1000])      # reversed
        self.assertEqual([b.map_axis(r, z) for r in (0, 2047)], [1000, 2000])
        self.assertEqual(b.map_axis(1024, z), 1500)

    def test_placeholder_range_is_dangerous_on_the_real_one(self):
        """Evidence for the corrected example map: the old placeholder (-1024..1024, centre 0) reads a CENTRED real stick (1024) as full deflection."""
        old = {"min": -1024, "max": 1024, "center": 0, "deadband": 0.02}
        self.assertEqual(b.map_axis(1024, old), 2000)
        self.assertEqual(b.map_axis(0, old), 1500)          # and the real stick at the bottom reads neutral

    def test_deadband(self):
        x = self.cfg(CFG_X)
        for raw in (1024, 1030, 1024 + 20, 1024 - 20, 1004):
            self.assertEqual(b.map_axis(raw, x), 1500, raw)
        self.assertEqual(b.map_axis(1024 + 21, x), ref_us(1024 + 21, **CFG_X))
        self.assertNotEqual(b.map_axis(1024 + 60, x), 1500)
        # continuity: no jump at the edge of the deadband (a jump would be a step in an actuator command)
        self.assertLessEqual(b.map_axis(1024 + 22, x) - 1500, 2)
        self.assertEqual(b.map_axis(2047, x), 2000)

    def test_inversion(self):
        for raw in (0, 100, 1024, 1500, 2047):
            self.assertEqual(b.map_axis(raw, self.cfg(CFG_X)) + b.map_axis(raw, self.cfg(CFG_Y)), 3000, raw)

    def test_clamp_of_absurd_raw_values(self):
        x = self.cfg(CFG_X)
        self.assertEqual((b.map_axis(5000, x), b.map_axis(-500, x), b.map_axis(10 ** 9, x)), (2000, 1000, 2000))
        self.assertEqual(b.sanitize([2500, 500, None]), [2000, 1000, IGN] + [IGN] * 5)

    def test_load_map_rejects_unsafe_maps(self):
        def bad(axes, **extra):
            p = tempfile.mktemp(suffix=".json")
            try:
                write_json(p, dict(axes=axes, **extra))
                with self.assertRaises(ValueError):
                    b.load_map(p)
            finally:
                if os.path.exists(p):
                    os.unlink(p)
        ok = {"channel": 1, "min": 0, "max": 2047}
        bad({"ABS_X": ok, "ABS_Y": dict(ok)})                                  # two axes on channel 1
        bad({"BTN_SOUTH": ok})                                                 # not an ABS_ code
        bad({"EV_ABS": ok})
        bad({"ABS_X": dict(ok, channel=True)})
        bad({"ABS_X": ok}, device={"vendor": "zzz"})
        bad({"ABS_X": ok}, device={"bogus": 1})
        bad({"ABS_X": ok}, device={})

    def test_device_filter_parsing(self):
        self.assertEqual(b.load_device_filter({"device": {"vendor": "0x1209", "product": 20308, "name": "X"}}),
                         {"vendor": 0x1209, "product": 20308, "name": "X"})
        self.assertEqual(b.load_device_filter({}), {})


class TestCli(unittest.TestCase):
    def test_unwritable_lock_is_a_clean_refusal(self):
        """take_lock() used to raise a traceback (rc 1) on an unusable lock path: the single-writer guarantee is unproven, so refuse cleanly with rc 3."""
        r = subprocess.run([sys.executable, BRIDGE, "--input", "sweep", "--confirm-props-off", "--lock", "/nonexistent-dir/x/lock"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_bad_selector_is_refused_before_any_io(self):
        with FakeModule() as fake:
            fake.plug("/dev/input/event3")
            with self.assertRaises(SystemExit) as cm:
                b.EvdevSource("vendor=1209", b.load_map(EXAMPLE_MAP))
            self.assertIn("bad evdev selector", str(cm.exception))


# ------------------------------------------------------------------ in-process fake evdev

class FakeModule:
    """Context manager: sys.modules['evdev'] = vdev.FakeEvdevModule(...)."""

    def __enter__(self):
        self.saved = sys.modules.get("evdev")
        self.fake = vdev.FakeEvdevModule(PROFILE)
        sys.modules["evdev"] = self.fake.module
        return self.fake

    def __exit__(self, *a):
        if self.saved is None:
            sys.modules.pop("evdev", None)
        else:
            sys.modules["evdev"] = self.saved


def wait_for(cond, timeout=3.0, step=0.01):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return cond()


class TestSourceFake(unittest.TestCase):
    PATH = "/dev/input/event7"

    def setUp(self):
        self.cm = FakeModule()
        self.fake = self.cm.__enter__()
        self.addCleanup(self.cm.__exit__)
        self.dev = self.fake.plug(self.PATH)
        self.mapping = b.load_map(EXAMPLE_MAP)

    def source(self, **kw):
        src = b.EvdevSource(self.PATH, self.mapping, **kw)
        self.addCleanup(src.close)
        return src

    def vals(self, src):
        return src.poll()[0]

    def test_initial_state_is_read_at_open(self):
        self.dev.state[vdev.ABS["ABS_X"]] = 2047
        self.assertEqual(self.vals(self.source())[:4], [2000, 1500, 1500, 1500])

    def test_axes_are_committed_at_syn_report_only(self):
        """One kernel packet = one stick state: X must not be visible before the Y of the same packet (no torn frames)."""
        src = self.source()
        self.dev.raw([(vdev.EV_ABS, vdev.ABS["ABS_X"], 2047)])
        time.sleep(0.25)                                  # the reader has certainly consumed it
        self.assertEqual(self.vals(src)[0], 1500, "ABS_X visible before SYN_REPORT")
        self.dev.raw([(vdev.EV_ABS, vdev.ABS["ABS_Y"], 2047), (vdev.EV_SYN, vdev.SYN_REPORT, 0)])
        self.assertTrue(wait_for(lambda: self.vals(src)[:2] == [2000, 1000]), self.vals(src))

    def test_syn_dropped_resynchronises_from_kernel_state(self):
        """After a client-buffer overrun the kernel drops events, possibly the last change of a still axis; the source must re-read the state."""
        src = self.source()
        self.dev.silently_change(ABS_X=2047, ABS_Z=0)    # changes whose events were dropped
        self.dev.raw([(vdev.EV_SYN, vdev.SYN_DROPPED, 0), (vdev.EV_SYN, vdev.SYN_REPORT, 0)])
        self.assertTrue(wait_for(lambda: self.vals(src)[0] == 2000 and self.vals(src)[2] == 1000), self.vals(src))

    def test_events_between_syn_dropped_and_syn_report_are_ignored(self):
        src = self.source()
        self.dev.state[vdev.ABS["ABS_X"]] = 1024
        self.dev.raw([(vdev.EV_SYN, vdev.SYN_DROPPED, 0)])
        os.write(self.dev.wfd, vdev.packet([(vdev.EV_ABS, vdev.ABS["ABS_X"], 2047)]))   # a stale half packet: the kernel state stays 1024
        time.sleep(0.25)
        self.assertEqual(self.vals(src)[0], 1500)

    def test_unplug_is_seen_at_once(self):
        src = self.source()
        self.dev.destroy()
        self.assertTrue(wait_for(lambda: src.poll() == (None, None), 1.0), "an unplugged device must stop being fresh immediately, not after --deadman-ms")
        self.assertTrue(src.lost)

    def test_eagain_is_not_a_lost_device(self):
        src = self.source()
        self.dev.make_eagain(1)
        time.sleep(0.25)
        self.assertFalse(src.lost)
        self.dev.set(ABS_X=2047)
        self.assertTrue(wait_for(lambda: self.vals(src)[0] == 2000))

    def test_unexpected_reader_exception_is_loud_and_safe(self):
        src = self.source()
        self.dev.fail = ValueError("boom")
        self.dev.set(ABS_X=0)                              # wakes the reader
        self.assertTrue(wait_for(lambda: src.lost, 1.0))
        self.assertEqual(src.poll(), (None, None))

    def test_still_stick_stays_fresh(self):
        """Design (docs/GAPS.md S4): no events from a still stick is NOT a failure; the timestamp keeps moving while the reader runs."""
        src = self.source()
        t1 = src.poll()[1]
        time.sleep(0.4)
        t2 = src.poll()[1]
        self.assertGreater(t2, t1 + 0.2)
        self.assertEqual(self.vals(src), [1500, 1500, 1500, 1500] + [IGN] * 4)

    def test_close_stops_the_reader(self):
        src = b.EvdevSource(self.PATH, self.mapping)
        src.close()
        self.assertFalse(src.thread.is_alive())

    # ---- selection and the identity guard
    def test_selector_by_id_and_name_pick_the_one_match(self):
        other = dict(PROFILE, name="Some Gamepad", vendor=0x046D, product=0xC216)
        self.fake.plug("/dev/input/event3", other)
        for sel, want in (("id=1209:4f54", self.PATH), ("id=046d:c216", "/dev/input/event3"),
                          ("name=OpenTX TX12MK2 Joystick", self.PATH), (self.PATH, self.PATH)):
            self.assertEqual(b.resolve_evdev(sel, sys.modules["evdev"]), want, sel)

    def test_ambiguous_selector_is_refused(self):
        self.fake.plug("/dev/input/event9")                # same name, same ids: two identical radios (or a clone)
        for sel in ("id=1209:4f54", "name=OpenTX TX12MK2 Joystick"):
            with self.assertRaises(SystemExit) as cm:
                b.resolve_evdev(sel, sys.modules["evdev"])
            self.assertIn("ambiguous", str(cm.exception))

    def test_no_match_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            b.resolve_evdev("id=dead:beef", sys.modules["evdev"])
        self.assertIn("no input device matches", str(cm.exception))

    def test_guard_rejects_another_device_at_the_same_path(self):
        other = dict(PROFILE, name="Some Gamepad", vendor=0x046D, product=0xC216)
        self.fake.plug("/dev/input/event3", other)
        guard = b.load_device_filter(read_json(EXAMPLE_MAP))
        with self.assertRaises(SystemExit) as cm:
            b.EvdevSource("/dev/input/event3", self.mapping, guard)
        self.assertIn("not the expected one", str(cm.exception))
        self.source(device_filter=guard)                   # the real one passes
        self.source(device_filter={"name": "OpenTX TX12MK2 Joystick"})
        with self.assertRaises(SystemExit):
            self.source(device_filter={"name": "OpenTX Other"})

    def test_missing_axis_is_refused(self):
        m = dict(self.mapping, ABS_HAT0X={"channel": 5, "min": 0, "max": 2047})
        with self.assertRaises(SystemExit) as cm:
            b.EvdevSource(self.PATH, m)
        self.assertIn("has no axes", str(cm.exception))

    def test_unopenable_path_is_refused_with_a_message(self):
        with self.assertRaises(SystemExit) as cm:
            b.EvdevSource("/dev/input/event99", self.mapping)
        self.assertIn("cannot open", str(cm.exception))
        self.dev.denied = True
        with self.assertRaises(SystemExit) as cm:
            b.EvdevSource(self.PATH, self.mapping)
        self.assertIn("Permission denied", str(cm.exception))


# ------------------------------------------------------------------ the real bridge process

_port = [20000 + (os.getpid() * 37) % 20000]


def next_port():
    _port[0] += 1
    return _port[0]


class Sniffer(threading.Thread):
    """FC stand-in: HEARTBEAT 2 Hz once a peer is known (so the bridge learns the target), records every RC_CHANNELS_OVERRIDE."""

    def __init__(self, port):
        super().__init__(daemon=True)
        from pymavlink import mavutil
        self.m = mavutil.mavlink_connection("udpin:127.0.0.1:%d" % port, source_system=1, source_component=1)
        self.frames, self.lock, self.halt = [], threading.Lock(), False

    def run(self):
        peer, last = False, 0.0
        while not self.halt:
            msg = self.m.recv_match(blocking=True, timeout=0.02)
            now = time.monotonic()
            if msg is not None:
                peer = True
                if msg.get_type() == "RC_CHANNELS_OVERRIDE":
                    with self.lock:
                        self.frames.append((now, msg.target_system, (msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                                                                     msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw)))
            if peer and now - last >= 0.5:
                last = now
                self.m.mav.heartbeat_send(2, 3, 0, 0, 4)

    def snapshot(self):
        with self.lock:
            return list(self.frames)

    def close(self):
        self.halt = True
        self.join(2)
        try:
            self.m.close()
        except Exception:
            pass


class Bridge:
    def __init__(self, rig, spec=None, args=(), mapfile=None, lock=None, deadman=300, user=None, fake=False, plugs=(), name="b"):
        self.rig = rig
        self.logf = os.path.join(rig.tmp, "%s.log" % name)
        self.trace = os.path.join(rig.tmp, "%s.trace" % name)
        cmd = [sys.executable]
        if fake:
            cmd += [os.path.join(HERE, "fakeproc.py")]
            for p in plugs:
                cmd += ["--plug", p]
            cmd += ["--"]
        cmd += [BRIDGE, "--input", spec, "--map", mapfile or EXAMPLE_MAP, "--confirm-props-off", "--conn", "udpout:127.0.0.1:%d" % rig.port,
                "--lock", lock or os.path.join(rig.tmp, "lock"), "--deadman-ms", str(deadman), "--rate", "25", "--max-rate", "50",
                "--tx-trace", self.trace] + list(args)
        if fake:
            del cmd[cmd.index(BRIDGE)]
        kw = {}
        if user is not None:
            kw.update(user=user, group=user)
        out = open(self.logf, "w")
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE if fake else subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, text=True,
                                  env=dict(os.environ, EVDEV_ROOT=ROOT), **kw)
        out.close()
        rig.procs.append(self)

    def text(self):
        try:
            with open(self.logf) as f:
                return f.read()
        except OSError:
            return ""

    def cmd(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def wait_log(self, needle, timeout=20):
        return wait_for(lambda: needle in self.text() or self.p.poll() is not None, timeout * SLOW) and needle in self.text()

    def wait_active(self, timeout=25):
        ok = self.wait_log("input fresh: sending sticks", timeout)
        assert ok, "bridge did not start sending (rc=%s):\n%s" % (self.p.poll(), self.text())

    def wait_exit(self, timeout=15):
        try:
            return self.p.wait(timeout * SLOW)
        except subprocess.TimeoutExpired:
            return None

    def stop(self, sig=signal.SIGTERM):
        if self.p.poll() is None:
            try:
                self.p.send_signal(sig)
                self.p.wait(10)
            except Exception:
                self.p.kill()
        if self.p.stdin:
            try:
                self.p.stdin.close()
            except OSError:
                pass


class Rig(unittest.TestCase):
    """FC stand-in + bridge processes + devices, torn down after each test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="evdev-")
        os.chmod(self.tmp, 0o1777)
        self.port = next_port()
        self.sniff = Sniffer(self.port)
        self.sniff.start()
        self.procs, self.devs = [], []

    def tearDown(self):
        for p in self.procs:
            p.stop(signal.SIGKILL if p.p.poll() is None else signal.SIGTERM)
        for d in self.devs:
            try:
                d.destroy()
            except OSError:
                pass
        self.sniff.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, *a, **kw):
        return Bridge(self, *a, **kw)

    def frames(self, since=0.0):
        return [(t, v) for t, _, v in self.sniff.snapshot() if t >= since]

    def wait_frame(self, pred, timeout=10, since=0.0):
        end = time.monotonic() + timeout * SLOW
        while time.monotonic() < end:
            for t, v in self.frames(since):
                if pred(v):
                    return t, v
            time.sleep(0.02)
        self.fail("no frame matched within %.0f s; last frames: %s" % (timeout * SLOW, [v for _, v in self.frames(since)][-4:]))

    def sticks_after(self, t):
        return [v for tt, v in self.frames(t) if v not in (FS_FRAME, ZERO)]


class TestMainLoopFake(Rig):
    """The real bridge main loop (dead-man, state machine, exit code) with the fake evdev inside the process. Needs pymavlink only."""
    SPEC = "evdev:/dev/input/event7"

    @classmethod
    def setUpClass(cls):
        if not HAVE_MAV:
            raise unittest.SkipTest("pymavlink is not installed")

    def start(self, **kw):
        br = self.bridge(self.SPEC, fake=True, **kw)
        br.wait_active()
        return br

    def test_tracks_the_stick_and_clamps(self):
        br = self.start()
        self.wait_frame(lambda v: v[:4] == (1500, 1500, 1500, 1500) and v[4:] == (IGN,) * 4)
        br.cmd("set ABS_X=2047 ABS_Y=2047 ABS_Z=0 ABS_RX=0")
        self.wait_frame(lambda v: v[:4] == (2000, 1000, 1000, 1000))
        br.cmd("set ABS_X=99999 ABS_RX=-5000")                    # absurd raw values: clamped, never beyond 1000..2000
        self.wait_frame(lambda v: v[0] == 2000 and v[3] == 1000)
        self.assertTrue(all(v[i] in (IGN, 0) or 1000 <= v[i] <= 2000 for _, v in self.frames() for i in range(8)))

    def test_unplug_runs_the_deadman_at_once_then_exits_rc4(self):
        br = self.start(deadman=1000)                              # a long dead-man: a quick reaction must come from the device-lost path
        self.wait_frame(lambda v: v[0] == 1500)
        t0 = time.monotonic()
        br.cmd("destroy")
        t_fs, _ = self.wait_frame(lambda v: v == FS_FRAME, since=t0)
        print("INFO unplug -> throttle failsafe frame: %.3f s (--deadman-ms 1000)" % (t_fs - t0))
        self.assertLess(t_fs - t0, 0.7, "throttle failsafe must follow the unplug at once, not after the 1 s dead-man timer")
        later = [v for _, v in self.frames(t_fs)]
        self.assertTrue(all(v in (FS_FRAME, ZERO) for v in later), "stick frames after the failsafe: %s" % [v for v in later if v not in (FS_FRAME, ZERO)][:3])
        self.assertEqual(br.wait_exit(), 4, br.text())
        self.assertIn("evdev device lost", br.text())
        n = len(self.frames())
        time.sleep(1.0)
        self.assertEqual(len(self.frames()), n, "silent after the release (RC receiver takes over)")
        t_end = self.frames()[-1][0]
        zeros = [t for t, v in self.frames(t_fs) if v == ZERO]
        self.assertTrue(0.7 <= t_end - t_fs <= 1.6 * SLOW, "release hold ~1 s: %.2f" % (t_end - t_fs))
        self.assertGreaterEqual(len(zeros), 5)

    def test_releases_throttle_failsafe_values_exactly(self):
        br = self.start()
        br.cmd("set ABS_Z=2047")                                   # full throttle when the radio disappears
        self.wait_frame(lambda v: v[2] == 2000)
        t0 = time.monotonic()
        br.cmd("destroy")
        _, fs = self.wait_frame(lambda v: v[2] != 2000, since=t0)
        self.assertEqual(fs, FS_FRAME)

    def test_syn_dropped_resync_end_to_end(self):
        br = self.start()
        br.cmd("silent ABS_X=2047")
        br.cmd("raw 0:3:0 0:0:0")                                  # SYN_DROPPED, SYN_REPORT
        self.wait_frame(lambda v: v[0] == 2000)

    def test_lost_before_the_fc_is_known_exits_rc4(self):
        br = self.bridge(self.SPEC, fake=True, args=["--conn", "udpout:127.0.0.1:%d" % next_port()])   # nobody answers: state WAIT
        time.sleep(1.5)
        br.cmd("destroy")
        self.assertEqual(br.wait_exit(), 4, br.text())
        self.assertEqual([f for f in self.frames() if f[1] != IGN], [])

    def test_decoy_without_guard_is_followed_with_guard_refused(self):
        """Documents the hazard the 'device' guard closes: a path alone does not say WHICH device it is."""
        mp = os.path.join(self.tmp, "noguard.json")
        m = read_json(EXAMPLE_MAP)
        m.pop("device")
        write_json(mp, m)
        br = self.bridge("evdev:/dev/input/event3", fake=True, plugs=["/dev/input/event3"], mapfile=mp, name="noguard")
        br.wait_active()                                          # event3 is "OpenTX..." in the fake; the guard variant below uses another profile
        br.stop()
        decoy = dict(PROFILE, name="Logitech Gamepad F310", vendor=0x046D, product=0xC216)
        prof = os.path.join(self.tmp, "decoy.json")
        write_json(prof, decoy)
        cmd = [sys.executable, os.path.join(HERE, "fakeproc.py"), "--profile", prof, "--plug", "/dev/input/event3", "--", "--input", "evdev:/dev/input/event3",
               "--map", EXAMPLE_MAP, "--confirm-props-off", "--conn", "udpout:127.0.0.1:%d" % self.port, "--lock", os.path.join(self.tmp, "l2")]
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30 * SLOW, env=dict(os.environ, EVDEV_ROOT=ROOT))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not the expected one", r.stderr + r.stdout)


# ------------------------------------------------------------------ real kernel devices

class KernelBase(Rig):
    BACKEND = None

    @classmethod
    def setUpClass(cls):
        why = device_skip(cls.BACKEND)
        if why:
            raise unittest.SkipTest(why)

    def plug(self, **kw):
        prof = vdev.load_profile()
        for k, v in kw.items():
            if k == "values":
                for name, val in v.items():
                    prof["axes"][name]["value"] = val
            else:
                prof[k] = v
        d = vdev.create(self.BACKEND, prof)
        self.devs.append(d)
        return d

    def spec(self, d):
        return "evdev:" + d.path

    def expect(self, v, x=None, y=None, z=None, rx=None):
        """True when frame v has the given axis outputs (None = don't care)."""
        return all(e is None or v[i] == e for i, e in enumerate((x, y, z, rx))) and v[4:] == (IGN,) * 4


class BridgeScenarios(object):
    """Mixed into KernelBase subclasses (one per back end): the same scenarios run on uinput and on uhid."""

    def test_normal_work_ranges_centre_and_unmapped_channels(self):
        d = self.plug()
        br = self.bridge(self.spec(d))
        br.wait_active()
        self.wait_frame(lambda v: self.expect(v, 1500, 1500, 1500, 1500))
        # (axis kwargs, expected x, y, z, rx): hard-coded endpoints plus oracle values in between
        steps = [
            (dict(ABS_X=2047), 2000, 1500, 1500, 1500),
            (dict(ABS_X=0), 1000, 1500, 1500, 1500),
            (dict(ABS_X=1024, ABS_Y=2047), 1500, 1000, 1500, 1500),         # Y is reversed in the example map
            (dict(ABS_Y=0), 1500, 2000, 1500, 1500),
            (dict(ABS_Y=1024, ABS_Z=0), 1500, 1500, 1000, 1500),
            (dict(ABS_Z=2047), 1500, 1500, 2000, 1500),
            (dict(ABS_Z=512, ABS_RX=2047), 1500, 1500, ref_us(512, **CFG_Z), 2000),
            (dict(ABS_RX=0), 1500, 1500, ref_us(512, **CFG_Z), 1000),
            (dict(ABS_RX=1700, ABS_Z=1024, ABS_RY=0, ABS_RZ=0, ABS_THROTTLE=0, ABS_RUDDER=2047), 1500, 1500, 1500, ref_us(1700, **CFG_X)),   # unmapped axes move: channels 5-8 stay ignored
        ]
        for kw, x, y, z, rx in steps:
            d.set(**kw)
            self.wait_frame(lambda v: self.expect(v, x, y, z, rx), timeout=8)

    def test_two_axes_in_one_report(self):
        d = self.plug()
        br = self.bridge(self.spec(d))
        br.wait_active()
        t0 = time.monotonic()
        d.set(ABS_X=2047, ABS_Y=2047, ABS_Z=0, ABS_RX=0)
        self.wait_frame(lambda v: self.expect(v, 2000, 1000, 1000, 1000))
        # alternate two full states quickly: every frame must be one of the states, never a mix (torn)
        a, c = (2000, 1000, 1000, 1000), (1000, 2000, 2000, 2000)
        for i in range(150):
            d.set(**(dict(ABS_X=2047, ABS_Y=2047, ABS_Z=0, ABS_RX=0) if i % 2 == 0 else dict(ABS_X=0, ABS_Y=0, ABS_Z=2047, ABS_RX=2047)))
            time.sleep(0.004)
        time.sleep(0.2)
        mix = [v for _, v in self.frames(t0) if v[:4] not in ((1500, 1500, 1500, 1500), a, c)]
        self.assertEqual(mix, [], "torn frames (axes from two different reports): %s" % mix[:3])

    def test_unplug_releases_channels_and_a_replug_does_not_resume(self):
        d = self.plug()
        br = self.bridge(self.spec(d), deadman=1000)
        br.wait_active()
        d.set(ABS_Z=2047)
        self.wait_frame(lambda v: v[2] == 2000)
        t0 = time.monotonic()
        d.destroy()
        t_fs, _ = self.wait_frame(lambda v: v == FS_FRAME, since=t0)
        print("INFO unplug -> throttle failsafe frame: %.3f s (%s, --deadman-ms 1000)" % (t_fs - t0, self.BACKEND))
        self.assertLess(t_fs - t0, 0.7, "failsafe latency after unplug (the 1 s dead-man timer must not be what fires)")
        self.assertTrue(all(v in (FS_FRAME, ZERO) for _, v in self.frames(t_fs)))
        self.assertIn("evdev device lost", br.text())
        # a new device appears (possibly at the same eventN): the old bridge must not pick it up and resume the sticks
        d2 = self.plug(values=dict(ABS_X=2047))
        time.sleep(2.5 * SLOW)
        self.assertEqual(self.sticks_after(t_fs), [], "bridge resumed by itself after a replug")
        self.assertEqual(br.wait_exit(), 4, br.text())            # it exits so a supervisor restarts it with a fresh device path
        self.assertIn("exiting", br.text())
        # a freshly started bridge works on the new device (lock was released)
        br2 = self.bridge(self.spec(d2), name="b2")
        br2.wait_active()
        self.wait_frame(lambda v: v[0] == 2000, since=time.monotonic() - 1)

    def test_event_storm_10khz(self):
        d = self.plug()
        br = self.bridge(self.spec(d))
        br.wait_active()
        t0 = time.monotonic()
        n = 3000
        d.burst(n, codes=("ABS_X", "ABS_Y", "ABS_Z", "ABS_RX"))
        dt = time.monotonic() - t0
        d.set(ABS_X=777, ABS_Y=1024, ABS_Z=1024, ABS_RX=1024)
        self.wait_frame(lambda v: v[0] == ref_us(777, **CFG_X), since=t0, timeout=10)
        print("INFO storm: %d packets (4 axes) in %.2f s = %.0f packets/s (%s)" % (n, dt, n / dt, self.BACKEND))
        self.assertIsNone(br.p.poll(), br.text())
        self.assertNotIn(FS_FRAME, [v for _, v in self.frames(t0)], "dead-man must not fire in a storm")
        with open(br.trace) as f:
            tr = [float(x) for x in f]
        gaps = [y - x for x, y in zip(tr, tr[1:])]
        self.assertGreaterEqual(min(gaps), 0.018, "--max-rate 50 gap violated under the storm: %.4f" % min(gaps))
        self.assertTrue(all(1000 <= x <= 2000 for _, v in self.frames(t0) for x in v[:4]))

    def test_overrun_syn_dropped_keeps_a_still_axis_correct(self):
        """The kernel client buffer overflows while the bridge is not reading: it drops events, among them the last change of a
        still axis (here ABS_X, the roll stick). The bridge must resynchronise (EVIOCGABS) instead of keeping the stale value."""
        d = self.plug()
        br = self.bridge(self.spec(d), deadman=1000)
        br.wait_active()
        self.wait_frame(lambda v: self.expect(v, 1500, 1500, 1500, 1500))
        br.p.send_signal(signal.SIGSTOP)
        try:
            time.sleep(0.2)
            d.set(ABS_X=2047)
            d.burst(800, codes=("ABS_Y", "ABS_Z"))
            d.set(ABS_Y=1024, ABS_Z=1024)
        finally:
            br.p.send_signal(signal.SIGCONT)
        self.wait_frame(lambda v: v[0] == 2000 and v[1] == 1500 and v[2] == 1500, since=time.monotonic(), timeout=15)
        self.assertIn("SYN_DROPPED", br.text())

    def test_silent_still_device_keeps_sending_by_design(self):
        """Limitation, not a feature (docs/GAPS.md S4): evdev is event-on-change, so a still stick and a hung radio look the same."""
        d = self.plug()
        br = self.bridge(self.spec(d))
        br.wait_active()
        d.set(ABS_Z=1500)
        time.sleep(3.0 * SLOW)
        t = time.monotonic()
        time.sleep(1.0)
        fr = self.frames(t)
        self.assertGreater(len(fr), 5)
        self.assertTrue(all(v[2] == ref_us(1500, **CFG_Z) for _, v in fr))

    def test_unprivileged_user_without_access_is_refused(self):
        d = self.plug()
        os.chmod(d.path, 0o600)                                    # root-only, as devtmpfs makes it without udev rules
        br = self.bridge(self.spec(d), user=65534)
        self.assertEqual(br.wait_exit(), 1, br.text())
        self.assertIn("cannot open", br.text())
        self.assertIn("Permission denied", br.text())
        self.assertEqual(self.frames(), [])
        # read-only access is enough for the bridge (python-evdev falls back to O_RDONLY)
        os.chmod(d.path, 0o644)
        br2 = self.bridge(self.spec(d), user=65534, name="ro")
        br2.wait_active()
        d.set(ABS_X=2047)
        self.wait_frame(lambda v: v[0] == 2000)

    def test_double_start_is_refused_and_the_lock_dies_with_the_process(self):
        d = self.plug()
        br = self.bridge(self.spec(d))
        br.wait_active()
        br2 = self.bridge(self.spec(d), name="b2")
        self.assertEqual(br2.wait_exit(), 3, br2.text())
        self.assertIn("REFUSED", br2.text())
        self.assertFalse(os.path.exists(br2.trace) and os.path.getsize(br2.trace) > 0, "the refused instance sent frames")
        br.stop(signal.SIGKILL)                                    # no cleanup at all: the kernel drops the flock
        br3 = self.bridge(self.spec(d), name="b3")
        br3.wait_active()

    def test_missing_axis_refused(self):
        d = self.plug()
        mp = os.path.join(self.tmp, "hat.json")
        m = read_json(EXAMPLE_MAP)
        m["axes"]["ABS_HAT0X"] = {"channel": 5, "min": 0, "max": 2047}
        write_json(mp, m)
        br = self.bridge(self.spec(d), mapfile=mp)
        self.assertEqual(br.wait_exit(), 1, br.text())
        self.assertIn("has no axes", br.text())
        self.assertEqual(self.frames(), [])


class TestBridgeUinput(BridgeScenarios, KernelBase):
    BACKEND = "uinput"

    def test_initial_axis_state_is_read_at_open(self):
        d = self.plug(values=dict(ABS_X=2047, ABS_Z=0))           # nothing changes after the bridge starts
        br = self.bridge(self.spec(d))
        br.wait_active()
        self.wait_frame(lambda v: self.expect(v, 2000, 1500, 1000, 1500))

    def test_out_of_range_raw_values_are_clamped(self):
        d = self.plug()
        br = self.bridge(self.spec(d))
        br.wait_active()
        d.set(ABS_X=5000, ABS_Y=-300)
        self.wait_frame(lambda v: v[0] == 2000 and v[1] == 2000)    # Y reversed: raw far below min = full reverse = 2000
        d.set(ABS_X=-1, ABS_Y=70000)
        self.wait_frame(lambda v: v[0] == 1000 and v[1] == 1000)

    def test_selection_by_id_name_and_guard(self):
        tx = self.plug()
        decoy = self.plug(name="Logitech Gamepad F310", vendor=0x046D, product=0xC216)
        mp = os.path.join(self.tmp, "noguard.json")
        m = read_json(EXAMPLE_MAP)
        m.pop("device")
        write_json(mp, m)
        decoy.set(ABS_X=0)                                          # the decoy moves, the radio does not
        br = self.bridge("evdev:id=1209:4f54", name="byid")
        br.wait_active()
        d_t0 = time.monotonic()
        tx.set(ABS_X=2047)
        self.wait_frame(lambda v: v[0] == 2000, since=d_t0)         # follows the radio, not the decoy
        br.stop()
        br = self.bridge("evdev:name=Logitech Gamepad F310", mapfile=mp, name="byname")
        br.wait_active()
        t1 = time.monotonic()
        self.wait_frame(lambda v: v[0] == 1000, since=t1)           # now it follows the decoy (X = 0)
        br.stop()
        # explicit path to the decoy with the example map (which carries the guard): refused
        br = self.bridge(self.spec(decoy), name="guard")
        self.assertEqual(br.wait_exit(), 1, br.text())
        self.assertIn("not the expected one", br.text())
        # the same path without a guard is followed: this is the hazard the guard closes
        br = self.bridge(self.spec(decoy), mapfile=mp, name="noguard")
        br.wait_active()
        br.stop()
        # no match, and ambiguity (two radios with identical ids)
        br = self.bridge("evdev:id=dead:beef", name="nomatch")
        self.assertEqual(br.wait_exit(), 1, br.text())
        self.assertIn("no input device matches", br.text())
        self.plug()                                                 # second TX12-like device: identical name and ids
        br = self.bridge("evdev:id=1209:4f54", name="ambig")
        self.assertEqual(br.wait_exit(), 1, br.text())
        self.assertIn("ambiguous", br.text())
        br = self.bridge("evdev:name=OpenTX TX12MK2 Joystick", name="ambig2")
        self.assertEqual(br.wait_exit(), 1, br.text())
        self.assertIn("ambiguous", br.text())


class TestBridgeUhid(BridgeScenarios, KernelBase):
    BACKEND = "uhid"


class TestKernelHid(KernelBase):
    """The 6.8 guest kernel's hid-input turns the EdgeTX Classic descriptor into evdev; the profile and the map must agree with it."""
    BACKEND = "uhid"

    def test_kernel_derived_capabilities_equal_the_profile(self):
        import evdev
        d = vdev.UhidDevice(PROFILE)
        self.devs.append(d)
        dev = evdev.InputDevice(d.path)
        self.assertEqual((dev.info.vendor, dev.info.product, dev.info.bustype), (PROFILE["vendor"], PROFILE["product"], PROFILE["bustype"]))
        self.assertEqual(dev.name, PROFILE["name"])
        caps = dict(dev.capabilities(absinfo=True))
        got = {c: (i.min, i.max, i.fuzz, i.flat) for c, i in caps[evdev.ecodes.EV_ABS]}
        want = {vdev.ABS[n]: (a["min"], a["max"], a["fuzz"], a["flat"]) for n, a in PROFILE["axes"].items()}
        self.assertEqual(got, want)
        self.assertEqual(sorted(caps[evdev.ecodes.EV_KEY]), PROFILE["button_codes"])
        print("INFO kernel hid-input: %s %04x:%04x abs(min,max,fuzz,flat)=%s buttons=%d" %
              (dev.name, dev.info.vendor, dev.info.product, sorted(got.items())[:1], len(caps[evdev.ecodes.EV_KEY])))

    def test_report_positions_map_to_the_documented_axes_and_buttons(self):
        import evdev
        d = vdev.UhidDevice(PROFILE)
        self.devs.append(d)
        dev = evdev.InputDevice(d.path)
        time.sleep(0.3)
        self._drain(dev)
        for i, name in enumerate(PROFILE["axes"]):
            d.set(**{name: 300 + 100 * i})
            ev = []
            wait_for(lambda: ev.extend(self._drain(dev)) or any(e.type == evdev.ecodes.EV_ABS for e in ev), 3)
            hits = [(e.code, e.value) for e in ev if e.type == evdev.ecodes.EV_ABS]
            self.assertIn((vdev.ABS[name], 300 + 100 * i), hits, "%s: %s" % (name, hits))
        for n in (1, 16, 17, 24):
            d.press(n)
            ev = []
            wait_for(lambda: ev.extend(self._drain(dev)) or any(e.type == evdev.ecodes.EV_KEY for e in ev), 3)
            self.assertIn(PROFILE["button_codes"][n - 1], [e.code for e in ev if e.type == evdev.ecodes.EV_KEY and e.value == 1], n)
            d.press(n, False)

    @staticmethod
    def _drain(dev):
        out = []
        try:
            for e in dev.read():
                out.append(e)
        except (BlockingIOError, OSError):
            pass
        return out

    def test_one_khz_reports_are_filtered_by_the_kernel_to_changes(self):
        """The radio reports every 1 ms; evdev only delivers changes. A still stick therefore makes no events (the freshness design of EvdevSource)."""
        import evdev
        d = vdev.UhidDevice(PROFILE)
        self.devs.append(d)
        dev = evdev.InputDevice(d.path)
        time.sleep(0.3)
        self._drain(dev)
        for _ in range(300):
            d.send()                                                # identical reports
        time.sleep(0.2)
        self.assertEqual([e for e in self._drain(dev) if e.type == evdev.ecodes.EV_ABS], [])

    def test_fuzz_swallows_changes_smaller_than_half_the_fuzz(self):
        import evdev
        d = vdev.UhidDevice(PROFILE)
        self.devs.append(d)
        dev = evdev.InputDevice(d.path)
        time.sleep(0.3)
        self._drain(dev)
        d.set(ABS_X=1026)                                           # |delta| = 2 < fuzz/2 = 3.5 -> input core keeps the old value
        time.sleep(0.2)
        self.assertEqual(dev.absinfo(vdev.ABS["ABS_X"]).value, 1024)
        d.set(ABS_X=1100)
        time.sleep(0.2)
        self.assertEqual(dev.absinfo(vdev.ABS["ABS_X"]).value, 1100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
