#!/usr/bin/env python3
"""Tests of the session replay twin (unittest; stdlib + pymavlink for the integration part, SKIP without it).

Layers:
  pure      schedule derivation, replay plan arithmetic, determinism/seed, contract logic on hand-made results (no processes)
  channel   the UDP hub on loopback (delay, silence, jitter) and its decision rule against air_relay.py (source check always,
            live veth cross-check only as root with CAP_NET_ADMIN, else SKIP)
  integration  REAL bench/tx12_bridge.py + tests/sim/apm_fc.py + tests/sim/udp_probe.py driven by a schedule that comes from the
            scenario engine (fast profile: FC time constants scaled down so the whole module runs in a few seconds)

Fast profile values are SYNTH test settings, not the defaults of the product (docs/SIM-TWIN.md).
"""
import concurrent.futures
import copy
import os
import random
import shutil
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
sys.path.insert(0, HERE)
import channel  # noqa: E402
import contracts  # noqa: E402
import schedule as S  # noqa: E402

PY = os.environ.get("PY") or sys.executable


def has_pymavlink():
    try:
        return subprocess.run([PY, "-c", "import pymavlink"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


HAVE_MAV = has_pymavlink()
FAST = {"rc_override_time": 0.8, "fs_gcs_timeout": 1.2, "rc_fs_timeout": 0.4, "hb_period_s": 0.2, "release_hold_s": 0.5, "silence_cap_s": 2.2,
        "pre_s": 0.4, "post_s": 1.2, "quiet_s": 0.5, "warmup_s": 1.6, "steady_min_s": 0.0, "include_bringup": False, "budget_s": 6.0, "video_pps": 100.0}

BASE = {"loss": 0.01, "delay_ms": 2.0, "jitter_ms": 0.5}


def ev(t, kind, **kw):
    d = {"t_s": float(t), "kind": kind}
    d.update(kw)
    return d


EVENTS = [ev(0, "bringup", ok=True, t_s_total=4.0), ev(10, "throttled"), ev(50, "usb_trip"), ev(50, "usb_drop", reason="overcurrent"),
          ev(70, "usb_return"), ev(90, "burst_on"), ev(110, "burst_off"), ev(130, "shock", excess_db=9.0), ev(150, "stall", backlog_ms=400.0),
          ev(200, "thermal_shutdown"), ev(260, "thermal_resume")]


def loss_fn(noise, burst):
    return min(1.0, 0.01 + 0.08 * noise + (0.7 if burst else 0.0))


class TestScheduleDerivation(unittest.TestCase):
    def setUp(self):
        self.fx = S.derive_effects(EVENTS, 600.0)
        self.segs = S.build_segments(600.0, self.fx, BASE, loss_fn, shock_tau_s=10.0, catchup_ms_per_s=20.0)

    def test_silences_from_events(self):
        sil = {(round(a, 1), round(b, 1), c) for a, b, c, _j in self.fx["silences"]}
        self.assertIn((0.0, 4.0, "bringup"), sil)                      # start-up delay
        self.assertIn((50.0, 74.0, "usb_overcurrent"), sil)            # drop at 50, return at 70 + re-bring-up 4 s
        self.assertIn((200.0, 260.0, "thermal_shutdown"), sil)

    def test_latched_drop_never_returns(self):
        fx = S.derive_effects([ev(0, "bringup", ok=True, t_s_total=3.0), ev(20, "usb_latched"), ev(20, "usb_drop", reason="overcurrent")], 100.0)
        self.assertIn((20.0, 100.0), {(a, b) for a, b, _c, _j in fx["silences"]})

    def test_failed_bringup_is_silent_forever(self):
        fx = S.derive_effects([ev(0, "bringup", ok=False, t_s_total=9.0, failed_stage="fw_load")], 100.0)
        self.assertEqual([(0.0, 100.0)], [(a, b) for a, b, _c, _j in fx["silences"]])

    def test_ignored_kinds_are_counted_not_applied(self):
        self.assertEqual(1, self.fx["ignored"].get("throttled"))
        self.assertEqual(1, self.fx["ignored"].get("usb_trip"))

    def test_segments_cover_the_session_without_gaps(self):
        self.assertAlmostEqual(0.0, self.segs[0]["t0"])
        self.assertAlmostEqual(600.0, self.segs[-1]["t1"])
        for a, b in zip(self.segs, self.segs[1:]):
            self.assertAlmostEqual(a["t1"], b["t0"])

    def test_silence_is_total_loss(self):
        for s in self.segs:
            if 51 < s["t0"] < 73:
                self.assertTrue(s["silent"])
                self.assertEqual(1.0, s["loss"])

    def test_burst_raises_loss(self):
        seg = next(s for s in self.segs if s["t0"] <= 100 < s["t1"])
        self.assertGreater(seg["loss"], 0.6)
        self.assertFalse(seg["silent"])

    def test_shock_decays(self):
        a = next(s for s in self.segs if s["t0"] <= 130.5 < s["t1"])["loss"]
        b = next(s for s in self.segs if s["t0"] <= 136.5 < s["t1"])["loss"]
        self.assertGreater(a, b)
        self.assertGreater(a, BASE["loss"] + 0.1)

    def test_stall_adds_delay(self):
        seg = next(s for s in self.segs if s["t0"] <= 151 < s["t1"])
        self.assertGreater(seg["delay_ms"], BASE["delay_ms"] + 100.0)       # the stall backlog (400 ms) is applied as extra delay
        quiet = next(s for s in self.segs if s["t0"] <= 300 < s["t1"])
        self.assertAlmostEqual(BASE["delay_ms"], quiet["delay_ms"])

    def test_bringup_can_be_excluded(self):
        segs = S.build_segments(600.0, self.fx, BASE, loss_fn, include_bringup=False)
        self.assertFalse(segs[0]["silent"])

    def test_joystick_shared_marks_overcurrent_only(self):
        fx = S.derive_effects(EVENTS, 600.0, "shared")
        self.assertEqual({"usb_overcurrent"}, {c for _a, _b, c, j in fx["silences"] if j})
        segs = S.build_segments(600.0, fx, BASE, loss_fn)
        plan = S.make_plan(segs, 600.0, BASE, S.effective({"include_bringup": True, "budget_s": 1000.0}))
        self.assertEqual(1, len(S.input_stalls(plan)))
        plan2 = S.make_plan(self.segs, 600.0, BASE, S.effective({"budget_s": 1000.0}))
        self.assertEqual([], S.input_stalls(plan2))


class TestPlan(unittest.TestCase):
    def setUp(self):
        fx = S.derive_effects(EVENTS, 600.0)
        self.segs = S.build_segments(600.0, fx, BASE, loss_fn, shock_tau_s=10.0)

    def test_accounting_adds_up(self):
        P = S.effective({"budget_s": 1000.0})
        plan = S.make_plan(self.segs, 600.0, BASE, P)
        self.assertAlmostEqual(600.0, plan["replayed_model_s"] + plan["elided_model_s"] + plan["truncated_model_s"], places=3)

    def test_long_silence_is_truncated_to_the_cap(self):
        P = S.effective({"budget_s": 1000.0})
        segs = S.build_segments(600.0, S.derive_effects([ev(0, "bringup", ok=True, t_s_total=3.0), ev(100, "usb_latched"), ev(100, "usb_drop", reason="overcurrent")], 600.0),
                                BASE, loss_fn)
        plan = S.make_plan(segs, 600.0, BASE, P)
        o = [x for x in plan["outages"] if x["m0"] == 100.0][0]
        self.assertAlmostEqual(P["silence_cap_s"], o["played_len_model"])
        self.assertAlmostEqual(500.0 - P["silence_cap_s"], o["truncated_model_s"])
        self.assertAlmostEqual((P["silence_cap_s"]) / P["speed"], o["r1"] - o["r0"], places=3)

    def test_budget_is_respected_and_reported(self):
        P = S.effective({"budget_s": 12.0})
        plan = S.make_plan(self.segs, 600.0, BASE, P)
        self.assertLessEqual(plan["real_s"], 12.0 + 1e-6)
        self.assertTrue(plan["skipped"])

    def test_speed_compresses_real_time(self):
        p1 = S.make_plan(self.segs, 600.0, BASE, S.effective({"budget_s": 1000.0, "speed": 1.0}))
        p2 = S.make_plan(self.segs, 600.0, BASE, S.effective({"budget_s": 1000.0, "speed": 2.0}))
        o1 = [o for o in p1["outages"] if o["m0"] == 50.0][0]
        o2 = [o for o in p2["outages"] if o["m0"] == 50.0][0]
        self.assertAlmostEqual((o1["r1"] - o1["r0"]) / 2.0, o2["r1"] - o2["r0"], places=3)
        self.assertLess(p2["real_s"], p1["real_s"])

    def test_speed_bounds(self):
        with self.assertRaises(ValueError):
            S.effective({"speed": 3.0})
        e = S.effective({"speed": 2.0})
        self.assertAlmostEqual(e["rc_override_time"] / 2.0, e["rc_override_time_real"])
        self.assertGreaterEqual(e["deadman_ms_real"], 50)

    def test_seg_at_lookup(self):
        plan = S.make_plan(self.segs, 600.0, BASE, S.effective({"budget_s": 1000.0}))
        o = plan["outages"][0]
        self.assertTrue(S.seg_at(plan["segs"], (o["r0"] + o["r1"]) / 2)["silent"])
        self.assertIsNone(S.seg_at(plan["segs"], plan["real_s"] + 1.0))

    def test_plan_segments_are_contiguous(self):
        plan = S.make_plan(self.segs, 600.0, BASE, S.effective({"budget_s": 1000.0}))
        for a, b in zip(plan["segs"], plan["segs"][1:]):
            self.assertAlmostEqual(a["r1"], b["r0"], places=6)

    def test_steady_tail_when_everything_is_bad(self):
        segs = S.build_segments(100.0, S.derive_effects([ev(0, "bringup", ok=True, t_s_total=2.0)], 100.0), BASE, loss_fn)
        plan = S.make_plan(segs, 100.0, BASE, S.effective({"steady_min_s": 5.0}))
        self.assertGreaterEqual(sum(s["r1"] - s["r0"] for s in plan["segs"] if not s["tags"] or s["tags"] in (["steady"], ["warmup"], ["elided"])), 4.9)


class TestEngineBinding(unittest.TestCase):
    """The schedule must really come from the scenario engine, deterministically per seed."""

    def test_determinism(self):
        a = S.forecast("pi5_3a_weak_psu", 1)
        b = S.forecast("pi5_3a_weak_psu", 1)
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["segs"], b["segs"])

    def test_seed_changes_the_schedule(self):
        d = {S.forecast("pi5_3a_weak_psu", s)["digest"] for s in (1, 2, 3, 4)}
        self.assertGreaterEqual(len(d), 3)

    def test_events_are_the_engines_own_events_per_seed(self):
        """The twin must replay exactly what `scenario_engine.py events --seed N` prints (no private re-implementation)."""
        import contextlib
        import io
        import json
        sys.path.insert(0, S.MODELS)
        import scenario_engine as se
        seen = []
        for seed in (1, 2, 3):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(0, se.main(["events", "pi5_3a_weak_psu", "--seed", str(seed)]))
            want = json.loads(buf.getvalue())["events"]
            self.assertEqual(want, S.forecast("pi5_3a_weak_psu", seed)["events"], seed)
            seen.append(json.dumps(want))
        self.assertGreaterEqual(len(set(seen)), 3, "different seeds must give different event timelines")

    def test_every_usb_drop_event_becomes_a_silence(self):
        for seed in (1, 2, 3):
            f = S.forecast("pi5_3a_weak_psu", seed)
            for e in f["events"]:
                if e["kind"] == "usb_drop":
                    self.assertTrue(any(s["silent"] and s["t0"] - 1e-6 <= e["t_s"] < s["t1"] for s in f["segs"]), (seed, e))

    def test_bringup_delay_is_a_silence_at_t0(self):
        f = S.forecast("nominal_pi5_5a_150m", 1)
        self.assertTrue(f["segs"][0]["silent"])
        self.assertAlmostEqual(f["fx"]["bringup_s"], f["segs"][0]["t1"], places=2)

    def test_schedule_explains_engine_downtime(self):
        for sc, seed in (("pi5_3a_weak_psu", 1), ("nominal_pi5_5a_150m", 2), ("hot_day_closed_case", 4)):
            f = S.forecast(sc, seed)
            sil = sum(s["t1"] - s["t0"] for s in f["segs"] if s["silent"]) - f["fx"]["bringup_s"]
            self.assertLessEqual(abs(f["model"]["down_s"] - sil), f["dt"] * (len(f["fx"]["silences"]) + 1), (sc, seed))

    def test_duration_override(self):
        f = S.forecast("nominal_pi5_5a_150m", 1, duration=60.0)
        self.assertAlmostEqual(60.0, f["segs"][-1]["t1"])

    def test_forecast_plan_fits_budget(self):
        for sc, seed in (("pi5_3a_weak_psu", 2), ("hot_day_closed_case", 3)):
            f = S.forecast(sc, seed)
            P = S.effective({"speed": 2.0})
            plan = S.make_plan(f["segs"], f["T"], f["base"], P)
            self.assertLessEqual(plan["real_s"], P["budget_s"] + 1e-6)


class TestChannelDecision(unittest.TestCase):
    def test_air_relay_rule_is_the_same(self):
        with open(os.path.join(HERE, "..", "air_relay.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("rnd.random() < a.loss", src)
        self.assertIn("rnd.gauss(a.delay_ms, a.jitter_ms) if a.jitter_ms else a.delay_ms", src)
        self.assertIn("max(0.0,", src)

    def test_total_loss_and_no_loss(self):
        r = random.Random(1)
        self.assertTrue(all(channel.decide(r, 1.0, 5.0, 0.0) is None for _ in range(200)))
        self.assertTrue(all(channel.decide(r, 0.0, 5.0, 0.0) == 0.005 for _ in range(200)))

    def test_loss_rate_matches(self):
        r = random.Random(7)
        n = 20000
        d = sum(1 for _ in range(n) if channel.decide(r, 0.3, 0.0, 0.0) is None)
        self.assertLess(abs(d / n - 0.3), 0.02)

    def test_seeded_and_seed_matters(self):
        def run(seed):
            r = random.Random(seed)
            return [channel.decide(r, 0.5, 10.0, 3.0) for _ in range(50)]
        self.assertEqual(run(3), run(3))
        self.assertNotEqual(run(3), run(4))

    def test_jitter_spreads_and_never_negative(self):
        r = random.Random(2)
        xs = [channel.decide(r, 0.0, 1.0, 5.0) for _ in range(2000)]
        self.assertTrue(all(x >= 0 for x in xs))
        self.assertGreater(max(xs) - min(xs), 0.005)
        self.assertGreater(sum(xs) / len(xs), 0.002)       # clipped gauss: mean above the nominal delay

    def test_delay_in_seconds(self):
        self.assertAlmostEqual(0.08, channel.decide(random.Random(1), 0.0, 80.0, 0.0))


class TestChannelUdp(unittest.TestCase):
    """The hub on loopback with a hand-made schedule: silence, then delay."""

    def _run(self, segs, send_until, seed=1):
        ch = channel.Channel(seed, seg_at=lambda t: S.seg_at(segs, t))
        sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sink.bind(("127.0.0.1", 0))
        sink.settimeout(0.05)
        ch.set_video_sink(sink.getsockname())
        ch.start()
        src = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        got = []
        sent = []
        i = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < send_until:
            src.sendto(struct.pack("<Id", i, time.monotonic()) + b"\0" * 20, ("127.0.0.1", ch.ports["vid_in"]))
            sent.append((i, time.monotonic() - t0))
            i += 1
            try:
                while True:
                    d = sink.recv(2000)
                    got.append((struct.unpack_from("<I", d)[0], time.monotonic() - t0, struct.unpack_from("<Id", d)[1] - t0))
            except socket.timeout:
                pass
            time.sleep(0.01)
        time.sleep(0.15)
        try:
            while True:
                d = sink.recv(2000)
                got.append((struct.unpack_from("<I", d)[0], time.monotonic() - t0, struct.unpack_from("<Id", d)[1] - t0))
        except socket.timeout:
            pass
        ch.stop()
        src.close()
        sink.close()
        return sent, got, ch.rec

    def test_silence_then_delay(self):
        def seg(r0, r1, silent, delay):
            return {"r0": r0, "r1": r1, "silent": silent, "loss": 1.0 if silent else 0.0, "delay_ms": delay, "jitter_ms": 0.0, "tags": []}
        segs = [seg(0.0, 0.4, True, 0.0), seg(0.4, 1.4, False, 120.0)]
        sent, got, rec = self._run(segs, 0.9)
        times = {i: t for i, t in sent}
        in_silence = [i for i, t in sent if t < 0.35]
        self.assertTrue(in_silence)
        gotids = {i for i, _t, _s in got}
        self.assertFalse(gotids & set(in_silence), "packets sent during silence were delivered")
        later = [(i, t, s) for i, t, s in got if i in times and times[i] > 0.5]
        self.assertTrue(later, "nothing delivered after the silence ended")
        # 120 ms configured delay must show (loopback itself is < 5 ms)
        self.assertTrue(all(t - ts >= 0.11 for _i, t, ts in later), min(t - ts for _i, t, ts in later))
        self.assertEqual(0, sum(1 for r in rec if r["silent"] and not r["dropped"]))

    def test_before_start_and_after_plan_end_the_link_is_down(self):
        segs = [{"r0": 0.0, "r1": 0.3, "silent": False, "loss": 0.0, "delay_ms": 0.0, "jitter_ms": 0.0, "tags": []}]
        sent, got, rec = self._run(segs, 0.7)
        late = [i for i, t in sent if t > 0.45]
        self.assertTrue(late)
        self.assertFalse({i for i, _t, _s in got} & set(late), "link must be down after the plan ends")


def _relay_env_ok():
    if os.geteuid() != 0 or not hasattr(socket, "AF_PACKET"):
        return False
    r = subprocess.run(["ip", "link", "add", "twchk0", "type", "veth", "peer", "name", "twchk1"], capture_output=True)
    if r.returncode != 0:
        return False
    subprocess.run(["ip", "link", "del", "twchk0"], capture_output=True)
    return True


@unittest.skipUnless(os.environ.get("TWIN_NO_VETH") != "1" and _relay_env_ok(), "needs root + veth (real air_relay.py cross-check); SKIP as an unprivileged user")
class TestAirRelayCrosscheck(unittest.TestCase):
    """The REAL tests/sim/air_relay.py on veth drops exactly the frames the hub's decide() drops for the same seed."""

    def test_same_seed_same_drops(self):
        k = "%d" % (os.getpid() % 100000)       # unique interface names: parallel runs (mutation script) must not collide
        tx0, air0, air1, rx0 = "tt%s" % k, "ta%s" % k, "tb%s" % k, "tr%s" % k
        names = [tx0, air0, air1, rx0]
        try:
            for a, b in ((tx0, air0), (air1, rx0)):
                subprocess.run(["ip", "link", "add", a, "type", "veth", "peer", "name", b], check=True, capture_output=True)
            for n in names:
                subprocess.run(["ip", "link", "set", n, "up"], check=True, capture_output=True)
            seed, loss, n = 5, 0.3, 150
            relay = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "air_relay.py"), "--src", air0, "--dst", air1, "--loss", str(loss),
                                      "--seed", str(seed), "--duration", "2.6"], stdout=subprocess.PIPE, text=True)
            time.sleep(0.5)
            rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
            rx.bind((rx0, 0))
            rx.settimeout(0.2)
            tx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            tx.bind((tx0, 0))
            for i in range(n):
                tx.send(struct.pack("<BBHI", 0, 0, 8, 0) + b"\x08\x00" + struct.pack("<H", i) + b"\0" * 40)
                time.sleep(0.004)
            seen = set()
            end = time.monotonic() + 0.4
            while time.monotonic() < end:
                try:
                    p = rx.recv(2000)
                except socket.timeout:
                    continue
                if len(p) > 20 and p[0] == 0 and p[2] == 11:        # the synthetic RX radiotap header of air_relay (11 bytes)
                    seen.add(struct.unpack_from("<H", p, 11 + 2)[0])
            out = relay.communicate(timeout=10)[0]
            fwd = int(out.split("forwarded=")[1].split()[0])
            drop = int(out.split("dropped=")[1].split()[0])
            if fwd + drop != n:
                self.skipTest("stray frames reached the relay (%d+%d != %d)" % (fwd, drop, n))
            r = random.Random(seed)
            expect = {i for i in range(n) if channel.decide(r, loss, 0.0, 0.0) is not None}
            self.assertEqual(drop, n - len(expect))
            self.assertEqual(expect, seen)
            rx.close()
            tx.close()
        finally:
            subprocess.run(["ip", "link", "del", tx0], capture_output=True)
            subprocess.run(["ip", "link", "del", air1], capture_output=True)


