#!/usr/bin/env python3
"""Валідація моделей SBC-GS без заліза (unittest, stdlib, режим --check < 15 с; VALIDATE_LONG=1 збільшує вибірки).

Що перевіряється (деталі й результати: docs/SIM-VALIDATION.md):
  * фізичні інваріанти й одиниці на всьому гіперкубі пріорів (>= 2000 seeded точок), бездефектність NaN/inf, межі;
  * крос-модельна узгодженість (power <-> degrade, rf <-> degrade при нульовій деградації, latency <-> рушій, замкнені формули <-> MC);
  * статистика (антитетика знижує дисперсію, детермінізм, бутстреп);
  * back-test проти зовнішніх довідкових даних (SRC, див. backtest.py);
  * «DEFECT»-тести (TestKnownDefects) фіксують ЗНАЙДЕНІ невідповідності моделей (Dn у docs/SIM-VALIDATION.md): вони проходять, поки дефект
    є; коли модель виправлять, тест впаде з повідомленням «виправлено — онови доку й тест». Виправлені дефекти (D1, D1b, D4, D9)
    перетворено на постійні «FIXED»-тести (TestFixedDefects): вони падають, якщо дефект повернеться.
Моделі цим файлом не змінюються. Тести проходять і під nobody на записуваній копії (нічого не пишуть у каталог моделей).
"""
import math
import os
import random
import sys
import unittest

import altforms
import backtest
import vlib
from vlib import common, dm, gpio_bounce, latency_budget, power_model, priors, rf_model, se
from vlib import Rng, Theta

LONG = os.environ.get("VALIDATE_LONG") == "1"
ROOT = os.path.abspath(os.path.join(vlib.MODELS, "..", "..", ".."))
DOC = os.path.join(ROOT, "docs", "SIM-VALIDATION.md")

_ENG = {}


def eng(name="nominal_pi5_5a_150m"):
    if name not in _ENG:
        _ENG[name] = vlib.engine(name)
    return _ENG[name]


