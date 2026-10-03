#!/usr/bin/env python3
"""Tests of the stochastic scenario engine (stdlib unittest, no network, a few seconds).

  python3 tests/sim/models/test_degrade.py                    # run
  UPDATE_GOLDEN=1 python3 tests/sim/models/test_degrade.py    # rewrite golden/scenario_*.txt and golden/sensitivity_*.txt
Covers: priors/provenance schema + ratchets, distributions, seeded reproducibility, percentile/probability sanity, monotonicity in
stress, nonlinearity properties, consistency with rf_model, USB/power state machines, bring-up, timing, GPIO bounce + the REAL
algorithm of gs/button.sh, catalog schema, documentation consistency, goldens (4 scenarios + 2 sensitivity tables).
"""
import json
import math
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common  # noqa: E402
import degrade_model as dm  # noqa: E402
import gpio_bounce as gb  # noqa: E402
import power_model  # noqa: E402
import priors  # noqa: E402
import rf_model  # noqa: E402
import scenario_engine as se  # noqa: E402
from priors import Rng, Theta  # noqa: E402

# Ratchets of params.degrade.json: both may only DECREASE (measure on the bench, change the provenance, lower the constant).
DEGRADE_UNMEASURED_MAX = 48
DEGRADE_SYNTH_MAX = 68
GOLDEN = os.path.join(HERE, "golden")
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
for _k in ("MODEL_PARAMS", "MODEL_MEASURED", "MODEL_SET", "DEGRADE_PARAMS", "DEGRADE_MEASURED", "DEGRADE_SET"):
    os.environ.pop(_k, None)


def engine(name="nominal_pi5_5a_150m", sets=None, dists=None, cfg=None):
    return se.Engine(se.load_scenario(name), sets, dists, cfg_over=cfg)


FAST = {"duration_s": 200.0, "dt_s": 20.0}


def mean_of(res, key):
    return sum(r[key] for r in res) / len(res)


def prob_of(res, mode):
    return sum(1 for r in res if r["flags"][mode]) / len(res)


class TestPriors(unittest.TestCase):
    def test_degrade_params_valid(self):
        doc = priors.load_degrade_doc()
        self.assertEqual(priors.validate_degrade(doc, set(common.load().leaves)), [])

    def test_degrade_ratchets(self):
        doc = priors.load_degrade_doc()
        u, s = priors.count_prov(doc, "UNMEASURED"), priors.count_prov(doc, "SYNTH")
        print("UNMEASURED parameters (degrade file): %d (ratchet max %d); SYNTH: %d (ratchet max %d)"
              % (u, DEGRADE_UNMEASURED_MAX, s, DEGRADE_SYNTH_MAX))
        self.assertLessEqual(u, DEGRADE_UNMEASURED_MAX, "degrade UNMEASURED grew: the ratchet may only decrease")
        self.assertLessEqual(s, DEGRADE_SYNTH_MAX, "degrade SYNTH grew: the ratchet may only decrease")

    def test_schema_rejects_bad_files(self):
        doc = priors.load_degrade_doc()
        bad = json.loads(json.dumps(doc))
        bad["sections"]["hw"]["params"]["pa_p1db_out_dbm"]["provenance"] = "GUESS"
        self.assertTrue(any("bad provenance" in e for e in priors.validate_degrade(bad)))
        bad = json.loads(json.dumps(doc))
        del bad["sections"]["hw"]["params"]["pa_rapp_p"]["note"]
        self.assertTrue(any("needs a note" in e for e in priors.validate_degrade(bad)))
        bad = json.loads(json.dumps(doc))
        bad["sections"]["hw"]["calibration"] = [c for c in bad["sections"]["hw"]["calibration"] if c["id"] != "hw-antenna"]
        self.assertTrue(any("no calibration entry" in e for e in priors.validate_degrade(bad)))
        bad = json.loads(json.dumps(doc))
        bad["sections"]["hw"]["params"]["pa_rapp_p"]["dist"] = {"kind": "uniform", "lo": 3, "hi": 1}
        self.assertTrue(any("bad dist" in e for e in priors.validate_degrade(bad)))
        bad = json.loads(json.dumps(doc))
        bad["base_sampled"].append("rf.nonexistent")
        self.assertTrue(any("unknown base parameter" in e for e in priors.validate_degrade(bad, set(common.load().leaves))))

    def test_every_param_has_distribution_and_provenance_label(self):
        d = priors.load_degrade()
        for k, lf in d.leaves.items():
            self.assertIn(lf["provenance"], priors.PROVENANCE, k)
            self.assertIn(lf["dist"]["kind"], priors.KINDS, k)
        kinds = {lf["dist"]["kind"] for lf in d.leaves.values()}
        self.assertTrue({"point", "uniform", "normal", "lognormal", "beta"} <= kinds)

    def test_dist_quantiles_monotone_and_bounded(self):
        specs = [{"kind": "uniform", "lo": 2, "hi": 5}, {"kind": "normal", "mu": 1, "sigma": 2, "lo": -1, "hi": 4},
                 {"kind": "lognormal", "median": 3, "sigma": 0.5}, {"kind": "beta", "a": 2, "b": 8},
                 {"kind": "beta", "a": 1, "b": 4999}, {"kind": "beta", "a": 2, "b": 3, "lo": 10, "hi": 20},
                 {"kind": "triangular", "lo": 1, "mode": 2, "hi": 6}]
        for sp in specs:
            d = priors.Dist(sp, 1.0)
            prev = -1e18
            for i in range(1, 100):
                x = d.ppf(i / 100.0)
                self.assertGreaterEqual(x, prev - 1e-12, (sp, i))
                prev = x
            if sp["kind"] == "beta":
                self.assertTrue(sp.get("lo", 0.0) <= d.ppf(0.001) and d.ppf(0.999) <= sp.get("hi", 1.0))

    def test_dist_means_match_sampling(self):
        r = Rng(5)
        for sp in ({"kind": "uniform", "lo": 2, "hi": 5}, {"kind": "normal", "mu": 1, "sigma": 2}, {"kind": "lognormal", "median": 3, "sigma": 0.4},
                   {"kind": "beta", "a": 2, "b": 8}, {"kind": "triangular", "lo": 1, "mode": 2, "hi": 6}):
            d = priors.Dist(sp, 1.0)
            m = sum(d.ppf(r.u()) for _ in range(6000)) / 6000.0
            self.assertAlmostEqual(m, d.mean(), delta=0.04 * (abs(d.mean()) + 0.5), msg=str(sp))

    def test_betainc_anchors(self):
        self.assertAlmostEqual(priors.betainc(2, 2, 0.5), 0.5, places=9)
        self.assertAlmostEqual(priors.betainc(1, 1, 0.3), 0.3, places=9)
        self.assertAlmostEqual(priors.beta_ppf_exact(2, 2, 0.5), 0.5, places=9)
        self.assertAlmostEqual(priors.beta_ppf(2, 8, 0.5), priors.beta_ppf_exact(2, 8, 0.5), delta=0.01)

    def test_bad_dist_specs_rejected(self):
        for sp in ({"kind": "uniform", "lo": 2, "hi": 2}, {"kind": "normal", "mu": 0, "sigma": 0}, {"kind": "beta", "a": 0, "b": 1},
                   {"kind": "weibull"}, {"kind": "triangular", "lo": 3, "mode": 2, "hi": 4}):
            with self.assertRaises(common.ParamError):
                priors.Dist(sp)

    def test_measured_overlay_replaces_prior(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"hw.pa_p1db_out_dbm": {"value": 24.5, "source": "meter test"}}, f)
        os.environ["DEGRADE_MEASURED"] = f.name
        try:
            deg = priors.load_degrade()
            self.assertEqual(deg.prov("hw.pa_p1db_out_dbm"), "MEASURED_HW")
            eng = se.Engine(se.load_scenario("nominal_pi5_5a_150m"), deg=deg)
            self.assertNotIn("hw.pa_p1db_out_dbm", eng.space.keys)
            self.assertEqual(eng.space.theta([0.3] * len(eng.space.dims)).get("hw.pa_p1db_out_dbm"), 24.5)
            self.assertEqual(priors.count_prov({"sections": {"x": {"params": deg.doc["sections"]["hw"]["params"]}}}, "MEASURED_HW"), 1)
        finally:
            del os.environ["DEGRADE_MEASURED"]
            os.unlink(f.name)

    def test_degrade_set_env_and_pinned(self):
        os.environ["DEGRADE_SET"] = "hw.thermal_shutdown_c=99"
        try:
            deg = priors.load_degrade()
            self.assertEqual(deg.get("hw.thermal_shutdown_c"), 99)
            self.assertEqual(deg.prov("hw.thermal_shutdown_c"), "OVERRIDE")
        finally:
            del os.environ["DEGRADE_SET"]
        eng = engine(sets={"ext.ambient_c": 33.0})
        self.assertNotIn("ext.ambient_c", eng.space.keys)
        self.assertEqual(eng.space.theta([0.9] * len(eng.space.dims)).get("ext.ambient_c"), 33.0)
        with self.assertRaises(common.ParamError):
            engine(sets={"no.such_key": 1})

    def test_antithetic_pairs(self):
        for s in (1, 2, 3):
            a, b = Rng(s, False), Rng(s, True)
            for _ in range(20):
                self.assertAlmostEqual(a.u() + b.u(), 1.0, places=12)
        eng = engine()
        t0, _ = eng.draw(0, 7, True)
        t1, _ = eng.draw(1, 7, True)
        k = "hw.pa_rapp_p"  # uniform(1.5, 4): antithetic draws mirror around the mean
        self.assertAlmostEqual(t0.get(k) + t1.get(k), 5.5, places=9)