# ---------------------------------------------------------------- contracts on hand-made results
def healthy():
    return {
        "t_end": 20.0,
        "bridge": {"rc_frames": 100, "sysids": [255], "targets": [1], "deadman_log": [], "released_any": False},
        "hub": {"delivered_in_silence": 0, "video_delivered_in_silence": 0},
        "fc": {"events": [(3.0, "RC override expired: x")], "ignored_override": 0},
        "oracle": {"pairs": 3, "missing": [], "extra": [], "max_dt": 0.01},
        "override_expiry": {"required": [{"gap_start": 2.0, "gap_len": 3.0, "want_by": 3.3, "got": 2.9}], "spurious": []},
        "gcs_failsafe": {"required": [{"gap_start": 2.0, "gap_len": 3.0, "want_by": 3.6, "got": 3.5}], "spurious": []},
        "radio_failsafe": {"required": [{"gap_start": 2.0, "gap_len": 3.0, "want_by": 3.6, "got": 3.4}]},
        "video": {"probe": {"sent": 1000, "recv": 800}, "total_sent": 1000, "total_delivered": 800, "quiet_loss": 0.0},
        "outages": [{"tags": ["silence:usb_overcurrent"], "r0": 2.0, "r1": 5.0, "played_real_s": 3.0, "control_restored_after_s": 0.1,
                     "video_freeze_s": 3.0, "video_recover_after_s": 0.01, "no_control_s": 2.1, "forecast_no_control_s": 2.0,
                     "override_expired_after_s": 1.0, "gcs_failsafe_after_s": 1.5, "radio_failsafe_after_s": 1.4,
                     "forecast": {"override_expired": True, "gcs_failsafe": True, "radio_failsafe": True}}],
        "deadman": [{"stall": (1.0, 3.0), "deadman_at": 0.31, "throttle_fs_frames": 3, "release_frames": 10, "release_end": 1.2,
                     "sticks_after_deadman": 0, "frames_after_release": 0, "resumed_after_s": 0.05, "fresh_after_s": 0.05}],
    }