# ================================================================ 1. фізичні інваріанти й одиниці
class TestPhysicsUnits(unittest.TestCase):
    def test_fspl_matches_textbook(self):
        # SRC Wikipedia FSPL: 20log10(d_km)+20log10(f_GHz)+92.45 ; модель 32.44 (+-0.02 дБ різниці констант)
        for d in (1, 10, 150, 1000, 8000, 30000):
            for f in (2437, 5180, 5745):
                ref = 20 * math.log10(d / 1000.0) + 20 * math.log10(f / 1000.0) + 92.45
                self.assertAlmostEqual(rf_model.fspl_db(d, f), ref, delta=0.02, msg="FSPL(%s m,%s MHz)" % (d, f))

    def test_fspl_scaling_6db_per_octave(self):
        for d in (10, 300):
            self.assertAlmostEqual(rf_model.fspl_db(2 * d, 5745) - rf_model.fspl_db(d, 5745), 6.0206, delta=1e-6)
        self.assertAlmostEqual(rf_model.fspl_db(100, 5800) - rf_model.fspl_db(100, 2900), 6.0206, delta=1e-6)

    def test_path_loss_slope_and_monotone(self):
        r = random.Random(1)
        for _ in range(50):
            n = r.uniform(2.0, 3.5)
            P = common.load({"rf.path_loss_exponent": n})
            a, b = r.uniform(2, 200), r.uniform(300, 20000)
            self.assertLess(rf_model.path_loss_db(P, a), rf_model.path_loss_db(P, b))
            self.assertAlmostEqual(rf_model.path_loss_db(P, 10 * a) - rf_model.path_loss_db(P, a), 10 * n, delta=1e-9)
            self.assertLess(rf_model.snr_db(P, b), rf_model.snr_db(P, a))
            self.assertLess(rf_model.rx_dbm(P, b), rf_model.rx_dbm(P, a))

    def test_noise_floor_formula(self):
        # kT = -173.98 dBm/Hz при 290 K (INF, фізична стала); модель -174 (+-0.05)
        for bw in (20, 40):
            P = common.load({"rf.bandwidth_mhz": bw, "rf.noise_figure_db": 6.0})
            self.assertAlmostEqual(rf_model.noise_dbm(P), -173.98 + 10 * math.log10(bw * 1e6) + 6.0, delta=0.05)
        P20, P40 = common.load({"rf.bandwidth_mhz": 20}), common.load({"rf.bandwidth_mhz": 40})
        self.assertAlmostEqual(rf_model.noise_dbm(P40) - rf_model.noise_dbm(P20), 3.0103, delta=1e-6)

    def test_snr_interference_never_increases_snr(self):
        P0 = common.load()
        s0 = rf_model.snr_db(P0, 500)
        for i in (-110, -95, -80, -60):
            P = common.load({"rf.interference_dbm": i})
            self.assertLessEqual(rf_model.snr_db(P, 500), s0 + 1e-12)

    def test_phy_rates_ht20(self):
        # INF (стандарт 802.11n, HT20 LGI 1SS): 6.5 13 19.5 26 39 52 58.5 65 Мбіт/с
        ref = [6.5, 13.0, 19.5, 26.0, 39.0, 52.0, 58.5, 65.0]
        for m in range(8):
            self.assertAlmostEqual(rf_model.phy_rate_mbps(m, 20, False), ref[m], delta=1e-9)
        self.assertAlmostEqual(rf_model.phy_rate_mbps(7, 20, True), 72.2222, delta=1e-3)  # SGI

    def test_per_monotone_bounded_all_mcs(self):
        grid = [x * 0.5 for x in range(-20, 120)]
        for vht, top in ((False, 7), (True, 9)):
            for m in range(top + 1):
                for nb in (100, 1456):
                    prev = 1.0
                    for s in grid:
                        p = rf_model.per_ideal(m, s, nb, vht)
                        self.assertTrue(0.0 <= p <= 1.0)
                        self.assertLessEqual(p, prev + 1e-12, "PER не монотонний: mcs=%d vht=%s snr=%.1f" % (m, vht, s))
                        prev = p
                self.assertEqual(rf_model.per_ideal(m, -30, 1456, vht), 1.0)
                self.assertLess(rf_model.per_ideal(m, 50, 1456, vht), 1e-6)

    def test_per_longer_frame_not_better(self):
        for m in range(8):
            for s in (3, 8, 12, 18, 24):
                a, b = rf_model.per_ideal(m, s, 1456), rf_model.per_ideal(m, s, 200)
                self.assertGreaterEqual(a, b - 1e-15)
                if 1e-6 < b < 0.05:  # у малих PER довший кадр ~ пропорційно гірший (8*nbytes біт)
                    self.assertGreater(a / b, 3.0)

    def test_required_snr_increases_with_mcs(self):
        # SRC таблиця чутливості: -82 -79 -77 -74 -70 -66 -65 -64 (строго зростає)
        P = common.load({"rf.fading_model": "none"})
        s = [rf_model.snr_for_per(common.load({"rf.mcs_index": m, "rf.fading_model": "none"}), m) for m in range(8)]
        for a, b in zip(s, s[1:]):
            self.assertLess(a, b)
        self.assertEqual(P.get("rf.fading_model"), "none")

    def test_rapp_pa_compresses(self):
        r = random.Random(2)
        for _ in range(60):
            p1, p = r.uniform(20, 32), r.uniform(1.5, 4.0)
            self.assertAlmostEqual(dm.pa_output_dbm(p1 + 1.0, p1, p), p1, delta=1e-9)  # рівно 1 дБ стиснення в P1dB
            xs = [dm.pa_output_dbm(x, p1, p) for x in range(-30, 70, 2)]
            for i in range(len(xs) - 1):
                self.assertGreaterEqual(xs[i + 1], xs[i] - 1e-9)           # Pout зростає
                self.assertLessEqual(xs[i + 1] - xs[i], 2.0 + 1e-9)       # підсилення не зростає
            self.assertLess(xs[-1], dm.mw_to_dbm(dm.pa_psat_mw(p1, p)) + 1e-6)  # не вище Psat
            self.assertAlmostEqual(dm.pa_output_dbm(-30, p1, p), -30.0, delta=1e-3)  # лінійно при малому драйві
            self.assertGreater(dm.mw_to_dbm(dm.pa_psat_mw(p1, p)), p1)

    def test_evm_and_snr_ceiling(self):
        r = random.Random(3)
        for _ in range(200):
            floor, k, shift = r.uniform(-40, -26), r.uniform(1, 2.5), r.uniform(0, 3)
            comps = [0.0, 0.5, 1, 2, 4, 8]
            evs = [dm.evm_db(c, floor, k, shift) for c in comps]
            for a, b in zip(evs, evs[1:]):
                self.assertGreaterEqual(b, a - 1e-12)  # більше стиснення => гірший EVM
            self.assertGreaterEqual(dm.evm_db(2, floor, k, shift + 1), dm.evm_db(2, floor, k, shift))
            for snr in (-10, 0, 10, 30, 60, 120):
                eff = dm.combine_snr_evm_db(snr, evs[2])
                self.assertLessEqual(eff, snr + 1e-9)
                self.assertLessEqual(eff, -evs[2] + 1e-9)  # стеля -EVM
            self.assertLess(dm.combine_snr_evm_db(200, evs[0]), -evs[0] + 1e-6)
            self.assertAlmostEqual(dm.combine_snr_evm_db(200, evs[0]), -evs[0], delta=1e-3)  # при великому SNR саме стеля
            self.assertAlmostEqual(dm.combine_snr_evm_db(15.0, evs[2]), -10 * math.log10(10 ** -1.5 + 10 ** (evs[2] / 10)), delta=1e-9)

    def test_thermal_first_order_exact(self):
        for t0, tamb, rth, tau, p in ((25, 25, 7, 150, 5.0), (90, 30, 7, 100, 3.0), (40, 40, 12, 60, 0.0)):
            th = dm.Thermal(t0, rth, tau, 85, 0.175, 110, 16.5)
            tss = tamb + rth * p
            t = 0.0
            for _ in range(40):
                tj, der, shut = th.step(5.0, p, tamb)
                t += 5.0
                self.assertAlmostEqual(tj, tss + (t0 - tss) * math.exp(-t / tau), delta=1e-9)
                self.assertGreaterEqual(der, 0.0)
                self.assertTrue(min(t0, tss) - 1e-9 <= tj <= max(t0, tss) + 1e-9)  # без перерегулювання
        # час до порогу з аналітики збігається з кроковою моделлю
        t_an = dm.thermal_time_to(25, 25, 7, 8.0, 150, 60)
        th = dm.Thermal(25, 7, 150, 85, 0.175, 200, 5)
        tt = 0.0
        while th.tj < 60:
            th.step(0.1, 8.0, 25)
            tt += 0.1
        self.assertAlmostEqual(tt, t_an, delta=0.2)

    def test_thermal_monotone_in_power_and_ambient(self):
        def tj_after(p, amb):
            th = dm.Thermal(amb, 7, 150, 85, 0.175, 200, 5)
            for _ in range(50):
                tj = th.step(5, p, amb)[0]
            return tj
        for p in (0.5, 2.0, 5.0):
            self.assertLess(tj_after(p, 20), tj_after(p + 1, 20))
            self.assertLess(tj_after(p, 20), tj_after(p, 30))

    def test_thermal_hysteresis(self):
        th = dm.Thermal(100, 7, 50, 85, 0.2, 110, 16.5)
        seq = []
        for _ in range(400):
            hot = len(seq) < 100
            seq.append(th.step(5, 20.0 if hot else 0.0, 25)[2])
        self.assertTrue(any(seq))
        self.assertFalse(seq[-1])
        # після вимкнення повертається лише при Tj <= 110-16.5
        th2 = dm.Thermal(111, 7, 50, 85, 0.2, 110, 16.5)
        tj, _d, shut = th2.step(0.001, 20.0, 25)
        self.assertTrue(shut)
        th2.tj = 100.0
        self.assertTrue(th2.step(0.0001, 20.0, 25)[2])

    def test_sag_agc_noise_rise(self):
        for k1, k2 in ((2, 4), (5, 14.5), (8, 25)):
            xs = [dm.tx_sag_db(v / 10.0, 4.75, k1, k2) for v in range(40, 56)]
            self.assertTrue(all(a >= b - 1e-12 for a, b in zip(xs, xs[1:])))  # нижча напруга => більший провал
            self.assertTrue(all(x >= 0 for x in xs))
            self.assertEqual(dm.tx_sag_db(5.0, 4.75, k1, k2), 0.0)
        pen = [dm.agc_penalty_db(x, -28, 1.35, 35) for x in range(-60, 10, 2)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(pen, pen[1:])))
        self.assertTrue(max(pen) <= 35 and min(pen) == 0.0)
        self.assertEqual(dm.noise_rise_db(-95, []), 0.0)
        rises = [dm.noise_rise_db(-95, [i]) for i in range(-120, -60, 5)]
        self.assertTrue(all(b >= a for a, b in zip(rises, rises[1:])) and rises[0] >= 0)
        self.assertAlmostEqual(dm.noise_rise_db(-95, [-95]), 3.0103, delta=1e-4)

    def test_usb_hazard_monotone_bounded(self):
        for vm in (-1, 0, 0.2, 0.6, 1.5):
            for im in (-0.5, 0, 0.3, 1.0):
                h = dm.usb_drop_rate_per_s(vm, im, 0.05, 0.1, 0.19)
                self.assertTrue(0.0 <= h <= 1.0)
                self.assertGreaterEqual(h, dm.usb_drop_rate_per_s(vm + 0.1, im, 0.05, 0.1, 0.19))
                self.assertGreaterEqual(h, dm.usb_drop_rate_per_s(vm, im + 0.1, 0.05, 0.1, 0.19))
        self.assertAlmostEqual(dm.usb_drop_rate_per_s(0.0, 0.0, 3600.0, 0.1, 0.2), 1.0, delta=1e-12)  # base_per_h/3600 при нульових запасах

    def test_mm1k_matches_birth_death(self):
        r = random.Random(4)
        for _ in range(40):
            rho, k = r.uniform(0.05, 3.0), r.randint(1, 40)
            w = [rho ** i for i in range(k + 1)]
            self.assertAlmostEqual(dm.injection_block_prob(rho, k), w[-1] / sum(w), delta=1e-9)
            self.assertTrue(0 <= dm.injection_block_prob(rho, k) <= 1)
            self.assertLess(dm.injection_block_prob(rho, k + 1), dm.injection_block_prob(rho, k) + 1e-15)
            self.assertLessEqual(dm.injection_block_prob(rho, k), dm.injection_block_prob(rho * 1.2, k) + 1e-15)
        self.assertAlmostEqual(dm.injection_block_prob(1.0, 9), 0.1, delta=1e-12)
        self.assertAlmostEqual(dm.injection_block_prob(1e6, 5), 1.0, delta=1e-5)

    def test_mm1k_vs_event_simulation(self):
        import studies
        for rho, k in ((0.8, 3), (1.5, 10)):
            mc = studies.queue_sim(rho, k, 40000, "exp", 7)
            self.assertAlmostEqual(dm.injection_block_prob(rho, k), mc, delta=0.02)

    def test_fec_iid_matches_enumeration(self):
        for n, k in ((4, 2), (6, 4), (8, 5), (7, 7), (5, 1)):
            for p in (0.01, 0.1, 0.3, 0.6):
                tot = 0.0
                for mask in range(1 << n):
                    lost = [(mask >> i) & 1 for i in range(n)]
                    pr = 1.0
                    for x in lost:
                        pr *= p if x else 1 - p
                    if sum(lost) > n - k:  # блок невідновний
                        tot += pr * sum(lost[:k]) / k
                self.assertAlmostEqual(rf_model.residual_iid(p, k, n), tot, delta=1e-12)

    def test_fec_residual_monotone_and_ordered(self):
        for k, n in ((8, 12), (4, 12), (1, 1)):
            prev = -1.0
            for i in range(1, 60):
                p = i / 60.0
                v = rf_model.residual_iid(p, k, n)
                self.assertTrue(0 <= v <= p + 1e-12)  # FEC не гірше за відсутність FEC
                self.assertGreaterEqual(v, prev - 1e-15)
                prev = v
        for p in (0.02, 0.1, 0.3):  # більше паритету => менше втрат
            self.assertLess(rf_model.residual_iid(p, 8, 12), rf_model.residual_iid(p, 8, 10) + 1e-15)

    def test_gilbert_dp_matches_monte_carlo(self):
        k, n, p, burst = 8, 12, 0.2, 4.0
        rnd = random.Random(11)
        p_bg = 1 / burst
        p_gb = p_bg * p / (1 - p)
        tot_lost = 0
        blocks = 40000
        bad = rnd.random() < p
        for _ in range(blocks):
            lost = []
            for _i in range(n):
                lost.append(bad)
                bad = (rnd.random() >= p_bg) if bad else (rnd.random() < p_gb)
            if sum(lost) > n - k:
                tot_lost += sum(lost[:k])
        mc = tot_lost / (blocks * k)
        dp = rf_model.residual_ge(p, burst, k, n)
        self.assertAlmostEqual(dp, mc, delta=0.12 * dp)
        self.assertGreater(dp, rf_model.residual_iid(p, k, n))  # пакетність гірша за iid

    def test_noise_floor_process_statistics(self):
        th = Theta({"proc.nf_ou_sigma_db": 2.0, "proc.nf_ou_tau_s": 60.0, "proc.shock_rate_per_h": 0.0,
                    "proc.shock_mag_db": 6.0, "proc.shock_tau_s": 20.0})
        nf = dm.NoiseFloor(th, Rng(5))
        xs = [nf.step(5.0)[0] for _ in range(30000)]
        m = sum(xs) / len(xs)
        v = sum((x - m) ** 2 for x in xs) / len(xs)
        self.assertAlmostEqual(v, 4.0, delta=0.5)                       # дисперсія OU = sigma^2
        c1 = sum((a - m) * (b - m) for a, b in zip(xs, xs[1:])) / len(xs) / v
        self.assertAlmostEqual(c1, math.exp(-5 / 60), delta=0.03)       # автокореляція exp(-dt/tau)
        self.assertEqual(nf.shock_n, 0)
        th2 = Theta({"proc.nf_ou_sigma_db": 0.5, "proc.nf_ou_tau_s": 60.0, "proc.shock_rate_per_h": 36.0,
                     "proc.shock_mag_db": 6.0, "proc.shock_tau_s": 20.0})
        nf2 = dm.NoiseFloor(th2, Rng(6))
        for _ in range(7200):  # 10 год при dt=5 с => очікувано 360 шоків
            nf2.step(5.0)
        self.assertAlmostEqual(nf2.shock_n, 360, delta=60)

    def test_burst_chain_duty(self):
        rate_h, mean_s, dt = 360.0, 10.0, 0.5
        ch = dm.BurstChain(rate_h, mean_s, Rng(8))
        n = 60000
        on = sum(1 for _ in range(n) if ch.step(dt))
        a, b = -math.expm1(-rate_h / 3600 * dt), -math.expm1(-dt / mean_s)
        self.assertAlmostEqual(on / n, a / (a + b), delta=0.04)

    def test_antenna_loss_nonnegative_capped(self):
        th = Theta({"hw.ant_null_prob": 0.3, "hw.ant_null_mean_db": 10, "hw.ant_pol_max_deg": 80, "hw.ant_pol_cap_db": 22.5,
                    "hw.ant_body_prob": 0.3, "hw.ant_body_mean_db": 12, "hw.ant_body_sigma_db": 4, "hw.ant_persist_s": 10})
        a = dm.AntennaLoss(th, Rng(9))
        vals = [a.step(5.0) for _ in range(3000)]
        self.assertTrue(min(vals) >= 0.0 and all(vlib.finite(v) for v in vals))
        self.assertTrue(dm.pol_loss_db(0, 22.5) == 0.0 and dm.pol_loss_db(89.9, 22.5) <= 22.5)

    def test_bringup_closed_form_vs_monte_carlo(self):
        th = eng().space.median_theta()
        th.vals.update({"bringup.usb_probe_fail_p": 0.3, "bringup.monitor_fail_p": 0.2, "bringup.fw_fail_p": 0.1,
                        "bringup.inj_start_fail_p": 0.1, "bringup.max_attempts": 2})
        rnd = Rng(12)
        n = 20000
        ok = sum(1 for _ in range(n) if dm.bringup(th, rnd)["ok"])
        self.assertAlmostEqual(dm.bringup_success_prob(th), ok / n, delta=0.012)
        th.vals["bringup.max_attempts"] = 4
        self.assertGreater(dm.bringup_success_prob(th), 0.8)

    def test_pi5_limiter_state_machine(self):
        lim = dm.Pi5UsbLimiter(0.6, 0.075, 3)
        self.assertIsNone(lim.step(0.0, 0.64, 2.0))      # у межах допуску 0.6*1.075=0.645
        self.assertEqual(lim.step(1.0, 0.66, 2.0), "usb_trip")
        self.assertIsNone(lim.step(2.0, 0.9, 2.0))       # порт вимкнено
        self.assertEqual(lim.step(3.0, 0.1, 2.0), "usb_return")
        self.assertEqual(lim.step(4.0, 0.7, 2.0), "usb_trip")
        self.assertEqual(lim.step(7.0, 0.1, 2.0), "usb_return")
        self.assertEqual(lim.step(8.0, 0.7, 2.0), "usb_latched")  # 3-й поспіль
        self.assertIsNone(lim.step(100.0, 0.0, 2.0))


