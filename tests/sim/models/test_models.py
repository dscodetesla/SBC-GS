#!/usr/bin/env python3
"""Property, schema and golden tests of the calibratable models (stdlib unittest, no network, < 5 s).

  python3 tests/sim/models/test_models.py              # run
  UPDATE_GOLDEN=1 python3 tests/sim/models/test_models.py   # rewrite golden/*.txt after an intended change
"""
import contextlib
import io
import itertools
import json
import math
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402
import latency_budget  # noqa: E402
import power_model  # noqa: E402
import relay_from_model  # noqa: E402
import rf_model  # noqa: E402

# Ratchet: number of UNMEASURED parameters in params.json. It may only DECREASE (measure on the bench,
# change the provenance, lower this constant). Raising it needs a reviewer's explicit agreement.
UNMEASURED_MAX = 48
GOLDEN = os.path.join(HERE, "golden")
for _k in ("MODEL_PARAMS", "MODEL_MEASURED", "MODEL_SET"):
    os.environ.pop(_k, None)


def P(**sets):
    return common.load({k.replace("__", "."): v for k, v in sets.items()})


def run(mod, argv):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = mod.main(argv)
    return rc, buf.getvalue(), err.getvalue()


class TestSchema(unittest.TestCase):
    def test_params_valid(self):
        self.assertEqual(common.validate(common.load_doc()), [])

    def test_unmeasured_ratchet(self):
        n = common.count_unmeasured(common.load_doc())
        print("UNMEASURED parameters: %d (ratchet max %d)" % (n, UNMEASURED_MAX))
        self.assertLessEqual(n, UNMEASURED_MAX, "UNMEASURED count grew: the ratchet may only decrease")
        if n < UNMEASURED_MAX:
            print("NOTE: lower UNMEASURED_MAX to %d" % n)

    def test_provenance_required_and_src_is_url(self):
        doc = common.load_doc()
        bad = json.loads(json.dumps(doc))
        bad["sections"]["rf"]["params"]["tx_power_dbm"].pop("provenance")
        self.assertTrue(any("provenance" in e for e in common.validate(bad)))
        bad = json.loads(json.dumps(doc))
        bad["sections"]["rf"]["params"]["fec_k"]["source"] = "somewhere"
        self.assertTrue(any("https" in e for e in common.validate(bad)))
        bad = json.loads(json.dumps(doc))
        bad["sections"]["rf"]["params"]["tx_power_dbm"]["provenance"] = "GUESS"
        self.assertTrue(any("bad provenance" in e for e in common.validate(bad)))

    def test_every_unmeasured_has_calibration(self):
        bad = json.loads(json.dumps(common.load_doc()))
        bad["sections"]["rf"]["calibration"] = [c for c in bad["sections"]["rf"]["calibration"] if c["id"] != "rf-power"]
        self.assertTrue(any("no calibration entry" in e for e in common.validate(bad)))

    def test_sections_have_assumptions_and_calibration(self):
        doc = common.load_doc()
        for sec in ("rf", "power", "latency"):
            self.assertTrue(doc["sections"][sec]["assumptions"], sec)
            self.assertTrue(doc["sections"][sec]["calibration"], sec)