class TestContractsLogic(unittest.TestCase):
    P = S.effective({})

    def status(self, r, cid):
        out = {c["id"]: c for c in contracts.evaluate(r, self.P, {})}
        return out[cid]["status"]

    def test_healthy_result_passes_everything(self):
        out = contracts.evaluate(healthy(), self.P, {})
        self.assertEqual([], [c for c in out if c["status"] == "FAIL"])
        self.assertEqual("PASS", contracts.verdict(out))

    def test_every_listed_contract_has_a_violation_case(self):
        cases = {
            "K01": lambda r: r["bridge"].update(sysids=[255, 77]),
            "K02": lambda r: r["bridge"].update(targets=[1, 9]),
            "K03": lambda r: r["hub"].update(delivered_in_silence=1),
            "K04": lambda r: r["override_expiry"]["required"][0].update(got=None),
            "K05": lambda r: r["gcs_failsafe"]["required"][0].update(got=None),
            "K06": lambda r: r["radio_failsafe"]["required"][0].update(got=None),
            "K07": lambda r: r["oracle"].update(missing=[("GCS Failsafe", 3.0)]),
            "K08": lambda r: r["outages"][0].update(control_restored_after_s=2.5),
            "K09": lambda r: r["outages"][0].update(video_freeze_s=0.1),
            "K10": lambda r: r["video"]["probe"].update(recv=500),
            "K11": lambda r: r["outages"][0].update(no_control_s=9.0),
            "K12": lambda r: r["outages"][0].update(gcs_failsafe_after_s=None),
            "K13": lambda r: r["deadman"][0].update(sticks_after_deadman=4),
        }
        listed = {c[0] for c in contracts.CONTRACTS}
        for cid, mut in cases.items():
            r = healthy()
            mut(r)
            self.assertEqual("FAIL", self.status(r, cid), cid)
        self.assertTrue(listed - set(cases) <= {"K14", "K15"})     # these two need a forecast/plan: covered below

    def test_k14_k15(self):
        f = S.forecast("pi5_3a_weak_psu", 1)
        ctx = {"forecast": f, "plan": {"real_s": 10.0}, "include_bringup": True}
        out = {c["id"]: c["status"] for c in contracts.evaluate(healthy(), self.P, ctx)}
        self.assertEqual("PASS", out["K14"])
        self.assertEqual("PASS", out["K15"])
        f2 = copy.deepcopy(f)
        f2["model"]["down_s"] = 5000.0
        ctx2 = {"forecast": f2, "plan": {"real_s": 99.0}, "include_bringup": True}
        out2 = {c["id"]: c["status"] for c in contracts.evaluate(healthy(), self.P, ctx2)}
        self.assertEqual("FAIL", out2["K14"])
        self.assertEqual("FAIL", out2["K15"])

    def test_wrong_gcs_sysid_fails_single_writer(self):
        P = S.effective({"mav_gcs_sysid": 254})
        out = {c["id"]: c["status"] for c in contracts.evaluate(healthy(), P, {})}
        self.assertEqual("FAIL", out["K01"])

    def test_fs_gcs_disabled_expectation_is_inverted(self):
        P = S.effective({"fs_gcs_enable": 0})
        r = healthy()
        out = {c["id"]: c["status"] for c in contracts.evaluate(r, P, {})}
        self.assertEqual("PASS", out["K05"])
        r["fc"]["events"].append((3.5, "GCS Failsafe"))
        out = {c["id"]: c["status"] for c in contracts.evaluate(r, P, {})}
        self.assertEqual("FAIL", out["K05"])

    def test_receiver_present_skips_radio_failsafe(self):
        P = S.effective({"receiver_present": True})
        out = {c["id"]: c["status"] for c in contracts.evaluate(healthy(), P, {})}
        self.assertEqual("NA", out["K06"])

    def test_spurious_failsafe_fails(self):
        r = healthy()
        r["gcs_failsafe"]["spurious"] = [9.0]
        self.assertEqual("FAIL", self.status(r, "K05"))
        r = healthy()
        r["override_expiry"]["spurious"] = [9.0]
        self.assertEqual("FAIL", self.status(r, "K04"))

    def test_deadman_resume_only_checked_when_the_stall_ends_in_the_run(self):
        r = healthy()
        r["deadman"][0].update(resumed_after_s=None)
        self.assertEqual("FAIL", self.status(r, "K13"))
        r["deadman"][0]["stall"] = (1.0, 19.99)
        self.assertEqual("PASS", self.status(r, "K13"))

    def test_joystick_outage_is_not_a_no_control_forecast_case(self):
        r = healthy()
        r["outages"][0].update(tags=["silence:usb_overcurrent+joystick"], no_control_s=0.0)
        self.assertEqual("NA", self.status(r, "K11"))

    def test_bringup_outage_is_not_judged(self):
        r = healthy()
        r["outages"][0]["tags"] = ["silence:bringup"]
        r["outages"][0]["control_restored_after_s"] = None
        self.assertEqual("NA", self.status(r, "K08"))