# ================================================================ 1b. гіперкуб пріорів
def theta_violations(th):
    """Фізичні інваріанти на одному векторі параметрів; повертає список рядків-порушень."""
    g = th.get
    v = []
    ds = (8, 50, 300, 1500, 8000)
    pl = [rf_model.path_loss_db(th, d) for d in ds]
    sn = [rf_model.snr_db(th, d) for d in ds]
    if not all(vlib.finite(x) for x in pl + sn):
        v.append("pl/snr not finite")
    if not all(a < b for a, b in zip(pl, pl[1:])):
        v.append("path loss not increasing")
    if not all(a > b for a, b in zip(sn, sn[1:])):
        v.append("snr not decreasing")
    p1, rp = g("hw.pa_p1db_out_dbm"), g("hw.pa_rapp_p")
    po = [dm.pa_output_dbm(x, p1, rp) for x in (0, 10, 20, 24, 27, 30)]
    if not all(b >= a - 1e-9 for a, b in zip(po, po[1:])):
        v.append("PA not monotone")
    comp = g("rf.tx_power_dbm") - dm.pa_output_dbm(g("rf.tx_power_dbm"), p1, rp)
    if comp < -1e-9:
        v.append("negative compression")
    ev = dm.evm_db(comp, g("hw.evm_floor_db"), g("hw.evm_comp_coeff"), g("hw.evm_temp_db_per_c") * 60)
    eff = dm.combine_snr_evm_db(40.0, ev)
    if not (vlib.finite(ev) and eff <= min(40.0, -ev) + 1e-9):
        v.append("EVM ceiling")
    prf = dm.dbm_to_mw(dm.pa_output_dbm(g("rf.tx_power_dbm"), p1, rp)) / 1000.0
    p_dc, heat, _eta = dm.air_tx_power_w(g("hw.air_bec_v"), g("power.devices.rtl8812_tx_a"), prf, dm.dbm_to_mw(p1) / 1000.0, g("hw.air_diss_frac"))
    if not (prf + heat <= p_dc * (1 + 1e-12) and heat >= 0):
        v.append("AIR energy balance")
    p_w = heat + g("hw.air_board_heat_w")
    tss = g("ext.ambient_c") + g("hw.air_ambient_rise_c") + g("ext.solar_rise_c") + g("hw.air_rth_c_per_w") * p_w
    if not (vlib.finite(tss) and p_w >= 0 and tss >= g("ext.ambient_c")):
        v.append("thermal steady state")
    for board, psu in (("pi5", 3.0), ("pi5", 5.0), ("pi4", 3.0)):
        b = power_model.budget(th, board, psu, 1, "tx", ("fc", "fan"), "active", True, False)
        if abs(b["total_a"] - sum(i[1] for i in b["items"])) > 1e-12 or abs(b["usb_a"] - sum(i[1] for i in b["items"] if i[2] == "usb")) > 1e-12:
            v.append("budget sum")
        if b["usb_a"] > b["total_a"] + 1e-12:
            v.append("usb > total")
        if abs(b["v_board"] - (g("power.psu_nominal_v") - b["total_a"] * g("power.cable_resistance_ohm"))) > 1e-12:
            v.append("v_board")
        if ("USB_OVER" in b["flags"]) != (b["usb_a"] > b["usb_budget_a"]):
            v.append("USB_OVER flag")
        if ("UNDERVOLT" in b["flags"]) != (b["v_board"] < g("power.undervolt_threshold_v")):
            v.append("UNDERVOLT flag")
    h = dm.usb_drop_rate_per_s(0.3, 0.2, g("usb.drop_rate_per_h"), g("usb.drop_v_scale"), g("usb.drop_i_scale_a"))
    if not (0 <= h <= 1):
        v.append("hazard")
    bl = dm.injection_block_prob(max(rf_model.utilisation(th, int(g("rf.mcs_index")), 8, 12), 1e-6), int(g("inj.queue_pkts")))
    if not 0 <= bl <= 1:
        v.append("block")
    lat = latency_budget.budget(th, "pi5", "h265", 30, 1280, 720, 4000.0, int(g("rf.mcs_index")), 8, 12, False)
    if not all(vlib.finite(x) and x > 0 for x in lat["total"]):
        v.append("latency")
    return v