class TestEngine(unittest.TestCase):
    def test_reproducible_and_seed_sensitive(self):
        e = engine(cfg=FAST)
        a, b, c = e.run(12, 3), e.run(12, 3), e.run(12, 4)
        self.assertEqual(a, b)
        self.assertNotEqual([r["residual"] for r in a], [r["residual"] for r in c])
        t1 = se.fmt_report(e, 12, 3, False, se.summarize(e, a))
        t2 = se.fmt_report(e, 12, 3, False, se.summarize(e, b))
        self.assertEqual(t1, t2)

    def test_percentiles_ordered_probabilities_bounded(self):
        for name in ("nominal_pi5_5a_150m", "pi5_3a_weak_psu", "hot_day_closed_case", "close_range_agc", "button_bouncy_switch"):
            e = engine(name, cfg=FAST)
            s = se.summarize(e, e.run(12, 1))
            for key, o in s["outputs"].items():
                p = [o["p"][q] for q in se.QS]
                self.assertEqual(p, sorted(p), (name, key))
                if o["unit"] == "frac":
                    self.assertTrue(all(0.0 <= x <= 1.0 for x in p), (name, key, p))
            for m, v in s["modes"].items():
                self.assertTrue(0.0 <= v <= 1.0, (name, m))
            if e.kind == "link":
                self.assertTrue(0.0 <= s["no_failure"] <= 1.0)

    def test_antithetic_runs_and_differs_from_plain(self):
        e = engine(cfg=FAST)
        a = e.run(10, 2, anti=True)
        self.assertEqual(a, e.run(10, 2, anti=True))
        self.assertNotEqual(a, e.run(10, 2, anti=False))

    def test_residual_monotone_in_distance(self):
        prev = -1.0
        for d in (60, 200, 500, 1200):
            e = engine(cfg=dict(FAST, distance_m=d))
            m = mean_of(e.run(14, 1), "residual")
            self.assertGreaterEqual(m, prev - 1e-9, d)
            prev = m
        self.assertGreater(prev, 0.2)

    def test_thermal_modes_monotone_in_ambient(self):
        pr = []
        for amb in (5.0, 30.0, 50.0):
            e = engine(sets={"ext.ambient_c": amb}, cfg=dict(duration_s=400.0, dt_s=40.0))
            pr.append(prob_of(e.run(30, 1), "thermal_derate"))
        self.assertEqual(pr, sorted(pr))
        self.assertGreater(pr[-1], pr[0])

    def test_weak_psu_trips_pi5_limit(self):
        weak = engine("pi5_3a_weak_psu", cfg=FAST).run(30, 1)
        strong = engine("nominal_pi5_5a_150m", cfg=FAST).run(30, 1)
        self.assertGreater(prob_of(weak, "usb_trip"), 0.9)
        self.assertLess(prob_of(strong, "usb_trip"), 0.1)
        self.assertGreater(mean_of(strong, "availability"), mean_of(weak, "availability"))

    def test_availability_falls_with_shock_rate_and_shocks_counted(self):
        sh = []
        for rate in (0.1, 400.0):
            e = engine(sets={"proc.shock_rate_per_h": rate}, cfg=dict(duration_s=400.0, dt_s=20.0))
            sh.append(mean_of(e.run(16, 1), "shocks"))
        self.assertLess(sh[0], 0.5)
        self.assertGreater(sh[1], 5.0)

    def test_agc_saturation_only_at_close_range(self):
        near = engine("close_range_agc", cfg=FAST).run(24, 1)
        far = engine(cfg=FAST).run(24, 1)
        self.assertGreater(prob_of(near, "agc_saturation"), 0.3)
        self.assertEqual(prob_of(far, "agc_saturation"), 0.0)

    def test_overshoot_triggers_injection_overload(self):
        base = prob_of(engine(cfg=FAST).run(24, 1), "injection_overload")
        over = prob_of(engine(sets={"vid.bitrate_overshoot": 4.0}, cfg=FAST).run(24, 1), "injection_overload")
        self.assertGreater(over, base)
        self.assertGreater(over, 0.5)

    def test_scenarios_have_required_keys(self):
        sc = se.list_scenarios()
        self.assertGreaterEqual(len(sc), 8)
        for n, d in sc.items():
            self.assertIn(d["kind"], ("link", "button"))
            self.assertTrue(d["description"], n)
            engine(n)

    def test_events_schema(self):
        e = engine("pi5_3a_weak_psu", cfg=dict(duration_s=200.0, dt_s=10.0))
        th, rp = e.draw(0, 1, False)
        ev = []
        dm.run_session(th, e.cfg, rp, ev)
        kinds = {x["kind"] for x in ev}
        self.assertIn("bringup", kinds)
        self.assertIn("usb_trip", kinds)
        t_prev = -1.0
        for x in sorted(ev, key=lambda z: z["t_s"]):
            self.assertGreaterEqual(x["t_s"], t_prev)
            t_prev = x["t_s"]
            if x["kind"] == "throttled":
                self.assertEqual(power_model.decode_throttled(int(x["value"], 16)), x["bits"])

    def test_cli_runs(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(se.main(["run", "nominal_pi5_5a_150m", "--n", "6", "--duration", "100", "--dt", "20"]), 0)
            self.assertEqual(se.main(["catalog-check"]), 0)
            self.assertEqual(se.main(["bounce", "button_bouncy_switch", "--kind", "long"]), 0)
        self.assertIn("failure_mode,probability", buf.getvalue())
        self.assertEqual(se.main(["run", "nope"]), 2)


class TestNonlinearHardware(unittest.TestCase):
    def test_pa_monotone_gain_decreasing_saturating(self):
        for p1, p in ((27.5, 2.0), (24.0, 1.5), (30.0, 4.0)):
            prev_out, prev_gain = -1e9, 1e9
            for i in range(-200, 400):
                pin = i / 10.0
                out = dm.pa_output_dbm(pin, p1, p)
                gain = out - pin
                self.assertGreater(out, prev_out, (p1, p, pin))
                self.assertLessEqual(gain, prev_gain + 1e-9)
                self.assertLessEqual(gain, 1e-9)
                prev_out, prev_gain = out, gain
            self.assertLess(dm.pa_output_dbm(60.0, p1, p), dm.mw_to_dbm(dm.pa_psat_mw(p1, p)) + 1e-6)
            self.assertAlmostEqual(dm.pa_output_dbm(p1 + 1.0, p1, p), p1, places=9)  # 1 dB compression point by definition
            self.assertAlmostEqual(dm.pa_output_dbm(-20.0, p1, p), -20.0, places=2)  # linear at low drive

    def test_evm_ceiling_caps_snr(self):
        for evm in (-40.0, -33.0, -25.0, -15.0):
            prev = -1e9
            for snr in range(0, 90, 3):
                eff = dm.combine_snr_evm_db(float(snr), evm)
                self.assertLessEqual(eff, -evm + 1e-9)
                self.assertLessEqual(eff, snr + 1e-9)
                self.assertGreaterEqual(eff, prev)
                prev = eff
            self.assertAlmostEqual(dm.combine_snr_evm_db(200.0, evm), -evm, places=3)
        self.assertLess(dm.evm_db(3.0, -33.0, 1.5), dm.evm_db(0.0, -33.0, 1.5) * -1 * -1 + 100)  # sanity of call
        a, b = dm.evm_db(0.1, -33.0, 1.5), dm.evm_db(3.0, -33.0, 1.5)
        self.assertGreater(b, a)  # more compression, worse EVM
        self.assertGreater(dm.evm_db(0.0, -30.0, 1.5, 3.0), dm.evm_db(0.0, -30.0, 1.5, 0.0))  # hotter, worse floor

    def test_thermal_rc(self):
        th = dm.Thermal(25.0, 10.0, 100.0, 85.0, 0.2, 110.0, 15.0)
        prev, shut_seen = 25.0, False
        for _ in range(400):
            tj, derate, shut = th.step(1.0, 12.0, 25.0)  # steady state 145 C
            self.assertGreaterEqual(tj, prev)
            prev = tj
            shut_seen = shut_seen or shut
        self.assertTrue(shut_seen)
        t_pred = dm.thermal_time_to(25.0, 25.0, 10.0, 12.0, 100.0, 110.0)
        th2 = dm.Thermal(25.0, 10.0, 100.0, 85.0, 0.2, 110.0, 15.0)
        t = 0.0
        while not th2.step(0.5, 12.0, 25.0)[2]:
            t += 0.5
        self.assertAlmostEqual(t, t_pred, delta=1.0)
        self.assertEqual(dm.thermal_time_to(25.0, 25.0, 5.0, 4.0, 100.0, 110.0), math.inf)
        hot = dm.thermal_time_to(25.0, 40.0, 10.0, 12.0, 100.0, 110.0)
        self.assertLess(hot, t_pred)  # higher ambient, earlier shutdown
        # derating is zero below the start and grows above; hysteresis keeps TX off until Tj falls by hyst
        th3 = dm.Thermal(111.0, 10.0, 100.0, 85.0, 0.2, 110.0, 15.0)
        self.assertTrue(th3.step(0.001, 12.0, 25.0)[2])
        tj, der, shut = th3.step(30.0, 0.0, 25.0)
        self.assertTrue(shut or tj <= 95.0)
        self.assertGreater(der if tj > 85 else 1.0, 0.0)

    def test_sag_agc_desense(self):
        prev = -1.0
        for v in (5.2, 4.9, 4.7, 4.5, 4.2):
            s = dm.tx_sag_db(v, 4.75, 4.0, 12.0)
            self.assertGreaterEqual(s, prev)
            prev = s
        self.assertEqual(dm.tx_sag_db(5.0, 4.75, 4.0, 12.0), 0.0)
        prev = -1.0
        for rx in range(-90, 10, 5):
            p = dm.agc_penalty_db(float(rx), -28.0, 1.0, 40.0)
            self.assertGreaterEqual(p, prev)
            self.assertLessEqual(p, 40.0)
            prev = p
        self.assertEqual(dm.noise_rise_db(-94.0, []), 0.0)
        self.assertAlmostEqual(dm.noise_rise_db(-94.0, [-94.0]), 10 * math.log10(2), places=9)
        prev = -1.0
        for i in range(-120, -60, 5):
            r = dm.noise_rise_db(-94.0, [float(i)])
            self.assertGreaterEqual(r, prev)
            prev = r

    def test_antenna_loss_distribution(self):
        eng = engine()
        th = eng.space.median_theta()
        a = dm.AntennaLoss(th, Rng(1))
        xs = [a.draw() for _ in range(3000)]
        self.assertTrue(all(x >= 0.0 for x in xs))
        self.assertGreater(max(xs), 8.0)  # nulls/body shadow produce a tail
        th2 = Theta(dict(th.vals))
        th2.vals["hw.ant_pol_max_deg"] = 10.0
        th2.vals["hw.ant_null_prob"] = 0.0
        th2.vals["hw.ant_body_prob"] = 0.0
        b = dm.AntennaLoss(th2, Rng(1))
        self.assertLess(max(b.draw() for _ in range(500)), 0.2)
        self.assertGreater(dm.pol_loss_db(60.0, 40.0), dm.pol_loss_db(20.0, 40.0))
        self.assertEqual(dm.pol_loss_db(89.99, 25.0), 25.0)

    def test_noise_floor_process(self):
        th = engine().space.median_theta()
        th.vals["proc.shock_rate_per_h"] = 0.0
        nf = dm.NoiseFloor(th, Rng(2))
        for _ in range(500):
            nf.step(5.0)
        self.assertEqual(nf.shock_n, 0)
        th.vals["proc.shock_rate_per_h"] = 3600.0
        nf = dm.NoiseFloor(th, Rng(2))
        for _ in range(500):
            nf.step(5.0)
        self.assertGreater(nf.shock_n, 300)

    def test_burst_chain_rates(self):
        c0 = dm.BurstChain(0.0, 10.0, Rng(1))
        self.assertFalse(any(c0.step(5.0) for _ in range(500)))
        c1 = dm.BurstChain(360.0, 20.0, Rng(1))
        on = sum(1 for _ in range(3000) if c1.step(5.0))
        # stationary on-fraction = rate*mean/(1+rate*mean) = 2/3 for rate 0.1/s, mean 20 s (slice-level approximation)
        self.assertGreater(on / 3000.0, 0.4)

    def test_consistent_with_rf_model_when_degradations_neutral(self):
        eng = engine()
        th = eng.space.median_theta()
        th = Theta(dict(th.vals))
        th.vals.update({"hw.pa_p1db_out_dbm": 90.0, "hw.evm_floor_db": -120.0, "hw.rx_agc_knee_dbm": 500.0, "hw.elrs900_present": 0,
                        "hw.multi24_present": 0, "hw.usb3_present_prob": 0.0, "inj.ebusy_prob": 0.0, "inj.rate_cap_pps": 1e12,
                        "inj.queue_pkts": 1e6, "vid.bitrate_overshoot": 1.0, "rf.floor_per": 0.0, "rf.fading_model": "none",
                        "rf.loss_model": "iid", "proc.age_hours": 0.0})
        plan = dm.prepare(th, eng.cfg, Rng(1))
        st = {"plin": th.get("rf.tx_power_dbm"), "p1db": plan["p1db0"], "evm_shift": 0.0, "ant": 0.0, "shadow": 0.0, "noise_extra": 0.0, "burst": False}
        mcs = int(th.get("rf.mcs_index"))
        for d in (100.0, 600.0, 1500.0, 2500.0, 3500.0):
            want = rf_model.frame_per(th, mcs, rf_model.snr_db(th, d))
            got = dm.link_eval(plan, d, st)["per"]
            self.assertAlmostEqual(got, want, delta=max(0.02 * want, 1e-6), msg=d)
        # and the range search agrees with rf_model.max_range (iid residual, no fading)
        ours, _ = dm.range_at_target(plan, st, th.get("rf.target_residual"))
        ref, _ = rf_model.max_range(th, mcs, int(th.get("rf.fec_k")), int(th.get("rf.fec_n")))
        self.assertAlmostEqual(ours / ref, 1.0, delta=0.03)


class TestUsbPower(unittest.TestCase):
    def test_drop_hazard_falls_with_margin(self):
        base = dm.usb_drop_rate_per_s(0.0, 0.0, 0.05, 0.08, 0.15)
        self.assertGreater(base, 0.05 / 3600.0 * 0.99)
        prev = 2.0
        for m in (-0.3, -0.1, 0.0, 0.1, 0.3, 1.0):
            r = dm.usb_drop_rate_per_s(m, m, 0.05, 0.08, 0.15)
            self.assertLessEqual(r, prev)
            self.assertLessEqual(r, 1.0)
            prev = r
        self.assertLess(dm.usb_drop_rate_per_s(1.0, 1.0, 0.05, 0.08, 0.15), 1e-9)

    def test_pi5_limiter_state_machine(self):
        th = engine().space.median_theta()
        lim600 = power_model.usb_budget_a(th, "pi5", 3.0, False)
        lim16 = power_model.usb_budget_a(th, "pi5", 5.0, False)
        self.assertEqual((lim600, lim16), (0.6, 1.6))
        s = dm.Pi5UsbLimiter(lim16, 0.05, 3)
        self.assertIsNone(s.step(0.0, 1.0, 2.0))
        self.assertEqual(s.state, "OK")
        s = dm.Pi5UsbLimiter(lim600, 0.05, 3)
        self.assertIsNone(s.step(0.0, 0.5, 2.0))
        self.assertEqual(s.step(1.0, 1.0, 2.0), "usb_trip")
        self.assertEqual(s.state, "TRIPPED")
        self.assertIsNone(s.step(2.0, 1.0, 2.0))  # still off
        self.assertEqual(s.step(3.5, 1.0, 2.0), "usb_return")
        self.assertEqual(s.step(4.0, 1.0, 2.0), "usb_trip")
        self.assertEqual(s.step(6.5, 1.0, 2.0), "usb_return")
        self.assertEqual(s.step(7.0, 1.0, 2.0), "usb_latched")  # third consecutive trip
        self.assertIsNone(s.step(100.0, 0.1, 2.0))
        self.assertEqual(s.state, "LATCHED")
        s2 = dm.Pi5UsbLimiter(lim600, 0.05, 3)
        s2.step(0.0, 1.0, 1.0)
        s2.step(2.0, 0.1, 1.0)
        s2.step(3.0, 0.1, 1.0)
        self.assertEqual(s2.consec, 0)  # a quiet period resets the counter

    def test_throttled_word_in_events(self):
        e = engine("nominal_pi5_5a_150m", sets={"power.cable_resistance_ohm": 0.4}, cfg=dict(duration_s=100.0, dt_s=10.0))
        th, rp = e.draw(0, 1, False)
        ev = []
        r = dm.run_session(th, e.cfg, rp, ev)
        self.assertTrue(r["flags"]["undervoltage"])
        self.assertTrue(r["throttled"] & power_model.UV_EVER)
        self.assertTrue(any(x["kind"] == "undervoltage" for x in ev))


class TestBringupInjectionTiming(unittest.TestCase):
    def theta(self, **kw):
        th = Theta(dict(engine().space.median_theta().vals))
        th.vals.update(kw)
        return th

    def test_bringup_mc_matches_closed_form(self):
        th = self.theta(**{"bringup.usb_probe_fail_p": 0.4, "bringup.monitor_fail_p": 0.3, "bringup.max_attempts": 2})
        want = dm.bringup_success_prob(th)
        r = Rng(11)
        got = sum(1 for _ in range(4000) if dm.bringup(th, r)["ok"]) / 4000.0
        self.assertAlmostEqual(got, want, delta=0.03)
        lo = self.theta(**{"bringup.usb_probe_fail_p": 0.1})
        hi = self.theta(**{"bringup.usb_probe_fail_p": 0.5})
        self.assertGreater(dm.bringup_success_prob(lo), dm.bringup_success_prob(hi))
        more = self.theta(**{"bringup.usb_probe_fail_p": 0.5, "bringup.max_attempts": 5})
        self.assertGreater(dm.bringup_success_prob(more), dm.bringup_success_prob(hi))

    def test_bringup_timing_and_failure_stage(self):
        ok = dm.bringup(self.theta(**{"bringup.usb_probe_fail_p": 0.0, "bringup.fw_fail_p": 0.0, "bringup.monitor_fail_p": 0.0,
                                      "bringup.inj_start_fail_p": 0.0}), Rng(1))
        self.assertTrue(ok["ok"] and ok["failed_stage"] is None and ok["t_s"] > 0)
        bad = dm.bringup(self.theta(**{"bringup.monitor_fail_p": 1.0}), Rng(1))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["failed_stage"], "monitor_mode")
        self.assertEqual(bad["attempts"]["monitor_mode"], 3)
        slow = dm.bringup(self.theta(**{"bringup.monitor_fail_p": 1.0, "bringup.backoff_base_s": 10.0}), Rng(1))
        self.assertGreater(slow["t_s"], bad["t_s"])

    def test_mm1k_blocking(self):
        self.assertEqual(dm.injection_block_prob(0.0, 10), 0.0)
        self.assertAlmostEqual(dm.injection_block_prob(1.0, 9), 0.1, places=9)
        prev = -1.0
        for rho in (0.1, 0.5, 0.9, 1.0, 1.2, 3.0, 50.0):
            b = dm.injection_block_prob(rho, 20)
            self.assertTrue(0.0 <= b <= 1.0)
            self.assertGreater(b, prev)
            prev = b
        self.assertGreater(dm.injection_block_prob(0.9, 5), dm.injection_block_prob(0.9, 50))  # longer queue, less blocking
        self.assertLess(dm.injection_block_prob(1e6, 20), 1.0)

    def test_iframe_overflow_and_freeze(self):
        self.assertEqual(dm.iframe_overflow_frac(50, 100, 1.5, 33.0), 0.0)
        self.assertGreater(dm.iframe_overflow_frac(300, 100, 1.5, 33.0), 0.4)
        prev = -1.0
        for res in (0.0, 1e-4, 1e-3, 1e-2, 1e-1, 1.0):
            f = dm.freeze_fraction(res, res, 3, 70, 30.0)
            self.assertTrue(0.0 <= f <= 1.0)
            self.assertGreaterEqual(f, prev)
            prev = f
        self.assertEqual(dm.freeze_fraction(0.0, 0.0, 3, 70, 30.0), 0.0)
        self.assertGreater(dm.freeze_fraction(1e-3, 0.5, 3, 70, 30.0), dm.freeze_fraction(1e-3, 1e-3, 3, 70, 30.0))  # lost IDR hurts most

    def test_timing_processes(self):
        th = self.theta()
        a, b = dm.clock_events(th, Rng(3), 36000.0), dm.clock_events(th, Rng(3), 36000.0)
        self.assertEqual(a, b)
        self.assertEqual(a[0]["kind"], "clock_drift")
        self.assertTrue(any(e["kind"] == "ntp_step" for e in dm.clock_events(self.theta(**{"timing.ntp_step_rate_per_h": 20.0}), Rng(3), 36000.0)))
        self.assertFalse(any(e["kind"] == "ntp_step" for e in dm.clock_events(self.theta(**{"timing.ntp_step_rate_per_h": 0.0}), Rng(3), 36000.0)))
        j = [dm.sched_jitter_ms(th, Rng(i)) for i in range(200)]
        self.assertTrue(all(x > 0 for x in j))
        spiky = self.theta(**{"timing.sched_spike_prob": 0.5})
        self.assertGreater(sum(dm.sched_jitter_ms(spiky, Rng(i)) for i in range(200)), sum(j))

    def test_latency_creep_grows_with_clock_mismatch(self):
        lat = []
        for ppm in (0.0, 80.0):
            e = engine(sets={"timing.clock_ppm": ppm, "timing.stall_rate_per_h": 0.1}, cfg=dict(duration_s=600.0, dt_s=20.0))
            lat.append(mean_of(e.run(12, 1), "g2g_mean_ms"))
        self.assertGreater(lat[1], lat[0] + 3.0)