class TestRF(unittest.TestCase):
    def test_per_monotone_in_snr(self):
        for vht, bw, top in ((False, 20, 7), (True, 40, 9)):
            for mcs in range(top + 1):
                prev = 1.1
                for i in range(-100, 500):
                    v = rf_model.per_ideal(mcs, i / 10.0, 1456, vht)
                    self.assertLessEqual(v, prev + 1e-15, (mcs, i))
                    self.assertTrue(0.0 <= v <= 1.0)
                    prev = v

    def test_ber_anchor_values(self):
        # textbook AWGN values: BPSK Pb = 0.5 erfc(sqrt(snr)); QPSK uses snr/2; BER never exceeds 0.5
        self.assertAlmostEqual(rf_model._ber(1, 2, 1.0), 0.5 * math.erfc(1.0), places=12)
        self.assertAlmostEqual(rf_model._ber(1, 2, 1.0), 0.0786496, places=6)
        self.assertAlmostEqual(rf_model._ber(2, 4, 2.0), 0.0786496, places=6)
        self.assertLess(rf_model._ber(1, 2, 10 ** 0.96), 1.1e-5)  # ~9.6 dB: BPSK 1e-5
        for m in (2, 4, 16, 64, 256):
            for db in range(-20, 40, 3):
                b = rf_model._ber(0, m, 10 ** (db / 10.0))
                self.assertTrue(0.0 <= b <= 0.5 + 1e-12, (m, db, b))
                if db > -20:
                    self.assertLessEqual(b, rf_model._ber(0, m, 10 ** ((db - 3) / 10.0)) + 1e-15)

    def test_per_anchor_values(self):
        # ideal receiver, 1000-byte frame: very low SNR always fails, high SNR always passes, MCS0 knee at a few dB
        for mcs in range(8):
            self.assertEqual(rf_model.per_ideal(mcs, -10.0, 1000), 1.0)
            self.assertLess(rf_model.per_ideal(mcs, 45.0, 1000), 1e-6)
        knee = [db / 10.0 for db in range(-100, 400) if rf_model.per_ideal(0, db / 10.0, 1000) <= 0.1][0]
        self.assertTrue(2.0 <= knee <= 6.0, knee)

    def test_required_snr_increases_with_mcs(self):
        p = P()
        s = [rf_model.snr_for_per(p, m) for m in range(8)]
        self.assertEqual(s, sorted(s))
        self.assertGreater(s[7] - s[0], 10.0)

    def test_loss_monotone_in_distance(self):
        for fading, lm, mcs in itertools.product(("none", "rayleigh", "rician"), ("iid", "ge"), (0, 1, 4, 7)):
            p = P(rf__fading_model=fading, rf__loss_model=lm)
            prev = -1.0
            for d in [50 * 1.15 ** i for i in range(60)]:
                e = rf_model.evaluate(p, d, mcs, 8, 12)
                self.assertGreaterEqual(e["res"], prev - 1e-12, (fading, lm, mcs, d))
                self.assertGreaterEqual(e["per"], 0.0)
                prev = e["res"]

    def test_fec_never_worsens(self):
        for p in (0.001, 0.01, 0.05, 0.1, 0.2, 0.4, 0.7):
            for k in (1, 4, 8):
                base = rf_model.residual_iid(p, k, k)
                self.assertAlmostEqual(base, p, places=12)  # no parity = raw loss
                prev = base
                for n in range(k, k + 9):
                    r = rf_model.residual_iid(p, k, n)
                    self.assertLessEqual(r, prev + 1e-15, (p, k, n))
                    prev = r
            for n in (12, 16):
                prev = 2.0
                for k in range(n, 0, -1):  # fewer data per block = more parity
                    r = rf_model.residual_iid(p, k, n)
                    self.assertLessEqual(r, prev if prev <= 1 else 1.0 + 1e-15)
                    prev = r

    def test_fec_never_worsens_burst(self):
        for p in (0.01, 0.05, 0.2):
            for burst in (1.0, 4.0, 12.0):
                prev = rf_model.residual_ge(p, burst, 8, 8)
                self.assertAlmostEqual(prev, p, places=9)
                for n in range(9, 17):
                    r = rf_model.residual_ge(p, burst, 8, n)
                    self.assertLessEqual(r, prev + 1e-12, (p, burst, n))
                    prev = r

    def test_iid_formula_matches_enumeration(self):
        p, k, n = 0.3, 3, 5
        tot = 0.0
        for pat in itertools.product((0, 1), repeat=n):  # 1 = lost
            pr = math.prod(p if x else 1 - p for x in pat)
            if sum(pat) > n - k:
                tot += pr * sum(pat[:k]) / k
        self.assertAlmostEqual(rf_model.residual_iid(p, k, n), tot, places=12)

    def test_ge_dp_matches_enumeration(self):
        p, burst, k, n = 0.2, 4.0, 3, 6
        p_bg = 1 / burst
        p_gb = p_bg * p / (1 - p)
        pi_b = p
        tot = 0.0
        for pat in itertools.product((0, 1), repeat=n):
            pr = pi_b if pat[0] else 1 - pi_b
            for a, b in zip(pat, pat[1:]):
                pr *= (p_bg if not b else 1 - p_bg) if a else (p_gb if b else 1 - p_gb)
            if sum(pat) > n - k:
                tot += pr * sum(pat[:k]) / k
        self.assertAlmostEqual(rf_model.residual_ge(p, burst, k, n), tot, places=12)

    def test_bursts_hurt(self):
        for p in (0.02, 0.1):
            self.assertGreaterEqual(rf_model.residual_ge(p, 8.0, 8, 12), rf_model.residual_iid(p, 8, 12))

    def test_range_is_the_threshold(self):
        p = P()
        for mcs in (0, 1, 3, 7):
            r, capped = rf_model.max_range(p, mcs, 8, 12)
            self.assertFalse(capped)
            self.assertLessEqual(rf_model.evaluate(p, r, mcs, 8, 12)["res"], 0.01 + 1e-9)
            self.assertGreater(rf_model.evaluate(p, r * 1.05, mcs, 8, 12)["res"], 0.01)

    def test_range_decreases_with_mcs_and_grows_with_power(self):
        p = P()
        rs = [rf_model.max_range(p, m, 8, 12)[0] for m in range(8)]
        self.assertEqual(rs, sorted(rs, reverse=True))
        r0 = rf_model.max_range(P(), 1, 8, 12)[0]
        r1 = rf_model.max_range(P(rf__tx_power_dbm=30.0), 1, 8, 12)[0]
        self.assertGreater(r1, r0)

    def test_interference_reduces_snr_and_range(self):
        quiet, noisy = P(), P(rf__interference_dbm=-80.0)
        self.assertLess(rf_model.snr_db(noisy, 500), rf_model.snr_db(quiet, 500))
        self.assertLess(rf_model.max_range(noisy, 1, 8, 12)[0], rf_model.max_range(quiet, 1, 8, 12)[0])

    def test_thermal_noise_by_bandwidth(self):
        self.assertAlmostEqual(rf_model.noise_dbm(P(rf__bandwidth_mhz=40)) - rf_model.noise_dbm(P(rf__bandwidth_mhz=20)),
                               10 * math.log10(2), places=9)

    def test_fspl_reference(self):
        self.assertAlmostEqual(rf_model.fspl_db(1000.0, 1000.0), 92.44, places=2)  # 1 km, 1 GHz textbook value

    def test_airtime_and_rate(self):
        self.assertAlmostEqual(rf_model.phy_rate_mbps(0, 20, False), 6.5)
        self.assertAlmostEqual(rf_model.phy_rate_mbps(7, 20, True), 72.2, places=1)
        self.assertGreater(rf_model.airtime_us(1456, 0, 20, False, False, 1), rf_model.airtime_us(1456, 7, 20, False, False, 1))
        with self.assertRaises(common.ParamError):
            rf_model.phy_rate_mbps(9, 20, False, True)

    def test_infeasible_flag(self):
        p = P(video__bitrate_kbps=20000)
        self.assertGreater(rf_model.utilisation(p, 1, 8, 12), 1.0)