class TestHypercube(unittest.TestCase):
    def test_prior_support_and_invariants_2000_points(self):
        e = eng()
        ths = vlib.thetas(e, 2000, 1) + vlib.corner_thetas(e, 200, 2)
        self.assertGreaterEqual(len(ths), 2000)
        bad = {}
        for i, th in enumerate(ths):
            for msg in theta_violations(th):
                bad.setdefault(msg, []).append(i)
        self.assertEqual(bad, {}, "порушення інваріантів: %s" % {k: v[:5] for k, v in bad.items()})
        for key, d, _p in e.space.dims:  # значення в носії пріора
            lo, hi = None, None
            s = d.spec
            if d.kind == "uniform":
                lo, hi = s["lo"], s["hi"]
            elif d.kind == "triangular":
                lo, hi = s["lo"], s["hi"]
            elif d.kind == "beta":
                lo, hi = s.get("lo", 0.0), s.get("hi", 1.0)
            elif "lo" in s or "hi" in s:
                lo, hi = s.get("lo"), s.get("hi")
            for th in ths[:300]:
                x = th.get(key)
                self.assertTrue(vlib.finite(x), key)
                if lo is not None:
                    self.assertGreaterEqual(x, lo - 1e-9, key)
                if hi is not None:
                    self.assertLessEqual(x, hi + 1e-9, key)

    def _check_outputs(self, res):
        for i, r in enumerate(res):
            for k, v in r.items():
                if isinstance(v, float):
                    self.assertTrue(vlib.finite(v), "%s=%r (draw %d)" % (k, v, i))
            for k in ("residual", "availability", "freeze"):
                self.assertTrue(0.0 <= r[k] <= 1.0, "%s=%r" % (k, r[k]))
            self.assertGreaterEqual(r["range_m"], 0.0)
            self.assertLessEqual(r["ttff_s"], 600.0 + 1e-9)
            # D4 (ВИПРАВЛЕНО): мертвий лінк не має margin (None + dead=True); живий має скінченний margin, а не заглушку
            self.assertEqual(r["margin_db"] is None, r["dead"], "margin_db None <=> dead")
            self.assertEqual(r["margin_p5_db"] is None, r["dead"])
            if not r["dead"]:
                self.assertGreaterEqual(r["margin_db"], -150.0)
            self.assertGreater(r["g2g_ms"], 0.0)
            self.assertTrue(all(isinstance(x, bool) for x in r["flags"].values()))
            self.assertGreaterEqual(r["down_s"], 0.0)

    def test_sessions_finite_and_bounded(self):
        n = 2000 if LONG else 250
        e = eng()
        self._check_outputs([dm.run_session(th, e.cfg, Rng(se.subseed(3, i, 1))) for i, th in enumerate(vlib.thetas(e, n, 21))])
        cor = vlib.corner_thetas(e, 400 if LONG else 60, 22)
        self._check_outputs([dm.run_session(th, e.cfg, Rng(se.subseed(4, i, 1))) for i, th in enumerate(cor)])

    def test_summary_percentiles_ordered_modes_probabilities(self):
        for name in ("nominal_pi5_5a_150m", "pi5_3a_weak_psu"):
            e = eng(name)
            s = se.summarize(e, e.run(80, 1))
            for o, d in s["outputs"].items():
                p = d["p"]
                if d["n"] == 0:  # D4: усі draw мертві -> у margin_db немає що порівнювати (nan), але це не фізичне число
                    self.assertTrue(all(math.isnan(x) for x in p.values()), o)
                    continue
                self.assertLessEqual(p[5], p[50] + 1e-12)
                self.assertLessEqual(p[50], p[95] + 1e-12)
                self.assertLessEqual(p[95], p[99] + 1e-12)
            for m, pr in s["modes"].items():
                self.assertTrue(0.0 <= pr <= 1.0, m)

    def test_button_engine_probabilities(self):
        e = eng("button_nominal")
        for r in e.run(25, 1):
            for k in ("false_event", "false_event_single", "false_event_long", "single_extra", "long_as_single", "long_missed"):
                self.assertTrue(0.0 <= r[k] <= 1.0, k)
            self.assertAlmostEqual(r["false_event"], 0.5 * (r["false_event_single"] + r["false_event_long"]), delta=1e-12)

    def test_monotone_in_distance_beyond_saturation(self):
        e = vlib.neutral_engine(extra={"rf.fading_model": "none"})
        th = e.space.median_theta()
        plan = dm.prepare(th, e.cfg, Rng(1))
        st = {"plin": th.get("rf.tx_power_dbm"), "p1db": plan["p1db0"], "evm_shift": 0.0, "ant": 0.0, "shadow": 0.0, "noise_extra": 0.0, "burst": False}
        ms = [dm.link_eval(plan, d, st)["margin"] for d in (50, 100, 200, 400, 800, 1600, 3200)]
        self.assertTrue(all(b < a for a, b in zip(ms, ms[1:])))
        rs = [dm.residual_of(plan, dm.link_eval(plan, d, st)["per"]) for d in (200, 400, 800, 1000, 1500, 3000)]
        self.assertTrue(all(b >= a - 1e-15 for a, b in zip(rs, rs[1:])))

    def test_temperature_monotone_in_ambient_and_bec_current(self):
        out = []
        for amb in (-5.0, 15.0, 35.0):
            e = vlib.engine("hot_day_closed_case", sets={"ext.ambient_c": amb, "proc.age_hours": 0.0})
            th = e.space.median_theta()
            out.append(dm.run_session(th, e.cfg, Rng(1))["tj_end_c"])
        self.assertTrue(out[0] < out[1] < out[2])
        tj = []
        for cur in (0.6, 0.9, 1.3):
            e = vlib.engine("hot_day_closed_case", sets={"power.devices.rtl8812_tx_a": cur})
            tj.append(dm.run_session(e.space.median_theta(), e.cfg, Rng(1))["tj_end_c"])
        self.assertTrue(tj[0] < tj[1] < tj[2])


# ================================================================ 2. крос-модельна узгодженість
class TestPowerBudgetAndTimeline(unittest.TestCase):
    def test_budget_conservation_random(self):
        P = common.load()
        r = random.Random(5)
        for _ in range(400):
            board = r.choice(power_model.BOARDS)
            psu = r.choice((None, 2.5, 3.0, 4.0, 5.0))
            st = r.choice(("idle", "rx", "tx"))
            w = tuple(x for x in ("fc", "webcam", "fan", "csi") if r.random() < 0.5)
            ad = r.randint(0, 2)
            b = power_model.budget(P, board, psu, ad, st, w, r.choice(("idle", "active", "load")), r.random() < 0.5, r.random() < 0.5)
            self.assertAlmostEqual(b["total_a"], sum(i[1] for i in b["items"]), delta=1e-12)
            self.assertAlmostEqual(b["usb_a"], sum(i[1] for i in b["items"] if i[2] == "usb"), delta=1e-12)
            self.assertLessEqual(b["usb_a"], b["total_a"] + 1e-12)
            self.assertAlmostEqual(b["psu_margin_a"], b["psu_a"] - b["total_a"], delta=1e-12)
            self.assertAlmostEqual(b["usb_margin_a"], b["usb_budget_a"] - b["usb_a"], delta=1e-12)
            self.assertAlmostEqual(b["v_board"], 5.1 - b["total_a"] * 0.15, delta=1e-12)
            # більше адаптерів => не менший струм
            b2 = power_model.budget(P, board, psu, ad + 1, st, w, "active", False, False)
            b1 = power_model.budget(P, board, psu, ad, st, w, "active", False, False)
            self.assertGreater(b2["total_a"], b1["total_a"])

    def test_pi5_usb_limit_rules(self):
        P = common.load()
        self.assertEqual(power_model.usb_budget_a(P, "pi5", 3.0, False), 0.6)
        self.assertEqual(power_model.usb_budget_a(P, "pi5", 5.0, False), 1.6)
        self.assertEqual(power_model.usb_budget_a(P, "pi5", 4.0, False), 0.6)
        self.assertEqual(power_model.usb_budget_a(P, "pi5", 3.0, True), 1.6)
        self.assertEqual(power_model.usb_budget_a(P, "pi4", 3.0, False), 1.2)

    def test_timeline_invariants(self):
        P = common.load()
        for name in ("pi5_3a_tx", "pi4_3a_dip", "pi5_5a_ok"):
            for seed in (1, 2, 3):
                ev = power_model.timeline(P, power_model.SCENARIOS[name], seed)
                ts = [e["t_s"] for e in ev]
                self.assertEqual(ts, sorted(ts))
                state, drops = None, {}
                for e in ev:
                    if e["kind"] == "undervoltage":
                        self.assertNotEqual(state, "uv")
                        state = "uv"
                        self.assertLess(e["v"], 4.63)
                    elif e["kind"] == "voltage_ok":
                        self.assertEqual(state, "uv")
                        state = "ok"
                        self.assertGreaterEqual(e["v"], 4.63)
                    elif e["kind"] == "usb_drop":
                        self.assertNotIn(e["device"], drops)
                        drops[e["device"]] = e["t_s"]
                    elif e["kind"] == "usb_return":
                        self.assertIn(e["device"], drops)
                        self.assertGreaterEqual(e["t_s"] - drops.pop(e["device"]), 3.0 - 0.06)  # >= usb_reenum_s
        ev = power_model.timeline(P, power_model.SCENARIOS["pi5_5a_ok"], 1)
        self.assertEqual([e for e in ev if e["kind"] in ("usb_drop", "undervoltage")], [])  # БЖ 5 А: без відмов
        ev3 = power_model.timeline(P, power_model.SCENARIOS["pi5_3a_tx"], 1)
        first = [e for e in ev3 if e["kind"] == "usb_drop"][0]
        self.assertEqual(first["reason"], "overcurrent")
        self.assertEqual(first["device"], "rtl8812")  # найбільше споживання
        b = power_model.budget(P, "pi5", 3.0, 1, "tx", ("fc", "webcam"), "active", True, False)
        self.assertEqual(b["verdict"], "FAIL")