class TestAnalysisPure(unittest.TestCase):
    def test_gaps_and_match(self):
        import analysis
        self.assertEqual([(1, 2), (2, 5), (5, None)], analysis.gaps([1, 2, 5], 9))
        pairs, miss, extra = analysis.match_events([(1.0, "GCS Failsafe"), (2.0, "Radio Failsafe")], [(1.05, "GCS Failsafe: x"), (4.0, "other")])
        self.assertEqual(1, len(pairs))
        self.assertEqual([("Radio Failsafe", 2.0)], miss)
        self.assertEqual(1, len(extra))

    def test_oracle_matches_model_semantics(self):
        import analysis
        P = S.effective({"rc_override_time": 1.0, "fs_gcs_timeout": 2.0, "rc_fs_timeout": 0.5})
        frames = []
        for i in range(10):      # heartbeats and overrides for 1 s, then silence
            t = i * 0.1
            frames.append({"type": "HEARTBEAT", "sys": 255, "t_out": t, "dropped": False})
            frames.append({"type": "RC_CHANNELS_OVERRIDE", "sys": 255, "t_out": t, "dropped": False, "chans": [1500, 1500, 1400, 1500, 0, 0, 0, 0], "target": 1})
        ev = analysis.oracle(frames, P, 6.0)
        kinds = [(analysis.ev_kind(x), round(t, 1)) for t, x in ev]
        self.assertIn(("RC override started", 0.0), kinds)
        self.assertIn(("RC override expired", 1.9), kinds)         # last override at 0.9 s + RC_OVERRIDE_TIME 1.0
        self.assertIn(("Radio Failsafe", 2.4), kinds)              # + RC_FS_TIMEOUT 0.5
        self.assertIn(("GCS Failsafe", 2.9), kinds)                # last heartbeat 0.9 + FS_GCS_TIMEOUT 2.0

    def test_forecast_flags_thresholds(self):
        import analysis
        P = S.effective({"rc_override_time": 1.0, "fs_gcs_timeout": 2.0, "rc_fs_timeout": 0.5})
        f = analysis.forecast_flags(0.2, P)
        self.assertEqual((False, False, False), (f["override_expired"], f["gcs_failsafe"], f["radio_failsafe"]))
        f = analysis.forecast_flags(5.0, P)
        self.assertEqual((True, True, True), (f["override_expired"], f["gcs_failsafe"], f["radio_failsafe"]))
        self.assertIsNone(analysis.forecast_flags(1.0, P)["override_expired"])

    def test_interval_arithmetic(self):
        import analysis
        self.assertEqual([(0, 1), (3, 5)], analysis.subtract([(0, 5)], [(1, 3)]))
        self.assertAlmostEqual(3.0, analysis.measure([(0, 1), (3, 5)]))