class TestOverrides(unittest.TestCase):
    def test_set_overrides_value_and_provenance(self):
        p = P(rf__tx_power_dbm=27.0)
        self.assertEqual(p.get("rf.tx_power_dbm"), 27.0)
        self.assertEqual(p.prov("rf.tx_power_dbm"), "OVERRIDE")

    def test_measured_overlay_via_env(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"rf.tx_power_dbm": {"value": 17.5, "source": "meter"}}, f)
        os.environ["MODEL_MEASURED"] = f.name
        try:
            p = common.load()
            self.assertEqual(p.get("rf.tx_power_dbm"), 17.5)
            self.assertEqual(p.prov("rf.tx_power_dbm"), "MEASURED_HW")
            rf_model.snr_db(p, 100)
            self.assertNotIn("rf.tx_power_dbm", p.used_unmeasured())
        finally:
            del os.environ["MODEL_MEASURED"]
            os.unlink(f.name)

    def test_model_set_env(self):
        os.environ["MODEL_SET"] = "rf.path_loss_exponent=3.0;rf.fading_model=\"rayleigh\""
        try:
            p = common.load()
            self.assertEqual(p.get("rf.path_loss_exponent"), 3.0)
            self.assertEqual(p.get("rf.fading_model"), "rayleigh")
        finally:
            del os.environ["MODEL_SET"]

    def test_unknown_key_rejected(self):
        with self.assertRaises(common.ParamError):
            common.load(["rf.nonexistent=1"])

    def test_footer_lists_unmeasured_used(self):
        rc, out, _ = run(rf_model, ["range", "--mcs", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("# depends on", out)
        self.assertIn("rf.tx_power_dbm", out)


class TestRelay(unittest.TestCase):
    def test_flags_exist_in_air_relay(self):
        with open(os.path.join(HERE, "..", "air_relay.py"), encoding="utf-8") as f:
            src = f.read()
        for flag in ("--loss", "--delay-ms", "--jitter-ms", "--seed", "--duration", "--src", "--dst"):
            self.assertIn('"%s"' % flag, src, flag)

    def test_args_line(self):
        rc, out, _ = run(relay_from_model, ["--distance", "1050", "--mcs", "1", "--fec", "8/12"])
        self.assertEqual(rc, 0)
        m = re.fullmatch(r"--loss (\d\.\d{6}) --delay-ms (\d+\.\d{4}) --jitter-ms (\d+\.\d{4}) --seed (\d+)\n", out)
        self.assertIsNotNone(m, out)
        self.assertTrue(0.0 <= float(m.group(1)) <= 1.0)
        p = P()
        self.assertAlmostEqual(float(m.group(1)), rf_model.frame_per(p, 1, rf_model.snr_db(p, 1050)), places=5)

    def test_ramp_loss_monotone_and_cmds(self):
        rc, out, _ = run(relay_from_model, ["--ramp", "600:1300:60:7", "--mcs", "1", "--fec", "8/12", "--format", "cmds"])
        self.assertEqual(rc, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 7)
        loss = [float(re.search(r"--loss (\S+)", l).group(1)) for l in lines]
        self.assertEqual(loss, sorted(loss))
        self.assertTrue(all("air_relay.py" in l and "--duration" in l for l in lines))

    def test_ge_equivalent_iid_matches_residual(self):
        p = P(rf__loss_model="ge")
        per = rf_model.frame_per(p, 1, rf_model.snr_db(p, 1000))
        eq = rf_model.iid_equivalent(p, per, 8, 12)
        self.assertAlmostEqual(rf_model.residual_iid(eq, 8, 12), rf_model.residual(p, per, 8, 12), places=9)
        self.assertGreaterEqual(eq, per)

    def test_json_schedule(self):
        rc, out, _ = run(relay_from_model, ["--ramp", "500:900:20:4", "--format", "json"])
        d = json.loads(out)
        self.assertEqual(d["schema"], "sbc-gs-relay-schedule/1")
        self.assertEqual(len(d["segments"]), 4)
        self.assertAlmostEqual(sum(s["duration_s"] for s in d["segments"]), 20.0)


class TestLatency(unittest.TestCase):
    def test_terms_sum_to_total(self):
        p = P()
        for (board, codec), fps, lossy in itertools.product(sorted(latency_budget.DECODERS), (30, 60), (False, True)):
            b = latency_budget.budget(p, board, codec, fps, 1920, 1080, 8000, 3, 8, 12, lossy)
            for i in range(3):
                self.assertAlmostEqual(sum(b["terms"][t][i] for t in latency_budget.TERMS), b["total"][i], places=9)
            for t in latency_budget.TERMS:
                mn, ty, mx = b["terms"][t]
                self.assertLessEqual(mn, ty + 1e-12, t)
                self.assertLessEqual(ty, mx + 1e-12, t)
                self.assertGreaterEqual(mn, 0.0, t)

    def test_lossy_adds_fec_wait_only(self):
        p = P()
        a = latency_budget.budget(p, "pi4", "h265", 30, 1280, 720, 4000, 1, 8, 12, False)
        b = latency_budget.budget(p, "pi4", "h265", 30, 1280, 720, 4000, 1, 8, 12, True)
        self.assertEqual(a["terms"]["fec_wait"], (0.0, 0.0, 0.0))
        self.assertGreater(b["terms"]["fec_wait"][2], 0.0)
        self.assertGreater(b["total"][1], a["total"][1])
        for t in latency_budget.TERMS:
            if t != "fec_wait":
                self.assertEqual(a["terms"][t], b["terms"][t])

    def test_airtime_term_grows_with_bitrate_and_falls_with_mcs(self):
        p = P()
        lo = latency_budget.budget(p, "pi4", "h265", 30, 1280, 720, 2000, 3, 8, 12)["terms"]["radio"][1]
        hi = latency_budget.budget(p, "pi4", "h265", 30, 1280, 720, 8000, 3, 8, 12)["terms"]["radio"][1]
        fast = latency_budget.budget(p, "pi4", "h265", 30, 1280, 720, 8000, 7, 8, 12)["terms"]["radio"][1]
        self.assertGreater(hi, lo)
        self.assertLess(fast, hi)

    def test_infeasible_when_link_too_slow(self):
        b = latency_budget.budget(P(), "pi4", "h265", 30, 1920, 1080, 12000, 1, 8, 12)
        self.assertFalse(b["feasible"])

    def test_sw_reference_is_measured_sim_and_scales(self):
        p = P()
        self.assertEqual(p.prov("latency.sw_ref_h264_ms"), "MEASURED_SIM")
        self.assertEqual(p.get("latency.sw_ref_h264_ms"), 5.0)  # tests/sim/video_latency.py x86 p50 (docs/TESTABILITY.md)
        lo = latency_budget.decode_ms(p, "pi5", "h264", 640, 360, 30)[1]
        hi = latency_budget.decode_ms(p, "pi5", "h264", 1920, 1080, 30)[1]
        self.assertAlmostEqual(hi / lo, 9.0, delta=0.5)

    def test_measured_override_changes_budget(self):
        a = latency_budget.budget(P(), "pi4", "h265", 30, 1920, 1080, 4000, 1, 8, 12)["total"][1]
        b = latency_budget.budget(P(**{"latency__decoders__pi4_h265_stateless__proc_ms_1080p": 30.0}), "pi4", "h265",
                                  30, 1920, 1080, 4000, 1, 8, 12)["total"][1]
        self.assertAlmostEqual(b - a, 30.0 - 8.0, places=6)

    def test_protocol_prints(self):
        rc, out, _ = run(latency_budget, ["protocol"])
        self.assertEqual(rc, 0)
        self.assertIn("photodiode", out)


class TestPower(unittest.TestCase):
    def test_margin_sign_matches_flags(self):
        p = P()
        for board, psu, state, w in itertools.product(power_model.BOARDS, (1.0, 2.0, 3.0, 5.0), ("idle", "rx", "tx"),
                                                      ((), ("fc", "webcam", "fan"))):
            b = power_model.budget(p, board, psu, 1, state, w)
            self.assertEqual(b["psu_margin_a"] < 0, "PSU_OVER" in b["flags"])
            self.assertEqual(b["usb_margin_a"] < 0, "USB_OVER" in b["flags"])
            self.assertAlmostEqual(b["psu_margin_a"], psu - b["total_a"], places=12)
            self.assertEqual(b["verdict"] == "FAIL", bool(set(b["flags"]) & {"PSU_OVER", "USB_OVER", "UNDERVOLT"}))

    def test_adding_device_reduces_margin(self):
        p = P()
        a = power_model.budget(p, "pi4", 3.0, 1, "rx", ())
        b = power_model.budget(p, "pi4", 3.0, 1, "rx", ("webcam",))
        self.assertAlmostEqual(a["psu_margin_a"] - b["psu_margin_a"], 0.25, places=9)
        self.assertAlmostEqual(a["usb_margin_a"] - b["usb_margin_a"], 0.25, places=9)
        c = power_model.budget(p, "pi4", 3.0, 1, "rx", ("fan",))  # fan: PSU only
        self.assertAlmostEqual(a["usb_margin_a"], c["usb_margin_a"], places=12)
        self.assertLess(c["psu_margin_a"], a["psu_margin_a"])

    def test_pi5_usb_budget_depends_on_psu(self):
        p = P()
        self.assertEqual(power_model.usb_budget_a(p, "pi5", 5.0), 1.6)
        self.assertEqual(power_model.usb_budget_a(p, "pi5", 3.0), 0.6)
        self.assertEqual(power_model.usb_budget_a(p, "pi5", 3.0, usb_max_current=True), 1.6)
        self.assertEqual(power_model.usb_budget_a(p, "pi4", 3.0), 1.2)
        self.assertEqual(power_model.usb_budget_a(p, "pi3bp", 2.5), 1.2)

    def test_sag_follows_resistance(self):
        a = power_model.budget(P(), "pi4", 3.0, 1, "tx", ())["v_board"]
        b = power_model.budget(P(power__cable_resistance_ohm=0.6), "pi4", 3.0, 1, "tx", ())
        self.assertLess(b["v_board"], a)
        self.assertIn("UNDERVOLT", b["flags"])

    def test_throttled_bits(self):
        self.assertEqual(power_model.decode_throttled(0x50005),
                         ["Undervoltage detected", "Currently throttled", "Undervoltage has occurred", "Throttling has occurred"])
        self.assertEqual(power_model.decode_throttled(0), [])
        self.assertEqual(sorted(power_model.THROTTLED_BITS), [0, 1, 2, 3, 16, 17, 18, 19])
        for b, name in power_model.THROTTLED_BITS.items():
            self.assertEqual(power_model.decode_throttled(1 << b), [name])

    def test_log_parser(self):
        log = "t=1 throttled=0x0\nt=2 throttled=0x50005\n[ 12.3] hwmon: Undervoltage detected!\n[ 15.0] Voltage normalised\n"
        vals, uv, ok = power_model.parse_log(log)
        self.assertEqual(vals, [0, 0x50005])
        self.assertEqual((uv, ok), (1, 1))
        s = power_model.summarize_log(vals, uv)
        self.assertTrue(s["undervoltage_seen"] and s["throttled_seen"])
        self.assertFalse(power_model.summarize_log([0, 0], 0)["undervoltage_seen"])

    def test_report_vs_log_agreement(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("throttled=0x50005\n")
        try:
            rc, out, _ = run(power_model, ["report", "--board", "pi5", "--psu-a", "5", "--throttled-log", f.name])
            self.assertEqual(rc, 0)
            self.assertIn("MODEL_OPTIMISTIC", out)
        finally:
            os.unlink(f.name)

    def test_brownout_schema_and_semantics(self):
        kinds = {"undervoltage", "voltage_ok", "throttled", "usb_drop", "usb_return"}
        p = P()
        for name, sc in power_model.SCENARIOS.items():
            d = power_model.build_events(p, name, sc, 1)
            self.assertEqual(d["schema"], "sbc-gs-usbfault/1")
            t_prev, down = -1.0, set()
            for e in d["events"]:
                self.assertIn(e["kind"], kinds)
                self.assertGreaterEqual(e["t_s"], t_prev)
                t_prev = e["t_s"]
                self.assertLessEqual(e["t_s"], sc["duration_s"] + 1e-9)
                if e["kind"] == "usb_drop":
                    self.assertNotIn(e["device"], down)
                    self.assertIn(e["reason"], ("undervoltage", "overcurrent"))
                    down.add(e["device"])
                if e["kind"] == "usb_return":
                    self.assertIn(e["device"], down)
                    down.discard(e["device"])
                if e["kind"] == "throttled":
                    self.assertEqual(power_model.decode_throttled(int(e["value"], 16)), e["bits"])
        self.assertEqual(power_model.build_events(p, "pi5_5a_ok", power_model.SCENARIOS["pi5_5a_ok"], 1)["events"], [])
        d = power_model.build_events(p, "pi5_3a_tx", power_model.SCENARIOS["pi5_3a_tx"], 1)
        self.assertTrue(any(e["kind"] == "usb_drop" and e["reason"] == "overcurrent" for e in d["events"]))
        d2 = power_model.build_events(p, "pi4_3a_dip", power_model.SCENARIOS["pi4_3a_dip"], 1)
        self.assertTrue(any(e["kind"] == "usb_drop" and e["reason"] == "undervoltage" for e in d2["events"]))

    def test_brownout_deterministic_and_seeded(self):
        p = P()
        sc = power_model.SCENARIOS["pi4_3a_dip"]
        a = power_model.build_events(p, "x", sc, 5)
        b = power_model.build_events(p, "x", sc, 5)
        c = power_model.build_events(p, "x", sc, 6)
        self.assertEqual(a, b)
        self.assertNotEqual(a["events"], c["events"])

    def test_ingest_overlay_feeds_model(self):
        with tempfile.TemporaryDirectory() as d:
            csvp = os.path.join(d, "m.csv")
            with open(csvp, "w") as f:
                f.write("device,state,amps\nrtl8812,tx,0.70\nrtl8812,tx,0.80\nrtl8812,tx,0.90\nfc,,0.08\nboard_pi5,idle,0.55\n")
            rc, out, _ = run(power_model, ["ingest", "--csv", csvp])
            self.assertEqual(rc, 0)
            ov = json.loads(out)
            self.assertEqual(ov["power.devices.rtl8812_tx_a"]["value"], 0.8)
            self.assertIn("power.boards.pi5.board_idle_a", ov)
            p = common.Params(common.load_doc(), None, ov)
            self.assertEqual(p.get("power.devices.rtl8812_tx_a"), 0.8)
            self.assertEqual(p.prov("power.devices.rtl8812_tx_a"), "MEASURED_HW")


class TestGolden(unittest.TestCase):
    """Three reference scenarios; compare the full text output with golden/*.txt."""

    def check(self, name, text):
        path = os.path.join(GOLDEN, name)
        if os.environ.get("UPDATE_GOLDEN"):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return
        with open(path, encoding="utf-8") as f:
            want = f.read()
        self.assertEqual(text, want, "golden mismatch: %s (intended change? UPDATE_GOLDEN=1)" % name)

    def test_scenario_rf_mcs1_fec8_12(self):
        out = ""
        for argv in (["table", "--mcs", "1", "--fec", "8/12", "--dists", "300,700,900,1000,1050,1100,1300,2000"],
                     ["sweep", "--fecs", "1/1,8/12"]):
            rc, o, _ = run(rf_model, argv)
            self.assertEqual(rc, 0)
            out += o
        rc, o, _ = run(relay_from_model, ["--distance", "1050", "--mcs", "1", "--fec", "8/12", "--seed", "7"])
        out += "relay_args: " + o
        self.check("rf_mcs1_fec8_12.txt", out)

    def test_scenario_power_pi5_psu(self):
        out = ""
        for argv in (["report", "--board", "pi5", "--psu-a", "3", "--with", "fc,webcam,fan"],
                     ["report", "--board", "pi5", "--psu-a", "5", "--with", "fc,webcam,fan", "--peak"],
                     ["report", "--board", "pi4", "--psu-a", "3", "--with", "fc,fan", "--peak"]):
            rc, o, _ = run(power_model, argv)
            self.assertEqual(rc, 0)
            out += o + "\n"
        self.check("power_pi5_psu.txt", out)

    def test_scenario_latency_pi4_h265(self):
        out = ""
        for argv in (["budget", "--board", "pi4", "--codec", "h265", "--res", "1920x1080", "--bitrate", "8000", "--mcs", "3",
                      "--fec", "8/12", "--fps", "30", "--lossy"],
                     ["matrix", "--res", "1920x1080", "--bitrate", "8000", "--mcs", "3"]):
            rc, o, _ = run(latency_budget, argv)
            self.assertEqual(rc, 0)
            out += o + "\n"
        self.check("latency_pi4_h265_1080p.txt", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