class TestCrossModel(unittest.TestCase):
    def test_usb_limit_and_currents_agree_between_power_and_degrade(self):
        for psu, umc in ((3.0, False), (5.0, False), (3.0, True), (4.0, False), (5.0, True)):
            e = vlib.engine("nominal_pi5_5a_150m", cfg_over={"psu_a": psu, "usb_max_current": umc})
            for th in vlib.thetas(e, 25, 7):
                plan = dm.prepare(th, e.cfg, Rng(1))
                self.assertEqual(plan["limit"], power_model.usb_budget_a(th, "pi5", psu, umc))
                w = tuple(e.cfg["gs_with"])
                self.assertAlmostEqual(plan["i_usb_tx"], power_model.budget(th, "pi5", psu, 1, "tx", w, "active", False, umc)["usb_a"], delta=1e-12)
                self.assertAlmostEqual(plan["i_usb_rx"], power_model.budget(th, "pi5", psu, 1, "rx", w, "active", False, umc)["usb_a"], delta=1e-12)
                self.assertAlmostEqual(plan["i_usb_pk"], power_model.budget(th, "pi5", psu, 1, "tx", w, "active", True, umc)["usb_a"], delta=1e-12)
                self.assertGreaterEqual(plan["i_usb_pk"], plan["i_usb_tx"])
                self.assertGreaterEqual(plan["i_usb_tx"], plan["i_usb_rx"])

    def _frozen(self, e, th, plan):
        return {"plin": th.get("rf.tx_power_dbm"), "p1db": plan["p1db0"], "evm_shift": 0.0, "ant": 0.0, "shadow": 0.0, "noise_extra": 0.0, "burst": False}

    def test_zero_degradation_equals_rf_model(self):
        # «нульова деградація = базова модель»: рівень на вході, SNR-поріг, PER, діапазон
        for fading in ("none", "rician"):
            e = vlib.neutral_engine(extra={"rf.fading_model": fading, "rf.loss_model": "iid"})
            th = e.space.median_theta()
            plan = dm.prepare(th, e.cfg, Rng(1))
            st = self._frozen(e, th, plan)
            for d in (150, 400, 600, 800):
                le = dm.link_eval(plan, d, st)
                self.assertAlmostEqual(le["rx"], rf_model.rx_dbm(th, d), delta=1e-9)
                self.assertAlmostEqual(le["pen"], 0.0)
                per_rf = rf_model.frame_per(th, plan["mcs"], rf_model.snr_db(th, d))
                tol = 0.05 if fading == "none" else 0.30
                if per_rf > (1e-5 if fading == "none" else 0.10):  # хвіст PER при завмиранні див. D9
                    self.assertAlmostEqual(le["per"] / per_rf, 1.0, delta=tol, msg="%s %dm" % (fading, d))
            rng_rf, _ = rf_model.max_range(th, plan["mcs"], plan["k"], plan["n"])
            rng_e, _ = dm.range_at_target(plan, st, e.cfg["spec"]["residual"])
            self.assertAlmostEqual(rng_e / rng_rf, 1.0, delta=0.03)
            # поріг: margin = 0 там, де eff-SNR = SNR(10 % PER) без завмирання
            if fading == "none":  # з завмиранням margin рушія відлічується від AWGN-порогу, а snr_for_per усереднює завмирання (різні визначення)
                snr10 = rf_model.snr_for_per(th, plan["mcs"])  # той самий вектор параметрів (diversity/ldpc/імплементаційні втрати)
                le = dm.link_eval(plan, 700.0, st)
                self.assertAlmostEqual(le["margin"], rf_model.snr_db(th, 700.0) - snr10, delta=0.3)

    def test_zero_degradation_session_outputs(self):
        e = vlib.neutral_engine(extra={"rf.fading_model": "none", "rf.loss_model": "iid"})
        th = e.space.median_theta()
        r = dm.run_session(th, e.cfg, Rng(1))
        self.assertEqual(r["availability"], 1.0)
        self.assertEqual(r["residual"], 0.0)
        self.assertFalse(any(r["flags"].values()), [k for k, v in r["flags"].items() if v])
        P = th
        th.vals["rf.fading_model"] = "none"
        self.assertAlmostEqual(r["margin_db"], rf_model.snr_db(P, 150) - rf_model.snr_for_per(th, 1), delta=0.3)

    def test_latency_engine_vs_budget(self):
        e = vlib.neutral_engine(extra={"rf.fading_model": "none", "rf.loss_model": "iid"})
        th = e.space.median_theta()
        plan = dm.prepare(th, e.cfg, Rng(1))
        k, n = int(th.get("rf.fec_k")), int(th.get("rf.fec_n"))
        b = latency_budget.budget(th, "pi5", "h265", int(th.get("video.fps")), int(th.get("video.width")), int(th.get("video.height")),
                                  th.get("video.bitrate_kbps"), plan["mcs"], k, n, False)
        self.assertAlmostEqual(plan["lat_total"], b["total"][1], delta=1e-9)
        self.assertAlmostEqual(b["total"][1], sum(b["terms"][t][1] for t in latency_budget.TERMS), delta=1e-9)
        self.assertAlmostEqual(plan["fec_full_ms"], latency_budget.radio_terms(th, plan["mcs"], k, n, 30, th.get("video.bitrate_kbps"), True)[1][2], delta=1e-9)
        self.assertAlmostEqual(plan["util"], rf_model.utilisation(th, plan["mcs"], k, n), delta=1e-12)
        r = dm.run_session(th, e.cfg, Rng(1))
        jitter_mean = th.get("timing.sched_jitter_median_ms") * math.exp(th.get("timing.sched_jitter_sigma") ** 2 / 2)
        self.assertAlmostEqual(r["g2g_mean_ms"], plan["lat_total"] + jitter_mean, delta=4.0)  # лише середній джитер планувальника
        self.assertGreaterEqual(r["g2g_ms"], plan["lat_total"] - 1e-9)
        # незалежний перерахунок радіо-доданка (typ): ppf * (airtime + доступ) * (1 + паритет/2)
        ppf = max(1, math.ceil(th.get("video.bitrate_kbps") * 1000 / 8 / 30 / th.get("rf.payload_bytes")))
        t = (rf_model.airtime_us(rf_model.frame_bytes(th), plan["mcs"], 20, False, False, 1) + th.get("rf.mac_access_us")) / 1000.0
        self.assertAlmostEqual(b["terms"]["radio"][1], ppf * t * (1 + (n - k) / k / 2), delta=1e-9)

    def test_bitrate_overshoot_feeds_utilisation(self):
        e0 = vlib.neutral_engine(extra={"vid.bitrate_overshoot": 1.0})
        e1 = vlib.neutral_engine(extra={"vid.bitrate_overshoot": 1.5})
        p0 = dm.prepare(e0.space.median_theta(), e0.cfg, Rng(1))
        p1 = dm.prepare(e1.space.median_theta(), e1.cfg, Rng(1))
        self.assertAlmostEqual(p1["util"] / p0["util"], 1.5, delta=0.02)

    def test_determinism_and_antithetic_pairing(self):
        e = eng()
        a, b = e.run(6, 3), e.run(6, 3)
        self.assertEqual([x["residual"] for x in a], [x["residual"] for x in b])
        c = e.run(6, 4)
        self.assertNotEqual([x["margin_db"] for x in a], [x["margin_db"] for x in c])
        r0, r1 = Rng(5, False), Rng(5, True)
        for _ in range(20):
            self.assertAlmostEqual(r0.u() + r1.u(), 1.0, delta=1e-12)
        th0, _ = e.draw(2, 9, True)
        th1, _ = e.draw(3, 9, True)  # пара: u і 1-u
        for key, d, _p in e.space.dims[:40]:
            if d.kind == "uniform":
                self.assertAlmostEqual(th0.get(key) + th1.get(key), d.spec["lo"] + d.spec["hi"], delta=1e-9)

    def test_scenario_pins_override_priors(self):
        e = vlib.engine("nominal_pi5_5a_150m", sets={"rf.tx_power_dbm": 21.0})
        for th in vlib.thetas(e, 5, 1):
            self.assertEqual(th.get("rf.tx_power_dbm"), 21.0)
            self.assertEqual(th.get("rf.fading_model"), "rician")