# ---------------------------------------------------------------- integration with the real programs
RESULTS = {}


def _plan_scenario():
    import twin
    fc, P, plan = twin.build("pi5_3a_weak_psu", 1, None, dict(FAST))
    if plan["real_s"] > FAST["budget_s"] + 1.0:      # fail fast instead of replaying a runaway plan
        raise RuntimeError("replay plan %.1fs exceeds the %.1fs budget" % (plan["real_s"], FAST["budget_s"]))
    return fc, P, plan


def _plan_stall():
    P = S.effective(dict(FAST))
    base = {"loss": 0.0, "delay_ms": 1.0, "jitter_ms": 0.3}

    def seg(r0, r1):
        return {"r0": r0, "r1": r1, "silent": False, "loss": 0.0, "delay_ms": 1.0, "jitter_ms": 0.3, "m0": r0, "m1": r1, "tags": []}
    plan = {"base": base, "segs": [seg(0.0, 4.0)], "real_s": 4.0, "outages": [], "model_s": 4.0, "elided_model_s": 0.0, "replayed_model_s": 4.0,
            "truncated_model_s": 0.0, "skipped": [], "windows": [[0, 4.0]], "bad": []}
    return None, P, plan


def _fixture():
    import analysis
    import harness

    def go(key, build, stalls=()):
        fc, P, plan = build()
        trace = harness.run_plan(plan, P, 1, py=PY, input_stalls=stalls, tail_s=0.3)
        res = analysis.analyze(trace, plan, P)
        ctx = {"plan": plan, "forecast": fc, "include_bringup": False}
        RESULTS[key] = {"res": res, "P": P, "plan": plan, "fc": fc, "contracts": contracts.evaluate(res, P, ctx), "trace": trace}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(go, "link", _plan_scenario), ex.submit(go, "stall", _plan_stall, [(1.6, 2.9)])]
        for key, f in zip(("link", "stall"), futs):
            try:
                f.result()
            except Exception as e:      # noqa: BLE001
                RESULTS[key] = {"error": repr(e)}