class TestGpioBounce(unittest.TestCase):
    @staticmethod
    def th(**kw):
        t = Theta(dict(engine("button_nominal").space.median_theta().vals))
        t.vals.update(kw)
        return t

    CLEAN = {"gpio.glitch_rate_per_s": 0.0, "gpio.chatter_prob": 0.0, "gpio.rp1_edge_loss_prob": 0.0, "gpio.rp1_filter_us": 0.0,
             "gpio.bounce_total_ms": 0.0001, "gpio.bounce_count_mean": 1.0}

    def test_constants_match_button_sh(self):
        with open(os.path.join(REPO, "gs", "button.sh"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("sleep 0.05", src)
        self.assertIn("-lt 200", src)
        self.assertIn("/proc/uptime", src)
        t = self.th()
        self.assertEqual(t.get("gpio.settle_s"), 0.05)
        self.assertEqual(t.get("gpio.long_threshold_cs"), 200)
        self.assertEqual(t.get("gpio.debounce_ms"), 0.0)

    def test_trace_structure(self):
        for seed in range(30):
            tr, end = gb.gen_trace(self.th(), Rng(seed), 0.3)
            ts = [e[0] for e in tr]
            self.assertEqual(ts, sorted(ts))
            lv = [e[1] for e in tr]
            for a, b in zip(lv, lv[1:]):
                self.assertNotEqual(a, b)  # level changes only
            self.assertEqual(tr[0][1] if tr[0][2] != "glitch" else 1, 1)
            self.assertEqual(lv[-1], 0)
            self.assertEqual(sum(1 for e in tr if e[2] == "press"), 1)
            self.assertGreater(end, 1.3)

    def test_clean_press_classification_and_threshold_bias(self):
        t = self.th(**self.CLEAN)
        for hold, want in ((0.12, ["single"]), (0.5, ["single"]), (1.9, ["single"]), (2.6, ["long"]), (4.0, ["long"])):
            tr, end = gb.gen_trace(t, Rng(1), hold)
            self.assertEqual([a for _t, a in gb.replay_button_sh(tr, t, Rng(2), end + 5)], want, hold)
        # the measurement starts after the 50 ms settle: a physical 2.03 s hold is measured below 2.00 s -> "single"
        tr, end = gb.gen_trace(t, Rng(1), 2.03)
        self.assertEqual([a for _t, a in gb.replay_button_sh(tr, t, Rng(2), end + 5)], ["single"])
        # a tap shorter than the settle delay is not seen at all (gpioget reads 0 -> continue)
        tr, end = gb.gen_trace(t, Rng(1), 0.03)
        self.assertEqual(gb.replay_button_sh(tr, t, Rng(2), end + 5), [])

    def test_false_events_fall_with_debounce(self):
        def rate(**kw):
            t = self.th(**dict({"gpio.bounce_total_ms": 80.0, "gpio.bounce_count_mean": 8.0, "gpio.glitch_rate_per_s": 0.3,
                                "gpio.chatter_prob": 0.0, "gpio.rp1_filter_us": 0.0}, **kw))
            bad, n = 0, 120
            for i in range(n):
                tr, end = gb.gen_trace(t, Rng(i), 0.15)
                acts = [a for _t, a in gb.replay_button_sh(tr, t, Rng(1000 + i), end + 5)]
                bad += acts != ["single"]
            return bad / n
        r0, r1, r2 = rate(**{"gpio.debounce_ms": 0.0}), rate(**{"gpio.debounce_ms": 8.0}), rate(**{"gpio.debounce_ms": 40.0})
        self.assertGreater(r0, 0.05)
        self.assertGreaterEqual(r0, r1)
        self.assertGreaterEqual(r1, r2)
        self.assertLess(r2, r0 * 0.6)

    def test_default_settle_beats_no_settle_on_bouncy_switch(self):
        def rate(settle):
            t = self.th(**{"gpio.bounce_total_ms": 25.0, "gpio.bounce_count_mean": 6.0, "gpio.glitch_rate_per_s": 0.3,
                           "gpio.chatter_prob": 0.0, "gpio.settle_s": settle})
            bad = 0
            for i in range(120):
                tr, end = gb.gen_trace(t, Rng(i), 0.2)
                bad += [a for _t, a in gb.replay_button_sh(tr, t, Rng(500 + i), end + 5)] != ["single"]
            return bad / 120.0
        self.assertLess(rate(self.th().get("gpio.settle_s")), rate(0.0))

    def test_chatter_breaks_long_press(self):
        t = self.th(**dict(self.CLEAN, **{"gpio.chatter_prob": 1.0}))
        bad = 0
        for i in range(40):
            tr, end = gb.gen_trace(t, Rng(i), 3.0)
            bad += [a for _t, a in gb.replay_button_sh(tr, t, Rng(i), end + 5)] != ["long"]
        self.assertGreater(bad, 20)

    def test_trace_json_schema_and_determinism(self):
        t = self.th()
        j = gb.trace_json(t, "x", 5, "long")
        self.assertEqual(j, gb.trace_json(t, "x", 5, "long"))
        self.assertNotEqual(j["events"], gb.trace_json(t, "x", 6, "long")["events"])
        self.assertEqual(j["schema"], "sbc-gs-gpio-bounce/1")
        for key in ("scenario", "seed", "line", "intent", "events", "algorithm", "expected_actions"):
            self.assertIn(key, j)
        self.assertEqual(j["line"], {"pull": "down", "active_level": 1, "idle_level": 0})
        ts = [e["t_us"] for e in j["events"]]
        self.assertEqual(ts, sorted(ts))
        self.assertTrue(all(isinstance(x, int) for x in ts))
        self.assertTrue(all(e["level"] in (0, 1) and e["cause"] in ("press", "press_bounce", "release", "release_bounce", "glitch", "chatter")
                            for e in j["events"]))
        json.dumps(j)

    def test_stats_probabilities(self):
        p = gb.button_stats(self.th(), Rng(3), 20)
        for k, v in p.items():
            self.assertTrue(0.0 <= v <= 1.0, k)
        self.assertAlmostEqual(p["single_ok"] + p["single_extra"] + p["single_missed"] + p["single_as_long"], 1.0)
        bouncy = gb.button_stats(self.th(**{"gpio.bounce_total_ms": 40.0, "gpio.glitch_rate_per_s": 1.0}), Rng(3), 20)
        self.assertGreaterEqual(bouncy["false_event"], p["false_event"] - 0.2)

    def test_rp1_edge_loss_hurts(self):
        ok = gb.button_stats(self.th(**dict(self.CLEAN, **{"gpio.single_hold_s": 0.15})), Rng(3), 20)
        lossy = gb.button_stats(self.th(**dict(self.CLEAN, **{"gpio.single_hold_s": 0.15, "gpio.rp1_edge_loss_prob": 0.5})), Rng(3), 20)
        self.assertGreater(lossy["false_event"], ok["false_event"])


class TestCatalogAndDocs(unittest.TestCase):
    def test_catalog_valid_and_big_enough(self):
        cat = se.load_catalog()
        self.assertEqual(se.validate_catalog(cat), [])
        self.assertGreaterEqual(len(cat["scenarios"]), 30)
        need = ("thermal", "desense", "brown-out", "re-enumeration", "clash", "heartbeat", "32862", "UART", "overshoot", "IDR", "16K", "DKMS",
                "SD-card", "throttling", "Wayland", "NTP", "device node", "axis", "bounce", "udev", "key mismatch", "alink")
        text = " ".join(s["title"] for s in cat["scenarios"]).lower()
        for w in need:
            self.assertIn(w.lower(), text, w)

    def test_catalog_probabilities_labelled_synth(self):
        for s in se.load_catalog()["scenarios"]:
            self.assertEqual(s["prior_prob"]["provenance"], "SYNTH")
        bad = json.loads(json.dumps(se.load_catalog()))
        bad["scenarios"][0]["prior_prob"]["provenance"] = "INF"
        self.assertTrue(any("SYNTH" in e for e in se.validate_catalog(bad)))
        bad = json.loads(json.dumps(se.load_catalog()))
        del bad["scenarios"][1]["detection_test"]
        self.assertTrue(any("missing detection_test" in e for e in se.validate_catalog(bad)))
        bad = json.loads(json.dumps(se.load_catalog()))
        bad["scenarios"][2]["engine_mode"] = "nonexistent_mode"
        self.assertTrue(any("engine_mode" in e for e in se.validate_catalog(bad)))
        bad = json.loads(json.dumps(se.load_catalog()))
        bad["scenarios"][3]["prior_prob"]["value"] = 1.5
        self.assertTrue(any("(0,1)" in e for e in se.validate_catalog(bad)))

    def test_false_confidence_ids_exist_in_blockers_doc(self):
        path = os.path.join(REPO, "docs", "SIM-BLOCKERS.md")
        if not os.path.exists(path):
            self.skipTest("docs/SIM-BLOCKERS.md absent")
        txt = open(path, encoding="utf-8").read()
        for s in se.load_catalog()["scenarios"]:
            for fc in s["false_confidence"]:
                self.assertRegex(txt, r"\| %s \|" % fc)

    def test_every_engine_mode_documented(self):
        used = {s["engine_mode"] for s in se.load_catalog()["scenarios"] if s["engine_mode"]}
        self.assertTrue(used <= set(dm.FAIL_MODES) | set(se.BUTTON_MODES))
        self.assertGreaterEqual(len(used), 8)

    def test_doc_mentions_every_parameter_and_scenario(self):
        path = os.path.join(REPO, "docs", "SIM-SCENARIOS.md")
        self.assertTrue(os.path.exists(path))
        txt = open(path, encoding="utf-8").read()
        for k in priors.load_degrade().leaves:
            self.assertIn("`%s`" % k, txt, k)
        for s in se.load_catalog()["scenarios"]:
            self.assertIn("| %s |" % s["id"], txt, s["id"])
        for n in se.list_scenarios():
            self.assertIn(n, txt, n)
        for w in ("SYNTH", "Чесність", "Morris"):
            self.assertIn(w, txt)


class TestAirEnergyBalance(unittest.TestCase):
    """D1/D1b: AIR heat = P_dc - P_rf, grows with the radiated power; energy is conserved on every prior draw."""

    def test_heat_identity_monotone_and_conserved(self):
        r = Rng(11)
        for _ in range(500):
            v, i, dfr, ref, prf = 4.5 + r.u(), 0.4 + 1.5 * r.u(), 0.7 + 0.25 * r.u(), 0.05 + 1.2 * r.u(), 1.5 * r.u()
            pdc, heat, eta = dm.air_tx_power_w(v, i, prf, ref, dfr)
            self.assertAlmostEqual(pdc - prf, heat, places=12)
            self.assertTrue(pdc >= prf and heat >= 0.0 and 0.0 < eta <= 1.0)
            self.assertGreater(dm.air_tx_power_w(v, i, prf + 0.1, ref, dfr)[1], heat)
            self.assertGreater(dm.air_tx_power_w(v, i + 0.1, prf, ref, dfr)[1], heat)

    def test_current_at_rated_output_is_reproduced(self):
        # at the rated output the model returns the sampled DC power (when the efficiency prior allows it): P_dc = V*I
        pdc, heat, _eta = dm.air_tx_power_w(5.0, 1.0, 0.5, 0.5, 0.8)  # eta 0.2 -> PA DC 2.5 W <= 5 W
        self.assertAlmostEqual(pdc, 5.0, places=12)
        self.assertAlmostEqual(heat, 4.5, places=12)
        pdc, _h, eta = dm.air_tx_power_w(5.0, 0.2, 0.5, 0.5, 0.8)  # PA would need 2.5 W > 1 W: the measured current wins
        self.assertAlmostEqual(pdc, 1.0, places=12)
        self.assertAlmostEqual(eta, 0.5, places=12)

    def test_no_violation_on_prior_draws(self):
        e = engine()
        r = Rng(5)
        k = len(e.space.dims)
        for _ in range(400):
            th = e.space.theta([r.u() for _ in range(k)])
            g = th.get
            prf = dm.dbm_to_mw(dm.pa_output_dbm(g("rf.tx_power_dbm"), g("hw.pa_p1db_out_dbm"), g("hw.pa_rapp_p"))) / 1000.0
            pdc, heat, _eta = dm.air_tx_power_w(g("hw.air_bec_v"), g("power.devices.rtl8812_tx_a"), prf, dm.dbm_to_mw(g("hw.pa_p1db_out_dbm")) / 1000.0,
                                                g("hw.air_diss_frac"))
            self.assertLessEqual(prf + heat, pdc * (1 + 1e-12))

    def test_junction_temperature_grows_with_rf_power_in_a_session(self):
        tj = []
        for tx in (20.0, 24.0, 27.0):
            e = engine("hot_day_closed_case", sets={"rf.tx_power_dbm": tx})
            o = dm.run_session(e.space.median_theta(), e.cfg, Rng(1))
            tj.append(o["tj_end_c"])
            self.assertAlmostEqual(o["air_energy"]["dc_w"] - o["air_energy"]["rf_w"], o["air_energy"]["heat_w"], places=12)
        self.assertTrue(tj[0] + 1.0 < tj[1] and tj[1] + 1.0 < tj[2], tj)

    def test_backing_off_cools_more_when_the_current_is_rf_proportional(self):
        # the TX current is measured at the rated output; flight power is lower. A PA whose DC is mostly proportional to the RF output
        # (low eta: the measured current wins, idle = 0) sheds more DC when backed off than a PA with a large fixed idle (eta 30 %)
        tj = []
        for dfr in (0.70, 0.95):
            e = engine("hot_day_closed_case", sets={"hw.air_diss_frac": dfr})
            tj.append(dm.run_session(e.space.median_theta(), e.cfg, Rng(1))["tj_end_c"])
        self.assertGreater(tj[0], tj[1])


class TestDeadLink(unittest.TestCase):
    """D4: a link that never works has margin None + dead=True, never a stub number inside the physical range."""

    def test_bringup_failure_is_dead_without_margin(self):
        e = engine("nominal_pi5_5a_150m", sets={"bringup.usb_probe_fail_p": 1.0, "bringup.max_attempts": 1})
        o = dm.run_session(e.space.median_theta(), e.cfg, Rng(1))
        self.assertTrue(o["dead"] and o["margin_db"] is None and o["margin_p5_db"] is None)
        self.assertEqual((o["availability"], o["residual"]), (0.0, 1.0))

    def test_working_link_has_finite_margin_and_not_dead(self):
        o = dm.run_session(engine().space.median_theta(), engine().cfg, Rng(1))
        self.assertFalse(o["dead"])
        self.assertTrue(math.isfinite(o["margin_db"]) and math.isfinite(o["margin_p5_db"]))

    def test_summary_excludes_dead_draws_and_reports_their_share(self):
        e = engine("pi5_3a_weak_psu")
        res = e.run(24, 1)
        s = se.summarize(e, res)
        dead = sum(1 for r in res if r["dead"])
        self.assertGreater(dead, 0)
        self.assertEqual(s["outputs"]["margin_db"]["n"], len(res) - dead)
        self.assertAlmostEqual(s["dead_frac"], dead / len(res))
        txt = "\n".join(se.fmt_report(e, 24, 1, False, s))
        self.assertIn("P(dead link", txt)
        self.assertNotIn("-60,-60", txt)

    def test_morris_keeps_the_dead_state_out_of_margin(self):
        e = engine()
        key = "power.tx_peak_factor"
        med = e.space.median_theta().get(key)

        def fake(th, _rng):
            dead = th.get(key) > med
            r = {"margin_db": None if dead else 10.0, "dead": dead}
            r.update({o: 0.5 for o in se.SENS_LINK if o != "margin_db"})
            return r
        e.evaluate = fake
        tab, noise = se.morris(e, 3, 1, list(se.SENS_LINK) + [se.SENS_DEAD])
        self.assertTrue(all(mu == 0.0 for mu, _sg, _k, _p in tab["margin_db"]))
        self.assertEqual(tab[se.SENS_DEAD][0][2], key)
        self.assertEqual(noise["margin_db"], 0.0)

    def test_sensitivity_report_has_dead_row_but_not_in_measure_first(self):
        e = engine("nominal_pi5_5a_150m", cfg={"duration_s": 100.0, "dt_s": 50.0})
        txt = "\n".join(se.fmt_sensitivity(e, 2, 1)[0])
        self.assertIn("## output dead", txt)
        self.assertIn("summed share over 6 outputs", txt)


class TestFadingQuadrature(unittest.TestCase):
    """D9: the fading average of the PER is an integral over the pdf, not 32 quantiles: the tail is right."""

    @staticmethod
    def rician_ref(tab, snr, kdb, n=20000, xmax=4.0):
        k = 10 ** (kdb / 10.0)

        def i0(z):
            t, s, term, j = (z / 2.0) ** 2, 1.0, 1.0, 1
            while term > 1e-17 * s:
                term *= t / (j * j)
                s += term
                j += 1
            return s
        h = xmax / n
        tot = mass = 0.0
        for i in range(n):
            x = (i + 0.5) * h
            p = (k + 1) * math.exp(-k - (k + 1) * x) * i0(2 * math.sqrt(k * (k + 1) * x)) * h
            mass += p
            tot += p * dm.per_lookup(tab, snr + 10 * math.log10(x))
        return tot / mass

    def test_weights_are_a_probability_with_unit_mean_power(self):
        for model, k in (("rician", 5.0), ("rician", 15.0), ("rayleigh", 0.0)):
            w = dm.fading_weights(model, k)
            self.assertAlmostEqual(sum(w), 1.0, places=12)
            self.assertAlmostEqual(sum(x * 10 ** ((dm._FADE_JLO + i) * 0.25 / 10.0) for i, x in enumerate(w)), 1.0, delta=3e-3)
        self.assertEqual(dm.fading_weights("none", 0.0)[-dm._FADE_JLO], 1.0)

    def test_tail_matches_independent_integration(self):
        tab = dm.per_table(1, False, 1456)
        ft = dm.fading_per_table(1, False, 1456, "rician", 10.0)
        for snr in (8.0, 15.0, 22.0):
            ref = self.rician_ref(tab, snr, 10.0)
            self.assertAlmostEqual(dm.per_lookup(ft, snr) / ref, 1.0, delta=0.04, msg="snr %s" % snr)
        self.assertGreater(dm.per_lookup(ft, 15.0), 1.5e-3)  # the 32-quantile estimate was 2.4e-7 here

    def test_deep_fade_tail_is_present_for_rayleigh_and_decays_like_1_over_snr(self):
        ft = dm.fading_per_table(1, False, 1456, "rayleigh", 0.0)
        p20, p30 = dm.per_lookup(ft, 20.0), dm.per_lookup(ft, 30.0)
        self.assertAlmostEqual(math.log10(p20 / p30), 1.0, delta=0.2)  # Rayleigh diversity order 1: -10 dB per decade

    def test_none_is_plain_table_and_tables_are_cached(self):
        self.assertIs(dm.fading_per_table(1, False, 1456, "none", 0.0), dm.per_table(1, False, 1456))
        self.assertIs(dm.fading_per_table(3, False, 1456, "rician", 7.5), dm.fading_per_table(3, False, 1456, "rician", 7.5))

    def test_engine_uses_the_faded_table(self):
        e = engine()
        plan = dm.prepare(e.space.median_theta(), e.cfg, Rng(1))
        self.assertIn("ftab", plan)
        self.assertNotIn("gains", plan)


class TestGolden(unittest.TestCase):
    """Four reference scenarios and two sensitivity tables, full text compared with golden/*.txt."""

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

    def scenario(self, name, n=60):
        e = engine(name)
        return "\n".join(se.fmt_report(e, n, 1, False, se.summarize(e, e.run(n, 1)))) + "\n"

    def test_scenario_nominal(self):
        self.check("scenario_nominal_pi5_5a_150m.txt", self.scenario("nominal_pi5_5a_150m"))

    def test_scenario_weak_psu(self):
        self.check("scenario_pi5_3a_weak_psu.txt", self.scenario("pi5_3a_weak_psu"))

    def test_scenario_hot_day(self):
        self.check("scenario_hot_day_closed_case.txt", self.scenario("hot_day_closed_case"))

    def test_scenario_button_bouncy(self):
        self.check("scenario_button_bouncy_switch.txt", self.scenario("button_bouncy_switch"))

    def test_sensitivity_link(self):
        e = engine("nominal_pi5_5a_150m", cfg={"duration_s": 300.0, "dt_s": 30.0})
        self.check("sensitivity_nominal_pi5_5a_150m.txt", "\n".join(se.fmt_sensitivity(e, 3, 1)[0]) + "\n")

    def test_sensitivity_button(self):
        e = engine("button_nominal")
        self.check("sensitivity_button_nominal.txt", "\n".join(se.fmt_sensitivity(e, 6, 1)[0]) + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=1)