# ================================================================ 3. статистика
class TestStatistics(unittest.TestCase):
    def test_antithetic_reduces_variance_of_monotone_output(self):
        e = eng()

        def est(seed, anti, n=40):
            xs = []
            for i in range(n):
                th, _rp = e.draw(i, seed, anti)
                xs.append(rf_model.snr_db(th, 800.0))
            return sum(xs) / n
        reps = 50
        vp = vlib.var([est(1000 + s, False) for s in range(reps)])
        va = vlib.var([est(1000 + s, True) for s in range(reps)])
        self.assertLess(va / vp, 0.7, "антитетика не знижує дисперсію монотонного виходу (%.2f)" % (va / vp))

    def test_bootstrap_ci_shrinks_as_inverse_sqrt_n(self):
        r = random.Random(1)
        pool = [r.gauss(0, 1) for _ in range(20000)]
        rnd = random.Random(2)
        h200 = vlib.bootstrap_halfwidth(pool, 200, 50, rnd, B=150)
        h800 = vlib.bootstrap_halfwidth(pool, 800, 50, rnd, B=150)
        self.assertGreater(h200 / h800, 1.4)
        self.assertLess(h200 / h800, 2.9)

    def test_spearman_helper(self):
        self.assertAlmostEqual(vlib.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(vlib.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertAlmostEqual(vlib.spearman([1, 1, 1, 2], [1, 2, 3, 4]), vlib.spearman([1, 1, 1, 2], [1, 2, 3, 4]))

    def test_morris_button_ranking_stable_between_seeds(self):
        e = eng("button_nominal")
        tabs = []
        for s in (1, 2):
            t, _n = se.morris(e, 8, s, ["false_event"])
            tabs.append({k: m for m, _sg, k, _p in t["false_event"]})
        keys = sorted(tabs[0])
        rho = vlib.spearman([tabs[0][k] for k in keys], [tabs[1][k] for k in keys])
        self.assertGreater(rho, 0.3, "Morris кнопки нестабільний між seed: rho=%.2f" % rho)

    def test_dead_link_has_no_margin_not_a_sentinel(self):
        # D4 (ВИПРАВЛЕНО): неробочий лінк = margin None + dead=True; residual=1 / availability=0 лишаються фізичними межами
        e = eng("pi5_3a_weak_psu")
        res = e.run(40, 2)
        for r in res:
            if r["dead"]:
                self.assertIsNone(r["margin_db"])
                self.assertEqual(r["availability"], 0.0)
                self.assertEqual(r["residual"], 1.0)
        self.assertTrue(any(r["dead"] for r in res))


# ================================================================ 4. back-test проти зовнішніх довідкових даних
class TestBacktestExternal(unittest.TestCase):
    def test_min_sensitivity_shape_vs_standard(self):
        rows = backtest.sens_rows(vht=True)
        s = backtest.sens_summary(rows)
        self.assertTrue(s["monotone"])
        self.assertLess(s["offset_spread"], 2.5)          # форма між MCS збігається зі стандартом
        self.assertLess(s["step_err_max"], 1.2)
        for _m, _std, _mod, d in rows:
            self.assertLess(d, -2.0)                       # модель чутливіша за МІНІМУМ стандарту (мінімум = вимога, а не типове)
            self.assertGreater(d, -8.0)

    def test_bandwidth_penalty_3db(self):
        P20, P40 = common.load({"rf.bandwidth_mhz": 20}), common.load({"rf.bandwidth_mhz": 40})
        self.assertAlmostEqual(rf_model.noise_dbm(P40) - rf_model.noise_dbm(P20), 3.0, delta=0.05)  # SRC: +3 дБ на подвоєння смуги

    def test_fspl_reference_rows(self):
        for d, f, ref, mod in backtest.fspl_rows():
            self.assertAlmostEqual(ref, mod, delta=0.02)

    def test_ns3_error_model_identical(self):
        self.assertLess(backtest.ns3_max_rel_diff(), 1e-9)
        for b, (fac, terms) in backtest.NS3_PE.items():
            self.assertAlmostEqual(rf_model._SPECTRA[b][0], fac, delta=1e-12)
            self.assertEqual([tuple(t) for t in rf_model._SPECTRA[b][1]], [tuple(t) for t in terms])

    def test_raspberry_pi_doc_values(self):
        for name, ref, mod in backtest.pi_rows():
            self.assertAlmostEqual(ref, mod, delta=1e-9, msg=name)
        for b, doc, mod in backtest.throttled_rows():
            self.assertEqual(doc, mod)
        for i, mask in ((0, 1), (16, 0x10000), (18, 0x40000)):
            self.assertEqual(1 << i, mask)
        self.assertEqual(power_model.decode_throttled(0x50005), ["Undervoltage detected", "Currently throttled", "Undervoltage has occurred", "Throttling has occurred"])

    def test_unavailable_sources_are_declared(self):
        self.assertGreaterEqual(len(backtest.UNAVAILABLE), 4)
        text = " ".join(w + y for w, y in backtest.UNAVAILABLE)
        self.assertIn("недоступно", text)


# ================================================================ 5. структурна невизначеність (інструмент)
class TestAlternativeForms(unittest.TestCase):
    def test_logistic_anchor_and_restore(self):
        orig = rf_model.per_ideal
        base = [orig(1, s, 1456, False) for s in (4, 8, 12)]
        s10 = altforms._s10(orig, 1, False, 1456)
        with altforms.per_logistic(1.0):
            self.assertAlmostEqual(rf_model.per_ideal(1, s10, 1456, False), 0.1, delta=1e-6)
            vals = [rf_model.per_ideal(1, s, 1456, False) for s in range(-5, 40)]
            self.assertTrue(all(b <= a + 1e-15 for a, b in zip(vals, vals[1:])))
            self.assertAlmostEqual(dm.per_threshold_db(dm.per_table(1, False, 1456)), s10, delta=0.3)
        self.assertIs(rf_model.per_ideal, orig)
        self.assertEqual(base, [rf_model.per_ideal(1, s, 1456, False) for s in (4, 8, 12)])
        self.assertGreater(abs(altforms.baseline_log_odds_slope(1)), 1.5)  # базова форма різка

    def test_alternatives_run_and_restore(self):
        orig = (dm.usb_drop_rate_per_s, dm.Thermal, rf_model.per_ideal)
        for name, mk in altforms.FORMS.items():
            with mk():
                e = vlib.engine()
                res = e.run(8, 1)
                self._ok(res)
        self.assertEqual(orig, (dm.usb_drop_rate_per_s, dm.Thermal, rf_model.per_ideal))

    def _ok(self, res):
        for r in res:
            self.assertTrue(0 <= r["residual"] <= 1 and 0 <= r["availability"] <= 1)

    def test_hazard_alternatives_agree_with_base_at_zero_margin_side(self):
        args = (0.5, 0.4, 0.05, 0.1, 0.19)
        base = dm.usb_drop_rate_per_s(*args)
        with altforms.hazard_form("capped"):
            self.assertAlmostEqual(dm.usb_drop_rate_per_s(*args), base, delta=1e-15)
        with altforms.hazard_form("step"):
            self.assertAlmostEqual(dm.usb_drop_rate_per_s(*args), 0.05 / 3600, delta=1e-15)  # x<0 => базова інтенсивність


# ================================================================ 6. ЗНАЙДЕНІ невідповідності (фіксуються, поки не виправлені)
class TestKnownDefects(unittest.TestCase):
    """Кожен тест = один пункт «Знайдені невідповідності» в docs/SIM-VALIDATION.md. Падіння = дефект виправлено: онови доку й тест."""

    def test_D2_usb_overcurrent_thresholds_disagree(self):
        P = common.load({"power.devices.fc_usb_a": 0.17})  # rx 0.45 + fc 0.17 = 0.62 А при ліміті 0.6 А
        b = power_model.budget(P, "pi5", 3.0, 1, "rx", ("fc",), "active", False, False)
        self.assertAlmostEqual(b["usb_a"], 0.62, delta=1e-9)
        self.assertIn("USB_OVER", b["flags"])
        lim = dm.Pi5UsbLimiter(b["usb_budget_a"], 0.075, 4)  # tol = типовий prior usb.pi5_trip_tol
        self.assertIsNone(lim.step(0.0, b["usb_a"], 2.0), "D2 виправлено? limiter тепер тріпає при бюджетному USB_OVER")

    def test_D3_fec_residual_quantisation_error(self):
        r = random.Random(1)
        worst = 0.0
        for _ in range(600):
            p = 10 ** r.uniform(-3, -0.5)
            ex = rf_model.residual_iid(p, 8, 12)
            if ex > 1e-12:
                worst = max(worst, abs(dm.fec_residual(p, 3.0, 8, 12, False) - ex) / ex)
        self.assertGreater(worst, 0.03, "D3 виправлено? макс. відносна похибка %.3f" % worst)
        self.assertLess(worst, 0.15)

    def test_D5_soc_prior_exceeds_documented_hard_limit(self):
        sp = backtest.soc_prior_rows()
        self.assertGreater(sp["hi"], backtest.PI_THERMAL_LIMIT_C)
        self.assertGreater(sp["p_above_hard_limit"], 0.03)

    def test_D6_any_slice_flags_depend_on_time_step(self):
        # прапор «хоч в одному зрізі» росте з числом зрізів: latency_creep при dt=5 с проти dt=40 с (n=140, seed 1)
        pr = {}
        for dt in (5.0, 40.0):
            e = vlib.engine("nominal_pi5_5a_150m", cfg_over={"dt_s": dt})
            res = e.run(140, 1)
            pr[dt] = sum(1 for r in res if r["flags"]["latency_creep"]) / len(res)
        self.assertGreater(pr[5.0], 1.8 * pr[40.0], "D6 виправлено? P(latency_creep) dt=5: %.3f, dt=40: %.3f" % (pr[5.0], pr[40.0]))

    def test_D12_weak_psu_scenario_is_degenerate(self):
        e = eng("pi5_3a_weak_psu")
        res = e.run(60, 1)
        dead = sum(1 for r in res if r["availability"] == 0.0) / len(res)
        sent = sum(1 for r in res if r["dead"]) / len(res)  # (D4: раніше margin == -60; тепер явний прапорець dead)
        self.assertGreater(dead, 0.9, "D12 виправлено? частка мертвих лінків %.2f" % dead)
        self.assertGreater(sent, 0.9)

    def test_D7_ldpc_has_no_effect_without_gain_parameter(self):
        a = rf_model.frame_per(common.load({"rf.ldpc": 0}), 1, 8.0)
        b = rf_model.frame_per(common.load({"rf.ldpc": 1}), 1, 8.0)
        self.assertEqual(a, b)
        c = rf_model.frame_per(common.load({"rf.ldpc": 1, "rf.ldpc_gain_db": 1.5}), 1, 8.0)
        self.assertLess(c, b)

    def test_D8_margin_not_monotone_in_saturated_zone(self):
        e = vlib.neutral_engine(extra={"hw.rx_agc_knee_dbm": -40.0, "hw.rx_agc_slope": 1.35, "rf.fading_model": "none"})
        th = e.space.median_theta()
        plan = dm.prepare(th, e.cfg, Rng(1))
        st = self._st(th, plan)
        m = [dm.link_eval(plan, d, st)["margin"] for d in (3, 6, 12)]
        self.assertTrue(m[1] > m[0], "D8: margin(d) тепер монотонно спадає в зоні AGC: %s" % m)

    def _st(self, th, plan):
        return {"plin": th.get("rf.tx_power_dbm"), "p1db": plan["p1db0"], "evm_shift": 0.0, "ant": 0.0, "shadow": 0.0, "noise_extra": 0.0, "burst": False}

    def test_D10_queue_ignores_deterministic_service(self):
        import studies
        cf = dm.injection_block_prob(1.0, 5)
        md = studies.queue_sim(1.0, 5, 60000, "det", 3)
        self.assertGreater(cf / md, 1.25, "D10 виправлено? M/M/1/K / M/D/1/K = %.2f" % (cf / md))

    def test_D11_pi3bp_rows_come_from_3b_column(self):
        # док-таблиця струмів має стовпець 3B, не 3B+; params.json позначає ці рядки SRC для pi3bp
        P = common.load()
        self.assertEqual(P.get("power.boards.pi3bp.board_idle_a"), backtest.PI_DOC_TABLE["pi3b_column"]["idle_avg"])
        self.assertEqual(P.prov("power.boards.pi3bp.board_idle_a"), "SRC")


# ================================================================ 6b. ВИПРАВЛЕНІ невідповідності (постійні регресійні тести)
def _rician_pdf_integral(tab, snr_db, k_db, n=240000, xmax=4.0):
    """Незалежний еталон (не код рушія): середній PER по Райсу з унормованою щільністю, інтегрування по x (середина) з I0 рядом Тейлора."""
    k = 10 ** (k_db / 10.0)

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
        tot += p * dm.per_lookup(tab, snr_db + 10 * math.log10(x))
    return tot / mass


class TestFixedDefects(unittest.TestCase):
    """Виправлені невідповідності (D1, D1b, D4, D9; docs/SIM-VALIDATION.md §3): якщо дефект повернеться, тест упаде."""

    def test_D1_air_temperature_grows_with_rf_tx_power(self):
        tj, rf, dc = [], [], []
        for tx in (20.0, 24.0, 27.0):
            e = vlib.engine("hot_day_closed_case", sets={"rf.tx_power_dbm": tx})
            r = dm.run_session(e.space.median_theta(), e.cfg, Rng(1))
            tj.append(r["tj_end_c"])
            rf.append(r["air_energy"]["rf_w"])
            dc.append(r["air_energy"]["dc_w"])
            self.assertAlmostEqual(r["air_energy"]["dc_w"] - r["air_energy"]["rf_w"], r["air_energy"]["heat_w"], delta=1e-12)
        self.assertTrue(tj[0] + 1.0 < tj[1] and tj[1] + 1.0 < tj[2], "Tj має рости з потужністю TX: %s" % tj)  # було 98,012 у всіх трьох
        self.assertTrue(rf[0] < rf[1] < rf[2] and dc[0] < dc[1] < dc[2], "P_dc і P_rf ростуть разом")

    def test_D1_heat_is_dc_minus_rf_and_grows_with_power_and_current(self):
        r = random.Random(7)
        for _ in range(2000):
            v, i, dfr = r.uniform(4.6, 5.4), r.uniform(0.3, 2.0), r.uniform(0.7, 0.95)
            ref = r.uniform(0.05, 1.3)
            prf = r.uniform(0.0, 1.5)
            pdc, heat, eta = dm.air_tx_power_w(v, i, prf, ref, dfr)
            self.assertAlmostEqual(pdc - prf, heat, delta=1e-12)
            self.assertGreaterEqual(pdc, prf)
            self.assertGreaterEqual(heat, 0.0)
            self.assertLessEqual(eta, 1.0)
            self.assertGreater(dm.air_tx_power_w(v, i, prf + 0.05, ref, dfr)[1], heat)       # більше ВЧ -> більше тепла
            self.assertGreater(dm.air_tx_power_w(v, i + 0.05, prf, ref, dfr)[1], heat)       # більше струму -> більше тепла
            self.assertGreater(dm.air_tx_power_w(v, i + 0.05, prf, ref, dfr)[0], pdc)

    def test_D1b_energy_conserved_on_whole_prior(self):
        # P_rf + heat == P_dc і P_rf <= P_dc на 3000 випадкових точках пріорів і 300 вершинах (було: порушення у ~3 % точок)
        e = eng()
        ths = vlib.thetas(e, 3000, 3) + vlib.corner_thetas(e, 300, 5)
        clamped = 0
        for th in ths:
            g = th.get
            prf = dm.dbm_to_mw(dm.pa_output_dbm(g("rf.tx_power_dbm"), g("hw.pa_p1db_out_dbm"), g("hw.pa_rapp_p"))) / 1000.0
            pdc, heat, eta = dm.air_tx_power_w(g("hw.air_bec_v"), g("power.devices.rtl8812_tx_a"), prf, dm.dbm_to_mw(g("hw.pa_p1db_out_dbm")) / 1000.0, g("hw.air_diss_frac"))
            self.assertLessEqual(prf + heat, pdc * (1 + 1e-12))
            self.assertGreaterEqual(heat, 0.0)
            self.assertLessEqual(prf / pdc, eta + 1e-12)
            clamped += eta > 1.0 - g("hw.air_diss_frac") + 1e-12
        # частка точок, де виміряний струм «виграє» в ККД-пріора (припущення моделі, див. SIM-VALIDATION D1b): інформаційно, не 100 %
        self.assertLess(clamped / len(ths), 0.6)

    def test_D4_dead_link_is_a_flag_not_a_stub_value(self):
        e = eng("pi5_3a_weak_psu")
        res = e.run(40, 1)
        dead = [r for r in res if r["dead"]]
        self.assertGreater(len(dead), 30)
        for r in dead:
            self.assertIsNone(r["margin_db"])
            self.assertIsNone(r["margin_p5_db"])
        s = se.summarize(e, res)
        self.assertEqual(s["outputs"]["margin_db"]["n"], len(res) - len(dead))
        self.assertAlmostEqual(s["dead_frac"], len(dead) / len(res))
        txt = "\n".join(se.fmt_report(e, 40, 1, False, s))
        self.assertIn("P(dead link", txt)
        # на nominal живі draw не мають заглушки -60, а мертві (якщо є) - не мають margin взагалі
        res = eng().run(200, 1)
        self.assertFalse(any(r["margin_db"] == -60.0 for r in res))
        self.assertTrue(all((r["margin_db"] is None) == r["dead"] for r in res))

    def test_D4_morris_does_not_mix_dead_state_into_margin(self):
        # синтетичний рушій: один параметр перемикає лінк «живий/мертвий», margin живого лінку = const 10 дБ. Стара заглушка -60
        # давала б для margin_db mu* ~ 50 дБ; тепер margin_db не залежить ні від чого, а перемикання видно лише в рядку dead
        e = eng()
        key = "power.tx_peak_factor"

        def fake(th, _rng):
            dead = th.get(key) > e.space.median_theta().get(key)
            r = {"margin_db": None if dead else 10.0, "dead": dead}
            r.update({o: 0.5 for o in se.SENS_LINK if o != "margin_db"})
            return r
        with vlib.patched(e, "evaluate", fake):
            tab, noise = se.morris(e, 4, 1, list(se.SENS_LINK) + [se.SENS_DEAD])
        self.assertTrue(all(mu == 0.0 for mu, _sg, _k, _p in tab["margin_db"]))
        self.assertEqual(tab[se.SENS_DEAD][0][2], key)
        self.assertGreater(tab[se.SENS_DEAD][0][0], 0.5)
        self.assertEqual(noise["margin_db"], 0.0)

    def test_D9_fading_average_matches_independent_integration_in_the_tail(self):
        # Райс K=10 дБ, MCS1, 1456 Б: еталон = пряме інтегрування щільності (код тесту, не рушія); рушій раніше 2,4e-7 проти 2,2e-3 при 15 дБ
        kdb = 10.0
        tab = dm.per_table(1, False, 1456)
        ftab = dm.fading_per_table(1, False, 1456, "rician", kdb)
        for snr, tol in ((5.0, 0.03), (8.0, 0.03), (12.0, 0.03), (15.0, 0.03), (20.0, 0.04), (25.0, 0.05)):
            ref = _rician_pdf_integral(tab, snr, kdb, n=30000 if not LONG else 240000)
            got = dm.per_lookup(ftab, snr)
            self.assertAlmostEqual(got / ref, 1.0, delta=tol, msg="snr=%s ref=%.4g engine=%.4g" % (snr, ref, got))

    def test_D9_engine_matches_monte_carlo_reference_and_beats_rf_model_noise(self):
        kdb = 10.0
        e = vlib.neutral_engine(extra={"rf.fading_model": "rician", "rf.loss_model": "iid", "rf.rician_k_db": kdb})
        th = e.space.median_theta()
        plan = dm.prepare(th, e.cfg, Rng(1))
        rnd = random.Random(99)
        k = 10 ** (kdb / 10)
        los, sg = math.sqrt(k / (k + 1)), math.sqrt(1 / (2 * (k + 1)))
        g = [(los + sg * rnd.gauss(0, 1)) ** 2 + (sg * rnd.gauss(0, 1)) ** 2 for _ in range(100000)]
        m = sum(g) / len(g)
        tab = plan["tab"]
        for snr, tol in ((8.0, 0.1), (15.0, 0.3)):  # шумовий еталон 1e5 draw: хвіст 15 дБ має ~10 % статистичної похибки
            ref = sum(dm.per_lookup(tab, snr + 10 * math.log10(x / m)) for x in g) / len(g)
            self.assertAlmostEqual(dm.per_lookup(plan["ftab"], snr) / ref, 1.0, delta=tol, msg="snr=%s" % snr)

    def test_D9_other_fading_models_and_weights(self):
        w = dm.fading_weights("rician", 10.0)
        self.assertAlmostEqual(sum(w), 1.0, delta=1e-12)
        self.assertAlmostEqual(sum(x * 10 ** ((dm._FADE_JLO + i) * 0.25 / 10.0) for i, x in enumerate(w)), 1.0, delta=2e-3)  # одиничне середнє за потужністю
        w0 = dm.fading_weights("rayleigh", 0.0)
        self.assertAlmostEqual(sum(x * 10 ** ((dm._FADE_JLO + i) * 0.25 / 10.0) for i, x in enumerate(w0)), 1.0, delta=2e-3)
        # Релей: еталон = рівноймовірнісні квантилі (точна формула -ln(1-u)), 100000 вузлів
        tab = dm.per_table(1, False, 1456)
        ft = dm.fading_per_table(1, False, 1456, "rayleigh", 0.0)
        gs = [-math.log(1 - (i + 0.5) / 100000) for i in range(100000)]
        for snr in (10.0, 20.0, 30.0):
            ref = sum(dm.per_lookup(tab, snr + 10 * math.log10(x)) for x in gs) / len(gs)
            self.assertAlmostEqual(dm.per_lookup(ft, snr) / ref, 1.0, delta=0.05, msg="rayleigh snr=%s" % snr)
        self.assertEqual(dm.fading_per_table(1, False, 1456, "none", 0.0), tab)
        self.assertTrue(all(b <= a + 1e-12 for a, b in zip(ft, ft[1:])))  # середній PER монотонно спадає з SNR

    def test_D9_residual_tail_is_not_underestimated_by_orders(self):
        # residual ~ p^5 (8/12): раніше недооцінювався на порядки при p ~ 1e-3; тепер масштаб p збігається з еталоном (див. тест вище)
        kdb = 10.0
        ft = dm.fading_per_table(1, False, 1456, "rician", kdb)
        p15 = dm.per_lookup(ft, 15.0)
        self.assertGreater(p15, 1.5e-3)
        self.assertLess(p15, 3.0e-3)


class TestDocs(unittest.TestCase):
    def test_validation_doc_lists_every_defect(self):
        if not os.path.exists(DOC):
            self.skipTest("docs/SIM-VALIDATION.md відсутній у цій копії")
        txt = open(DOC, encoding="utf-8").read()
        for d in ("D1", "D1b", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11", "D12"):
            self.assertIn(d, txt, "дефект %s не описано в docs/SIM-VALIDATION.md" % d)
        for tag in ("SRC", "INF", "SYNTH", "UNVERIFIED"):
            self.assertIn(tag, txt)
        self.assertIn("недоступно", txt)


if __name__ == "__main__":
    unittest.main(verbosity=1)