# The two real-program runs start at import and overlap with the pure tests (wall clock of the whole module ~ the longer run).
_FIX = threading.Thread(target=_fixture, daemon=True) if HAVE_MAV else None
if _FIX:
    _FIX.start()


@unittest.skipUnless(HAVE_MAV, "pymavlink not available in %s: SKIP integration with the real programs" % PY)
class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _FIX.join(40)
        if _FIX.is_alive():
            raise AssertionError("integration fixture still running after 40 s")

    def get(self, key):
        r = RESULTS.get(key)
        self.assertIsNotNone(r, "fixture did not run")
        self.assertNotIn("error", r, r.get("error"))
        return r

    def status(self, key, cid):
        return {c["id"]: c for c in self.get(key)["contracts"]}[cid]

    def test_link_run_has_an_outage_from_the_engine(self):
        r = self.get("link")
        self.assertEqual(1, len(r["res"]["outages"]))
        o = r["res"]["outages"][0]
        self.assertIn("silence:usb_overcurrent", o["tags"])
        self.assertAlmostEqual(FAST["silence_cap_s"], o["played_real_s"], places=1)
        self.assertGreater(o["truncated_model_s"], 10.0)         # the engine's 24 s drop was cut to the cap and reported

    def test_link_run_all_contracts(self):
        bad = [(c["id"], c["detail"]) for c in self.get("link")["contracts"] if c["status"] == "FAIL"]
        self.assertEqual([], bad)

    def test_override_expires_after_rc_override_time(self):
        o = self.get("link")["res"]["outages"][0]
        P = self.get("link")["P"]
        self.assertIsNotNone(o["override_expired_after_s"])
        self.assertAlmostEqual(P["rc_override_time_real"], o["override_expired_after_s"], delta=0.35)

    def test_gcs_failsafe_not_later_than_timeout_plus_margin(self):
        o = self.get("link")["res"]["outages"][0]
        P = self.get("link")["P"]
        self.assertIsNotNone(o["gcs_failsafe_after_s"])
        self.assertLessEqual(o["gcs_failsafe_after_s"], P["fs_gcs_timeout_real"] + 0.45)
        self.assertGreaterEqual(o["gcs_failsafe_after_s"], P["fs_gcs_timeout_real"] - P["hb_period_real"] - 0.2)

    def test_radio_failsafe_follows_override_loss(self):
        o = self.get("link")["res"]["outages"][0]
        P = self.get("link")["P"]
        self.assertAlmostEqual(P["rc_override_time_real"] + P["rc_fs_timeout_real"], o["radio_failsafe_after_s"], delta=0.35)

    def test_control_and_video_recover(self):
        o = self.get("link")["res"]["outages"][0]
        self.assertLess(o["control_restored_after_s"], 1.0)
        self.assertLess(o["video_recover_after_s"], 1.0)
        self.assertAlmostEqual(o["played_real_s"], o["video_freeze_s"], delta=0.35)

    def test_link_loss_does_not_trip_the_bridge_deadman(self):
        b = self.get("link")["res"]["bridge"]
        self.assertEqual([], b["deadman_log"])           # the joystick is fine: only the FC can notice a dead link
        self.assertFalse(b["released_any"])

    def test_rc_without_control_matches_forecast(self):
        o = self.get("link")["res"]["outages"][0]
        self.assertAlmostEqual(o["forecast_no_control_s"], o["no_control_s"], delta=o["control_restored_after_s"] + 0.5)
        self.assertGreater(o["no_control_s"], 0.5)

    def test_single_writer_and_target(self):
        self.assertEqual("PASS", self.status("link", "K01")["status"])
        self.assertEqual("PASS", self.status("link", "K02")["status"])
        self.assertEqual([255], self.get("link")["res"]["bridge"]["sysids"])

    def test_oracle_agrees(self):
        self.assertEqual("PASS", self.status("link", "K07")["status"], self.status("link", "K07")["detail"])
        self.assertGreaterEqual(self.get("link")["res"]["oracle"]["pairs"], 4)

    def test_probe_cross_check(self):
        self.assertEqual("PASS", self.status("link", "K10")["status"], self.status("link", "K10")["detail"])

    def test_deadman_on_joystick_loss(self):
        c = self.status("stall", "K13")
        self.assertEqual("PASS", c["status"], c["detail"])
        d = self.get("stall")["res"]["deadman"][0]
        self.assertAlmostEqual(self.get("stall")["P"]["deadman_ms_real"] / 1000.0, d["deadman_at"], delta=0.35)
        self.assertGreaterEqual(d["throttle_fs_frames"], 1)
        self.assertGreaterEqual(d["release_frames"], 1)
        self.assertEqual(0, d["sticks_after_deadman"])

    def test_deadman_frames_are_throttle_failsafe_then_release(self):
        tr = self.get("stall")["trace"]
        import analysis
        P = self.get("stall")["P"]
        fr = [f for f in analysis.decode(tr["g2f"], P["throttle_ch"], P["failsafe_throttle_us"]) if f["type"] == "RC_CHANNELS_OVERRIDE" and 1.6 <= f["t_in"] <= 2.9]
        seq = [f["cls"] for f in fr]
        first_fs = seq.index("FSTHR")
        self.assertEqual("STICK", seq[first_fs - 1])
        self.assertIn("RELEASE", seq[first_fs:])
        self.assertLess(seq.index("FSTHR"), seq.index("RELEASE"))
        self.assertNotIn("STICK", seq[first_fs:])
        fs = [f for f in fr if f["cls"] == "FSTHR"][0]
        self.assertEqual(P["failsafe_throttle_us"], fs["chans"][P["throttle_ch"] - 1])

    def test_stall_run_other_contracts(self):
        bad = [(c["id"], c["detail"]) for c in self.get("stall")["contracts"] if c["status"] == "FAIL"]
        self.assertEqual([], bad)

    def test_fc_never_saw_a_foreign_writer(self):
        self.assertEqual(0, self.get("link")["res"]["fc"]["ignored_override"])


@unittest.skipUnless(HAVE_MAV, "pymavlink not available: SKIP")
class TestForeignWriter(unittest.TestCase):
    """The FC model ignores an override from a sysid that is not MAV_GCS_SYSID (the wrong-sysid hazard of docs/MAVLINK-ROUTER.md)."""

    def test_apm_model_ignores_foreign_sysid(self):
        sys.path.insert(0, os.path.join(HERE, ".."))
        from apm_model import ApmModel
        fc = ApmModel(mav_gcs_sysid=255)
        self.assertFalse(fc.on_rc_override(77, [1500] * 8, 1.0))
        self.assertFalse(fc.overridden(1.1))
        self.assertTrue(fc.on_rc_override(255, [1500] * 8, 1.0))
        self.assertTrue(fc.overridden(1.1))


if __name__ == "__main__":
    unittest.main(verbosity=1)
