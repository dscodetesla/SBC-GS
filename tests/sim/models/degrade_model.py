#!/usr/bin/env python3
"""Degradation physics for the stochastic scenario engine: nonlinear hardware, drifting/shocked environment, USB/power
state machines, bring-up failures, timing. Pure functions + small state machines; every number comes from a Theta
(a sampled parameter vector, see priors.py) so it is overridable without code changes. Stdlib only, deterministic.

A MODEL, not a proof: the shapes (Rapp AM-AM, first-order thermal RC, hazard that falls with margin, M/M/1/K blocking)
are textbook INF choices; the numbers are SYNTH/UNMEASURED priors (params.degrade.json). It ranks risks and designs tests;
it does not describe real RTL8812AU silicon (docs/SIM-SCENARIOS.md, honesty section).

Contour modelled: AIR = OpenIPC camera + RTL8812AU (TX of the video downlink, powered from the airframe BEC);
GS = Raspberry Pi 5 + RTL8812AU dongle (RX of the video, ELRS TX module of the TX12 next to it, host Ubuntu).

Layout: (a) nonlinear hardware, (b) stochastic processes, (c) USB/power, (d) bring-up and injection, (e) timing,
        then Plan/Session = the time-stepped link simulation used by scenario_engine.py.
Event schema "sbc-gs-degrade-events/1": same shapes as sbc-gs-usbfault/1 for usb_drop/usb_return/undervoltage/voltage_ok/throttled
(power_model.py), plus thermal_*, burst_*, shock, stall, creep_reset, usb_trip, usb_latched, link_down, link_up, bringup.
"""
import functools
import math

import common
import power_model
import rf_model
import latency_budget
from priors import Rng, Theta


def dbm_to_mw(x):
    return 10.0 ** (x / 10.0)


def mw_to_dbm(x):
    return 10.0 * math.log10(x) if x > 0 else -300.0


# =============================================================== (a) nonlinear hardware
def pa_psat_mw(p1db_out_dbm, p):
    """Saturation power (mW) of a Rapp AM-AM curve whose output-referred 1 dB compression point is p1db_out_dbm."""
    plin_1db = dbm_to_mw(p1db_out_dbm + 1.0)  # linear (uncompressed) output that compresses to p1db
    x = 10.0 ** (0.1 * p) - 1.0
    return plin_1db / x ** (1.0 / p)


def pa_output_dbm(plin_dbm, p1db_out_dbm, p):
    """Rapp: Pout = Plin / (1 + (Plin/Psat)^p)^(1/p). Monotone increasing, gain non-increasing, -> Psat."""
    plin = dbm_to_mw(plin_dbm)
    ps = pa_psat_mw(p1db_out_dbm, p)
    return mw_to_dbm(plin / (1.0 + (plin / ps) ** p) ** (1.0 / p))


def evm_db(comp_db, floor_db, comp_coeff, temp_shift_db=0.0):
    """EVM (dB, negative = good): linear floor (+ thermal shift) in power plus the compression amplitude error."""
    amp_err = 1.0 - 10.0 ** (-max(comp_db, 0.0) / 20.0)
    e2 = 10.0 ** ((floor_db + temp_shift_db) / 10.0) + (comp_coeff * amp_err) ** 2
    return 10.0 * math.log10(e2)


def combine_snr_evm_db(snr_db, evm):
    """1/SNR_eff = 1/SNR + EVM^2 : the SNR can never exceed -EVM (the ceiling)."""
    return -10.0 * math.log10(10.0 ** (-snr_db / 10.0) + 10.0 ** (evm / 10.0))


def tx_sag_db(v, knee_v, k1, k2):
    """Power sag (dB, >= 0) when the PA supply v falls below the knee: linear + quadratic term."""
    dv = max(0.0, knee_v - v)
    return k1 * dv + k2 * dv * dv


def agc_penalty_db(rx_dbm, knee_dbm, slope, cap_db):
    """SNR loss from ADC/AGC saturation when the received level exceeds the knee (too close to the transmitter)."""
    return min(cap_db, max(0.0, rx_dbm - knee_dbm) * slope)


def noise_rise_db(noise_dbm, interferers_dbm):
    """Receiver desense: rise of the noise floor when interferers (dBm at the input) add to it."""
    n = dbm_to_mw(noise_dbm)
    return 10.0 * math.log10(1.0 + sum(dbm_to_mw(i) for i in interferers_dbm) / n)


def thermal_time_to(t0, t_amb, rth, p_w, tau, t_target):
    """Seconds for a first-order RC to go from t0 to t_target (inf when the steady state stays below it)."""
    tss = t_amb + rth * p_w
    if t_target >= tss or t0 >= t_target:
        return 0.0 if t0 >= t_target else math.inf
    return -tau * math.log((tss - t_target) / (tss - t0))


def air_tx_power_w(v_bec, i_tx_a, p_rf_w, p_rf_ref_w, diss_frac):
    """AIR radio in TX: (P_dc, heat) in W for a radiated power p_rf_w, consistent with energy conservation (D1, D1b).

    The measured TX current i_tx_a (UNMEASURED prior) is taken at the PA's rated output p_rf_ref_w (INF: the output at P1dB; the
    reference point itself is UNMEASURED). The PA drain efficiency eta = 1 - diss_frac (INF range 5..30 %, value UNMEASURED) is the
    INCREMENTAL efficiency: P_dc(P_rf) = P_idle + P_rf/eta_eff with P_idle = P_dc_ref - P_rf_ref/eta_eff >= 0. When the sampled
    current is too small for eta (P_rf_ref/eta > P_dc_ref), eta is raised to the implied P_rf_ref/P_dc_ref (the measured current wins,
    idle = 0, P_dc is proportional to P_rf). Hence P_dc >= P_rf for every input, heat = P_dc - P_rf >= 0 exactly, and BOTH P_dc and
    the heat grow with the radiated power and with the current. Returns (p_dc_w, heat_w, eta_eff)."""
    p_dc_ref = v_bec * i_tx_a
    eta = min(1.0, max(1.0 - diss_frac, 1e-3, p_rf_ref_w / p_dc_ref if p_dc_ref > 0 else 1.0))
    p_idle = max(0.0, p_dc_ref - p_rf_ref_w / eta)
    p_dc = p_idle + p_rf_w / eta
    return p_dc, p_dc - p_rf_w, eta


def air_rx_heat_w(v_bec, i_rx_a):
    """AIR radio not transmitting (queue empty / TX shut down): everything drawn from the BEC is heat (no RF out)."""
    return v_bec * i_rx_a


class Thermal:
    """Junction temperature of the adapter: Tj' = (Tamb + Rth*P - Tj)/tau, derating above a start temperature,
    shutdown at a threshold with hysteresis (the TX stops until Tj falls by `hyst`)."""

    def __init__(self, t0, rth, tau, derate_start, derate_db_per_c, shutdown, hyst):
        self.tj, self.rth, self.tau = t0, rth, tau
        self.ds, self.dk, self.sd, self.hy = derate_start, derate_db_per_c, shutdown, hyst
        self.shut = False

    def step(self, dt, p_w, t_amb):
        tss = t_amb + self.rth * p_w
        self.tj = tss + (self.tj - tss) * math.exp(-dt / self.tau)
        if not self.shut and self.tj >= self.sd:
            self.shut = True
        elif self.shut and self.tj <= self.sd - self.hy:
            self.shut = False
        return self.tj, max(0.0, self.tj - self.ds) * self.dk, self.shut


def pol_loss_db(psi_deg, cap_db):
    c = math.cos(math.radians(min(psi_deg, 89.9)))
    return min(cap_db, -20.0 * math.log10(max(c, 1e-6)))


class AntennaLoss:
    """Random antenna loss = pattern null (prob, exponential depth) + polarisation mismatch (uniform angle) + body shadow
    (prob, normal depth). The state persists for ~persist_s (slow attitude changes), then is redrawn."""

    def __init__(self, th, rng):
        g = th.get
        self.p_null, self.null_mean = g("hw.ant_null_prob"), g("hw.ant_null_mean_db")
        self.pol_max, self.pol_cap = g("hw.ant_pol_max_deg"), g("hw.ant_pol_cap_db")
        self.p_body, self.body_mean, self.body_sig = g("hw.ant_body_prob"), g("hw.ant_body_mean_db"), g("hw.ant_body_sigma_db")
        self.persist = g("hw.ant_persist_s")
        self.rng = rng
        self.val = self.draw()

    def draw(self):
        r = self.rng
        null = r.expo(self.null_mean) if r.u() < self.p_null else 0.0
        pol = pol_loss_db(r.u() * self.pol_max, self.pol_cap)
        body = max(0.0, self.body_mean + self.body_sig * r.z()) if r.u() < self.p_body else 0.0
        return null + pol + body

    def step(self, dt):
        if self.rng.u() < -math.expm1(-dt / self.persist):
            self.val = self.draw()
        return self.val


# =============================================================== (b) stochastic degradation processes
class NoiseFloor:
    """Excess noise (dB): OU slow drift + Poisson shocks (lognormal size, exponential decay) + ageing offset."""

    def __init__(self, th, rng):
        g = th.get
        self.sig, self.tau = g("proc.nf_ou_sigma_db"), g("proc.nf_ou_tau_s")
        self.rate, self.mag, self.stau = g("proc.shock_rate_per_h") / 3600.0, g("proc.shock_mag_db"), g("proc.shock_tau_s")
        self.rng, self.x, self.shock, self.shock_n = rng, 0.0, 0.0, 0

    def step(self, dt):
        a = math.exp(-dt / self.tau)
        self.x = self.x * a + self.sig * math.sqrt(1.0 - a * a) * self.rng.z()
        self.shock *= math.exp(-dt / self.stau)
        new = False
        if self.rng.poisson_step(self.rate, dt):
            self.shock += self.rng.lognorm(self.mag, 0.5)
            self.shock_n += 1
            new = True
        return self.x + self.shock, new


class BurstChain:
    """Two-state (Gilbert-style) episode process at slice level: quiet <-> interference burst (channel clash, jammer)."""

    def __init__(self, rate_per_h, mean_s, rng):
        self.on_rate, self.mean, self.rng, self.on = rate_per_h / 3600.0, mean_s, rng, False

    def step(self, dt):
        if self.on:
            if self.rng.u() < -math.expm1(-dt / self.mean):
                self.on = False
        elif self.rng.poisson_step(self.on_rate, dt):
            self.on = True
        return self.on


def _i0_scaled(z):
    """exp(-z)*I0(z) for z >= 0 (Abramowitz & Stegun 9.8.1/9.8.2, relative error < 2e-7): no overflow for large z."""
    if z < 3.75:
        t = (z / 3.75) ** 2
        return math.exp(-z) * (1.0 + t * (3.5156229 + t * (3.0899424 + t * (1.2067492 + t * (0.2659732 + t * (0.0360768 + t * 0.0045813))))))
    t = 3.75 / z
    return (0.39894228 + t * (0.01328592 + t * (0.00225319 + t * (-0.00157565 + t * (0.00916281 + t * (-0.02057706 + t * (0.02635537
            + t * (-0.01647633 + t * 0.00392377)))))))) / math.sqrt(z)


def _fade_pdf_x(x, k):
    """Unit-mean power pdf of Rician fading with linear K factor k (k = 0 -> Rayleigh, exp(-x)): the noncentral chi-square pdf
    (K+1) exp(-K-(K+1)x) I0(2 sqrt(K(K+1)x)), written with the scaled Bessel function so that it never overflows."""
    z = 2.0 * math.sqrt(k * (k + 1.0) * x)
    return (k + 1.0) * math.exp(-((math.sqrt((k + 1.0) * x) - math.sqrt(k)) ** 2)) * _i0_scaled(z)


# the fading gain G (dB) lives on the same 0.25 dB grid as the PER table, so the average over fading is a discrete convolution
_FADE_JLO, _FADE_JHI, _FADE_SUB = -240, 40, 16  # G from -60 dB (deep fades matter for the tail) to +10 dB


@functools.lru_cache(maxsize=64)
def fading_weights(model, k_db):
    """Probability mass of the unit-mean power gain on the grid G_j = j*_STEP dB, j in [_FADE_JLO, _FADE_JHI] (sums to 1).

    Exact pdf integration (midpoint rule with _FADE_SUB sub-bins per bin), not a draw count: the tail of the PER average is
    resolved down to ~1e-6 probability, which the old 32 equiprobable quantiles (tail below 1/32 lost) and rf_model's 400 random
    draws (noise x2) did not do (D9). Mass beyond the grid ends is added to the end nodes. 'none' -> all mass at G = 0 dB."""
    n = _FADE_JHI - _FADE_JLO + 1
    if model == "none":
        w = [0.0] * n
        w[-_FADE_JLO] = 1.0
        return tuple(w)
    k = 0.0 if model == "rayleigh" else 10.0 ** (k_db / 10.0)
    w = []
    sub = _STEP / _FADE_SUB
    ln10_10 = math.log(10.0) / 10.0
    for j in range(_FADE_JLO, _FADE_JHI + 1):
        m = 0.0
        for s in range(_FADE_SUB):
            g = j * _STEP - 0.5 * _STEP + (s + 0.5) * sub
            x = 10.0 ** (g / 10.0)
            m += _fade_pdf_x(x, k) * x * ln10_10 * sub
        w.append(m)
    tot = sum(w)
    lost = max(0.0, 1.0 - tot)  # tails beyond the grid: lumped at the ends (deep fade: PER ~ 1; high gain: PER ~ lowest)
    w[0] += 0.5 * lost
    w[-1] += 0.5 * lost
    tot = sum(w)
    return tuple(x / tot for x in w)


@functools.lru_cache(maxsize=64)
def fading_per_table(mcs, vht, nbytes, model, k_db):
    """log10 of the fading-averaged PER on the PER-table grid: E[PER(snr + G)] = sum_j w_j PER(snr + j*step). Interpolated by
    per_lookup like the plain table; replaces the per-call average over 32 quantiles (one lookup per call instead of 32)."""
    base = per_table(mcs, vht, nbytes)
    if model == "none":
        return base
    w = fading_weights(model, k_db)
    lin = [10.0 ** v for v in base]
    ext = [lin[0]] * (-_FADE_JLO) + lin + [lin[-1]] * _FADE_JHI  # PER outside the table is clamped to the end values
    n = len(lin)
    out = []
    for i in range(n):
        seg = ext[i:i + len(w)]  # ext index i + (j - JLO) <-> table index i + j
        out.append(math.log10(max(sum(a * b for a, b in zip(w, seg)), 1e-12)))
    return tuple(out)


def ageing_offsets(th):
    """(noise-figure rise dB, PA output loss dB) from the unit's operating hours."""
    h = th.get("proc.age_hours")
    return th.get("proc.age_nf_db_per_kh") * h / 1000.0, th.get("proc.age_pa_db_per_kh") * h / 1000.0


# =============================================================== PER lookup (fast, monotone)
_LO, _HI, _STEP = -10.0, 60.0, 0.25


@functools.lru_cache(maxsize=64)
def per_table(mcs, vht, nbytes):
    n = int((_HI - _LO) / _STEP) + 1
    return tuple(math.log10(max(rf_model.per_ideal(mcs, _LO + i * _STEP, nbytes, vht), 1e-12)) for i in range(n))


def per_lookup(tab, snr_db):
    if snr_db <= _LO:
        return 10.0 ** tab[0]
    if snr_db >= _HI:
        return 10.0 ** tab[-1]
    f = (snr_db - _LO) / _STEP
    i = int(f)
    return 10.0 ** (tab[i] + (tab[i + 1] - tab[i]) * (f - i))


def per_threshold_db(tab, target=0.1):
    lt = math.log10(target)
    for i, v in enumerate(tab):
        if v <= lt:
            if i == 0:
                return _LO
            return _LO + _STEP * (i - 1 + (tab[i - 1] - lt) / (tab[i - 1] - v))
    return _HI


@functools.lru_cache(maxsize=8192)
def _residual_q(lp_q, burst_q, k, n, ge):
    p = 10.0 ** (lp_q / 64.0)
    return rf_model.residual_ge(p, burst_q, k, n) if ge else rf_model.residual_iid(p, k, n)


def fec_residual(p, burst, k, n, ge):
    """rf_model residual loss with log-quantised p (1/64 decade) so the Gilbert DP is cached; monotone in p."""
    if p <= 1e-12:
        return 0.0
    if p >= 1.0:
        return 1.0
    return _residual_q(round(math.log10(p) * 64), round(burst * 4) / 4.0, k, n, ge)


# =============================================================== (c) USB / power
def usb_drop_rate_per_s(v_margin_v, i_margin_a, base_per_h, v_scale, i_scale):
    """Spontaneous USB drop hazard: base * exp(-v_margin/v_scale) * exp(-i_margin/i_scale); falls as the margins grow."""
    x = -max(-30.0, v_margin_v) / v_scale - max(-30.0, i_margin_a) / i_scale
    return min(1.0, base_per_h / 3600.0 * math.exp(min(x, 30.0)))


class Pi5UsbLimiter:
    """Pi 5 USB current limit as a state machine: OK -> TRIPPED (port off for off_s) -> OK (re-enumerates), LATCHED after
    `latch_n` consecutive trips (until power cycle). Limit = 600 mA or 1.6 A (power_model.usb_budget_a)."""

    def __init__(self, limit_a, tol, latch_n):
        self.limit, self.tol, self.latch_n = limit_a, tol, latch_n
        self.state, self.until, self.consec = "OK", 0.0, 0

    def step(self, t, i_a, off_s):
        """Returns an event name ('usb_trip', 'usb_return', 'usb_latched') or None."""
        if self.state == "LATCHED":
            return None
        if self.state == "TRIPPED":
            if t >= self.until:
                self.state = "OK"
                return "usb_return"
            return None
        if i_a > self.limit * (1.0 + self.tol):
            self.consec += 1
            if self.consec >= self.latch_n:
                self.state = "LATCHED"
                return "usb_latched"
            self.state, self.until = "TRIPPED", t + off_s
            return "usb_trip"
        self.consec = 0
        return None


# =============================================================== (d) bring-up and injection
BRINGUP_STAGES = (("usb_probe", "usb_probe_fail_p", "usb_probe_timeout_s", "usb_probe_ok_s"),
                  ("fw_load", "fw_fail_p", "fw_timeout_s", "fw_ok_s"),
                  ("monitor_mode", "monitor_fail_p", "monitor_timeout_s", "monitor_ok_s"),
                  ("injection_test", "inj_start_fail_p", "inj_start_timeout_s", "inj_start_ok_s"))


def bringup_success_prob(th):
    """Closed form: product over stages of 1 - p^attempts."""
    a = int(th.get("bringup.max_attempts"))
    pr = 1.0
    for _name, pk, _tk, _ok in BRINGUP_STAGES:
        pr *= 1.0 - th.get("bringup." + pk) ** a
    return pr


def bringup(th, rng):
    """One bring-up run: per stage up to max_attempts tries (Bernoulli), timeout + exponential backoff between tries.
    Returns {"ok", "t_s", "failed_stage", "attempts": {stage: n}}."""
    a = int(th.get("bringup.max_attempts"))
    base, fac = th.get("bringup.backoff_base_s"), th.get("bringup.backoff_factor")
    t, att = 0.0, {}
    for name, pk, tk, okk in BRINGUP_STAGES:
        p, t_to, t_ok = th.get("bringup." + pk), th.get("bringup." + tk), th.get("bringup." + okk)
        for i in range(1, a + 1):
            att[name] = i
            if rng.u() >= p:
                t += t_ok * rng.lognorm(1.0, 0.2)
                break
            t += t_to * rng.lognorm(1.0, 0.3) + base * fac ** (i - 1)
        else:
            return {"ok": False, "t_s": t, "failed_stage": name, "attempts": att}
    return {"ok": True, "t_s": t, "failed_stage": None, "attempts": att}


def injection_block_prob(rho, k):
    """M/M/1/K blocking probability (txq length K, utilisation rho): the nonlinearity of overload."""
    if rho <= 0:
        return 0.0
    if abs(rho - 1.0) < 1e-9:
        return 1.0 / (k + 1)
    if rho < 1.0:
        return (1.0 - rho) * rho ** k / (1.0 - rho ** (k + 1))
    r = 1.0 / rho  # same value without overflow for rho >> 1
    return (1.0 - r) / (1.0 - r ** (k + 1))


def iframe_overflow_frac(pkts_i, queue_pkts, t_pkt_ms, frame_ms):
    """Fraction of an I-frame's packets (incl. parity) dropped at the injection queue: arrivals in one frame
    period against queue + service during that period. Majestic bitrate overshoot -> IDR loss -> long freeze."""
    cap = queue_pkts + frame_ms / t_pkt_ms
    return max(0.0, pkts_i - cap) / pkts_i


def freeze_fraction(res, res_i, ppf, ppf_i, frames_per_gop):
    """Expected frozen time share: a lost I-frame freezes the whole GOP, a lost P-frame the rest of it (half on average)."""
    p_i = 1.0 - (1.0 - res_i) ** ppf_i
    p_p = 1.0 - (1.0 - res) ** ppf
    p_any_p = 1.0 - (1.0 - p_p) ** max(frames_per_gop - 1.0, 0.0)
    return p_i + (1.0 - p_i) * 0.5 * p_any_p


# =============================================================== (e) timing
def clock_events(th, rng, duration_s):
    """Clock offset events for virt tests: constant ppm drift + Poisson NTP steps. [{t_s, kind, ...}]"""
    ppm = th.get("timing.clock_ppm")
    out = [{"t_s": 0.0, "kind": "clock_drift", "ppm": round(ppm, 3)}]
    t, rate = 0.0, th.get("timing.ntp_step_rate_per_h") / 3600.0
    while rate > 0:
        t += rng.expo(1.0 / rate)
        if t >= duration_s:
            break
        out.append({"t_s": round(t, 3), "kind": "ntp_step", "step_ms": round(rng.lognorm(th.get("timing.ntp_step_ms"), 0.8) * (1 if rng.u() < 0.5 else -1), 3)})
    return out


def sched_jitter_ms(th, rng):
    j = rng.lognorm(th.get("timing.sched_jitter_median_ms"), th.get("timing.sched_jitter_sigma"))
    spike, ex = rng.u() < th.get("timing.sched_spike_prob"), rng.expo(th.get("timing.sched_spike_mean_ms"))  # fixed draw count
    return j + (ex if spike else 0.0)


# =============================================================== link simulation
DEFAULT_CFG = {"distance_m": 600.0, "duration_s": 600.0, "dt_s": 5.0, "board": "pi5", "psu_a": 5.0, "usb_max_current": False,
               "codec": "h265", "gs_with": ["fc", "fan"], "soak_s": 120.0,
               "spec": {"residual": 0.01, "g2g_ms": 250.0, "freeze": 1.0}}  # freeze share is reported; as a spec it is off by default (1.0)
FAIL_MODES = ("bringup_fail", "thermal_derate", "thermal_shutdown", "usb_dropout", "usb_trip", "usb_latched", "undervoltage",
              "soc_throttle", "agc_saturation", "desense", "burst_outage", "injection_overload", "idr_freeze",
              "latency_creep", "fec_exhaust")


def make_cfg(over=None):
    cfg = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in DEFAULT_CFG.items()}
    for k, v in (over or {}).items():
        if k == "spec":
            cfg["spec"].update(v)
        else:
            cfg[k] = v
    return cfg


def prepare(th, cfg, rng):
    """Static, per-draw derived quantities (the slice loop reads them)."""
    th = Theta(dict(th.vals), th._provs)  # private copy: we rescale the bitrate
    g = th.get
    ovs = g("vid.bitrate_overshoot")
    th.vals["video.bitrate_kbps"] = g("video.bitrate_kbps") * ovs
    mcs, vht = int(g("rf.mcs_index")), bool(g("rf.vht"))
    k, n = int(g("rf.fec_k")), int(g("rf.fec_n"))
    nbytes = rf_model.frame_bytes(th)
    tab = per_table(mcs, vht, nbytes)
    ftab = fading_per_table(mcs, vht, nbytes, g("rf.fading_model"), round(g("rf.rician_k_db") * 2) / 2.0)
    nf_age, pa_age = ageing_offsets(th)
    fps = int(g("video.fps"))
    t_pkt = (rf_model.frame_airtime_us(th, mcs) + g("rf.mac_access_us")) / 1000.0
    util = rf_model.utilisation(th, mcs, k, n)
    pps = g("video.bitrate_kbps") * 1000 / 8 / g("rf.payload_bytes") * n / k
    rho = max(util, pps / g("inj.rate_cap_pps"))
    block = injection_block_prob(rho, int(g("inj.queue_pkts")))
    p_inj = 1.0 - (1.0 - g("inj.ebusy_prob")) * (1.0 - block)
    ppf = rf_model.packets_per_frame(th)
    ppf_i = max(1, math.ceil(g("video.bitrate_kbps") * 1000 / 8 / fps * g("video.iframe_ratio") / g("rf.payload_bytes")))
    frame_ms = 1000.0 / fps
    ovf = iframe_overflow_frac(ppf_i * n / k, g("inj.queue_pkts"), t_pkt, frame_ms)
    # GS electrical (Pi): steady RX state and the TX burst peak, from power_model
    board, psu = cfg["board"], cfg["psu_a"]
    with_ = tuple(cfg["gs_with"])
    b_rx = power_model.budget(th, board, psu, 1, "rx", with_, "active", False, cfg["usb_max_current"])
    b_pk = power_model.budget(th, board, psu, 1, "tx", with_, "active", True, cfg["usb_max_current"])
    psu_off = g("usb.psu_v_sigma") * rng.child(1).z()
    v_pk = b_pk["v_board"] + psu_off
    i_tx_pk = power_model.device_a(th, "rtl8812", "tx", True)
    v_dongle_pk = v_pk - i_tx_pk * g("usb.cable_r_ohm")
    srcs = []  # (dBm at the receiver input, duty): ELRS 900 MHz module (harmonic/intermod) and the Multi 2.4 GHz module
    if g("hw.elrs900_present"):
        srcs.append((g("hw.elrs900_tx_dbm") - g("hw.elrs900_coupling_db"), g("hw.elrs900_duty")))
    if g("hw.multi24_present"):
        srcs.append((g("hw.multi24_tx_dbm") - g("hw.multi24_coupling_db"), g("hw.multi24_duty")))
    elrs = bool(srcs)
    # approximation: one on-state with the power sum, duty = P(at least one source on)
    e_dbm = mw_to_dbm(sum(dbm_to_mw(d) * du for d, du in srcs) / max(sum(du for _d, du in srcs), 1e-12)) if srcs else -200.0
    e_duty = 1.0 - math.prod(1.0 - du for _d, du in srcs) if srcs else 0.0
    b_tx = power_model.budget(th, board, psu, 1, "tx", with_, "active", False, cfg["usb_max_current"])
    plan = {
        "th": th, "mcs": mcs, "k": k, "n": n, "tab": tab, "ftab": ftab, "thr10": per_threshold_db(tab), "ge": g("rf.loss_model") == "ge",
        "burst": g("rf.ge_mean_burst_frames"), "floor": g("rf.floor_per"), "gtx": g("rf.tx_antenna_gain_dbi"),
        "grx": g("rf.rx_antenna_gain_dbi"), "misc": g("rf.misc_loss_db"), "pa_p": g("hw.pa_rapp_p"),
        "p1db0": g("hw.pa_p1db_out_dbm") - pa_age, "evm_floor": g("hw.evm_floor_db"), "evm_k": g("hw.evm_comp_coeff"),
        "agc_knee": g("hw.rx_agc_knee_dbm"), "agc_slope": g("hw.rx_agc_slope"), "agc_cap": g("hw.rx_agc_cap_db"),
        "noise0": rf_model.noise_dbm(th) + nf_age, "usb3": rng.child(2).u() < g("hw.usb3_present_prob"), "usb3_dbm": g("hw.usb3_noise_dbm"),
        "elrs": elrs, "elrs_i": e_dbm, "elrs_duty": e_duty,
        "clash_inr": g("ext.clash_inr_db"), "p_inj": p_inj, "block": block, "rho": rho, "util": util, "ovf": ovf, "ppf": ppf,
        "ppf_i": ppf_i, "t_pkt": t_pkt, "fps": fps, "frames_per_gop": fps * g("vid.gop_s"), "ovs": ovs,
        "b_rx": b_rx, "b_pk": b_pk, "v_pk": v_pk, "v_dongle_pk": v_dongle_pk, "i_usb_pk": b_pk["usb_a"], "i_usb_tx": b_tx["usb_a"], "i_usb_rx": b_rx["usb_a"],
        "limit": b_pk["usb_budget_a"], "fec_full_ms": latency_budget.radio_terms(th, mcs, k, n, fps, g("video.bitrate_kbps"), True)[1][2],
        "amb_air": g("ext.ambient_c") + g("hw.air_ambient_rise_c") + g("ext.solar_rise_c"),
        "amb_gs": g("ext.ambient_c") + 0.5 * g("ext.solar_rise_c"), "nf_age": nf_age,
    }
    codec = cfg["codec"]
    plan["lat"] = latency_budget.budget(th, board, codec, fps, int(g("video.width")), int(g("video.height")),
                                        g("video.bitrate_kbps"), mcs, k, n, False)
    plan["lat_total"] = plan["lat"]["total"][1]
    plan["lat_decode"] = plan["lat"]["terms"]["decode"][1]
    plan["t_disp"] = 1000.0 / g("latency.display_hz")
    return plan


def link_eval(plan, d, st):
    """SNR chain at distance d for a frozen slice state -> (PER, margin dB, rx dBm, agc penalty dB, desense dB, compression dB)."""
    th = plan["th"]
    pout = pa_output_dbm(st["plin"], st["p1db"], plan["pa_p"])
    comp = st["plin"] - pout
    evm = evm_db(comp, plan["evm_floor"], plan["evm_k"], st["evm_shift"])
    rx = pout + plan["gtx"] + plan["grx"] - rf_model.path_loss_db(th, d) - plan["misc"] - st["ant"] - st["shadow"]
    pen = agc_penalty_db(rx, plan["agc_knee"], plan["agc_slope"], plan["agc_cap"])
    n0 = plan["noise0"] + st["noise_extra"]
    cont = [plan["usb3_dbm"]] if plan["usb3"] else []
    n1b = n0 + (noise_rise_db(n0, cont) if cont else 0.0)  # without the clash episode (that one is "burst", not desense)
    if st["burst"]:
        cont = cont + [n0 + plan["clash_inr"]]
    n1 = n0 + (noise_rise_db(n0, cont) if cont else 0.0)
    snr_a = rx - n1 - pen
    eff_a = rf_model.eff_snr_db(th, plan["mcs"], combine_snr_evm_db(snr_a, evm))
    per = per_lookup(plan["ftab"], eff_a)
    desense = n1b - n0
    if plan["elrs_duty"] > 0.0:
        n2 = n1 + noise_rise_db(n1, [plan["elrs_i"]])
        eff_b = rf_model.eff_snr_db(th, plan["mcs"], combine_snr_evm_db(rx - n2 - pen, evm))
        per = (1.0 - plan["elrs_duty"]) * per + plan["elrs_duty"] * per_lookup(plan["ftab"], eff_b)
        desense += plan["elrs_duty"] * noise_rise_db(n1b, [plan["elrs_i"]])
    per = 1.0 - (1.0 - per) * (1.0 - plan["floor"]) * (1.0 - plan["p_inj"])
    return {"per": per, "margin": eff_a - plan["thr10"], "rx": rx, "pen": pen, "desense": desense, "comp": comp, "evm": evm}


def residual_of(plan, per):
    return fec_residual(per, plan["burst"], plan["k"], plan["n"], plan["ge"])


def range_at_target(plan, st, target):
    """Largest distance with residual <= target for the frozen typical state; the search starts beyond the AGC-saturated
    near zone. Returns (metres, capped)."""
    th = plan["th"]
    hi = th.get("rf.max_search_m")
    lo = th.get("rf.ref_distance_m")
    while lo < hi and link_eval(plan, lo, st)["pen"] > 0.5:  # skip the saturated near zone (geometric steps)
        lo *= 1.5
    if lo >= hi:
        return 0.0, False
    if residual_of(plan, link_eval(plan, hi, st)["per"]) <= target:
        return hi, True
    if residual_of(plan, link_eval(plan, lo, st)["per"]) > target:
        return 0.0, False
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        if residual_of(plan, link_eval(plan, mid, st)["per"]) <= target:
            lo = mid
        else:
            hi = mid
    return lo, False


def pctl(xs, q):
    """Linear-interpolated percentile (q in 0..100) of a list."""
    s = sorted(xs)
    if not s:
        return float("nan")
    f = (len(s) - 1) * q / 100.0
    i = int(f)
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (f - i)


def run_session(th, cfg, rng, events=None):
    """One session: bring-up, then T seconds in slices of dt. Returns the per-draw output dict (see scenario_engine.OUTPUTS).

    A link that never works (bring-up failed, or no slice with the link up) has NO link margin: margin_db and margin_p5_db are None
    and the separate flag out["dead"] is True (D4: the old stub margin = -60 dB lay inside the physical range, down to -70 dB at
    the corners of the prior hypercube). Consumers must test `dead` / None, never compare with a magic value."""
    plan = prepare(th, cfg, rng)
    th = plan["th"]
    g = th.get
    dt, T = cfg["dt_s"], cfg["duration_s"]
    spec = cfg["spec"]
    ev = events if events is not None else None

    def log(t, kind, **kw):
        if ev is not None:
            d = {"t_s": round(t, 3), "kind": kind}
            d.update(kw)
            ev.append(d)

    flags = dict.fromkeys(FAIL_MODES, False)
    out = {"flags": flags, "bringup_s": 0.0, "bringup_stage": None}
    r_bu, r_nf, r_ch, r_ant, r_sh, r_air, r_usb, r_lat, r_jit = (rng.child(10 + i) for i in range(9))
    bu = bringup(th, r_bu)
    out["bringup_s"], out["bringup_stage"] = bu["t_s"], bu["failed_stage"]
    log(0.0, "bringup", ok=bu["ok"], t_s_total=round(bu["t_s"], 3), failed_stage=bu["failed_stage"], attempts=bu["attempts"])
    if not bu["ok"]:
        flags["bringup_fail"] = True
        out.update({"residual": 1.0, "margin_db": None, "margin_p5_db": None, "dead": True, "range_m": 0.0, "g2g_ms": plan["lat_total"],
                    "g2g_mean_ms": plan["lat_total"], "availability": 0.0, "ttff_s": 0.0, "ttff_censored": False,
                    "freeze": 1.0, "throttled": 0, "down_s": T})
        return out
    # AIR adapter thermal state after the ground soak (TX on)
    i_tx, i_rx = g("power.devices.rtl8812_tx_a"), g("power.devices.rtl8812_rx_a")
    v_bec, dfrac, board_w = g("hw.air_bec_v"), g("hw.air_diss_frac"), g("hw.air_board_heat_w")
    p_rf_ref_w = dbm_to_mw(g("hw.pa_p1db_out_dbm")) / 1000.0
    # radiated power of the first slice: commanded power through the (aged, unsagged, underated) PA; later slices use the live state
    p_rf_w = dbm_to_mw(pa_output_dbm(g("rf.tx_power_dbm"), plan["p1db0"], plan["pa_p"])) / 1000.0
    p_dc_tx_w, heat_tx_w, _eta = air_tx_power_w(v_bec, i_tx, p_rf_w, p_rf_ref_w, dfrac)
    out["air_energy"] = {"dc_w": p_dc_tx_w, "rf_w": p_rf_w, "heat_w": heat_tx_w, "board_w": board_w}
    p_tx_w = heat_tx_w + board_w
    th_air = Thermal(plan["amb_air"], g("hw.air_rth_c_per_w"), g("hw.air_tau_s"), g("hw.thermal_derate_start_c"),
                     g("hw.thermal_derate_db_per_c"), g("hw.thermal_shutdown_c"), g("hw.thermal_hyst_c"))
    th_air.step(cfg["soak_s"], p_tx_w, plan["amb_air"])
    p_dc_duty = plan["util"] if plan["util"] < 1.0 else 1.0
    p_rx_w = air_rx_heat_w(v_bec, i_rx) + board_w
    nf, burst = NoiseFloor(th, r_nf), BurstChain(g("ext.clash_rate_per_h"), g("ext.clash_mean_s"), r_ch)
    ant = AntennaLoss(th, r_ant)
    lim = Pi5UsbLimiter(plan["limit"], g("usb.pi5_trip_tol"), int(g("usb.pi5_trip_latch_n")))
    shadow, a_sh = 0.0, math.exp(-dt / g("proc.shadow_tau_s"))
    sh_sig = g("proc.shadow_sigma_db")
    soc = plan["amb_gs"] + g("hw.soc_rise_c")
    thr_sticky, thr_now_prev = 0, 0
    down_until, latched, was_shut, derating = 0.0, False, False, False
    creep, backlog, resets = 0.0, 0.0, 0
    clock_rate = max(0.0, g("timing.clock_ppm")) * 1e-3  # ms of queue growth per second
    qmax = g("timing.queue_max_ms")
    p_tx_slice = -math.expm1(-g("usb.gs_tx_burst_hz") * dt)
    S = int(round(T / dt))
    res_l, mar_l, lat_l, ok_l, st_hist = [], [], [], [], []
    ttff, down_s, freeze_acc, up_n = None, 0.0, 0.0, 0
    n_fec_bad = 0
    for i in range(S):
        t = i * dt
        tc = t + 0.5 * dt
        # ---- slow environment
        nx, new_shock = nf.step(dt)
        if new_shock:
            log(tc, "shock", excess_db=round(nf.shock, 2))
        was_burst = burst.on
        on = burst.step(dt)
        if on != was_burst:
            log(tc, "burst_on" if on else "burst_off")
        shadow = shadow * a_sh + sh_sig * math.sqrt(1.0 - a_sh * a_sh) * r_sh.z()
        ant_db = ant.step(dt)
        # ---- AIR adapter: thermal, supply sag
        if th_air.shut:
            p_air_w = p_rx_w
        else:  # TX share of the airtime dissipates P_dc - P_rf at the live radiated power; the rest is the receive-state heat
            p_air_w = p_dc_duty * air_tx_power_w(v_bec, i_tx, p_rf_w, p_rf_ref_w, dfrac)[1] + (1.0 - p_dc_duty) * air_rx_heat_w(v_bec, i_rx) + board_w
        tj, derate, shut = th_air.step(dt, p_air_w, plan["amb_air"])
        if derate > 1.0 and not derating:
            derating = True
            flags["thermal_derate"] = True
            log(tc, "thermal_derate", tj_c=round(tj, 1), derate_db=round(derate, 2))
        if shut and not was_shut:
            flags["thermal_shutdown"] = True
            log(tc, "thermal_shutdown", tj_c=round(tj, 1))
        elif was_shut and not shut:
            log(tc, "thermal_resume", tj_c=round(tj, 1))
        was_shut = shut
        v_air = g("hw.air_bec_v") + g("hw.air_supply_ripple_v") * r_air.z() - i_tx * g("power.tx_peak_factor") * g("hw.air_supply_r_ohm")
        sag = tx_sag_db(v_air, g("hw.tx_sag_knee_v"), g("hw.tx_sag_k1_db_per_v"), g("hw.tx_sag_k2_db_per_v2"))
        st = {"plin": g("rf.tx_power_dbm") - derate, "p1db": plan["p1db0"] - sag - 0.5 * derate,
              "evm_shift": g("hw.evm_temp_db_per_c") * max(0.0, tj - 25.0), "ant": ant_db, "shadow": shadow,
              "noise_extra": nx, "burst": on}
        p_rf_w = dbm_to_mw(pa_output_dbm(st["plin"], st["p1db"], plan["pa_p"])) / 1000.0  # heat of the NEXT slice follows the live output
        # ---- GS side: Pi supply, USB limit, spontaneous drops, throttled word
        uv_now = plan["v_pk"] < g("power.undervolt_threshold_v")
        soft_now = soc >= g("hw.soc_soft_limit_c")
        now = (power_model.UV_NOW | power_model.THR_NOW if uv_now else 0) | ((1 << 3) if soft_now else 0)
        thr_sticky |= ((power_model.UV_EVER | power_model.THR_EVER) if uv_now else 0) | ((1 << 19) if soft_now else 0)
        if now != thr_now_prev:
            val = now | thr_sticky
            log(tc, "undervoltage" if (now & 1) and not (thr_now_prev & 1) else "voltage_ok" if (thr_now_prev & 1) and not (now & 1) else "throttled",
                v=round(plan["v_pk"], 3), value="0x%x" % val, bits=power_model.decode_throttled(val))
            if uv_now:
                flags["undervoltage"] = True
            thr_now_prev = now
        if soft_now:
            flags["soc_throttle"] = True
        usb_up = t >= down_until and not latched
        tx_now = r_usb.u() < p_tx_slice
        ev_lim = lim.step(tc, plan["i_usb_tx"] if tx_now else plan["i_usb_rx"], r_usb.lognorm(g("usb.pi5_trip_off_s"), 0.3))
        if ev_lim == "usb_trip":
            flags["usb_trip"] = True
            down_until = max(down_until, lim.until)
            log(tc, "usb_trip", limit_a=round(plan["limit"], 3), i_usb_a=round(plan["i_usb_tx"], 3))
            log(tc, "usb_drop", device="rtl8812", bus_port="1-1", reason="overcurrent", v=round(plan["v_dongle_pk"], 3), i_usb_a=round(plan["i_usb_pk"], 3))
        elif ev_lim == "usb_latched":
            flags["usb_latched"] = latched = True
            log(tc, "usb_latched")
            log(tc, "usb_drop", device="rtl8812", bus_port="1-1", reason="overcurrent", v=round(plan["v_dongle_pk"], 3), i_usb_a=round(plan["i_usb_pk"], 3))
        elif ev_lim == "usb_return":
            log(tc, "usb_return", device="rtl8812", bus_port="1-1")
            rb = bringup(th, r_bu)
            down_until = max(down_until, tc + rb["t_s"])
            if not rb["ok"]:
                flags["bringup_fail"] = latched = True
                log(tc, "bringup", ok=False, failed_stage=rb["failed_stage"])
        if usb_up and not latched and lim.state == "OK":
            haz = usb_drop_rate_per_s(plan["v_dongle_pk"] - g("power.usb_dropout_v"), plan["limit"] - plan["i_usb_pk"],
                                      g("usb.drop_rate_per_h"), g("usb.drop_v_scale"), g("usb.drop_i_scale_a"))
            if r_usb.u() < -math.expm1(-haz * dt):
                flags["usb_dropout"] = True
                re_s = r_usb.lognorm(g("power.usb_reenum_s"), g("usb.reenum_sigma"))
                log(tc, "usb_drop", device="rtl8812", bus_port="1-1", reason="undervoltage" if plan["v_dongle_pk"] < g("power.usb_dropout_v") else "spontaneous",
                    v=round(plan["v_dongle_pk"], 3), i_usb_a=round(plan["i_usb_pk"], 3))
                if r_usb.u() < g("usb.reenum_fail_prob"):
                    latched = flags["bringup_fail"] = True
                    log(tc, "usb_stuck")
                else:
                    rb = bringup(th, r_bu)
                    down_until = tc + re_s + rb["t_s"]
                    log(tc + re_s, "usb_return", device="rtl8812", bus_port="1-1")
                    if not rb["ok"]:
                        latched = flags["bringup_fail"] = True
        up = (t >= down_until) and not latched and not shut
        # ---- link
        e = link_eval(plan, cfg["distance_m"], st)
        if e["pen"] > 3.0:
            flags["agc_saturation"] = True
        if e["desense"] > 3.0:
            flags["desense"] = True
        if on and e["margin"] < 0:
            flags["burst_outage"] = True
        res = residual_of(plan, e["per"])
        res_i = max(res, plan["ovf"])
        frz = freeze_fraction(res, res_i, plan["ppf"], plan["ppf_i"], plan["frames_per_gop"])
        if plan["block"] > 0.01 or plan["ovf"] > 0.0:
            flags["injection_overload"] = True
        # ---- latency sample
        u_st, ex_st, u_v, u_fq, u_fw, u_ro, ex_ro = (r_lat.u(), r_lat.expo(g("timing.stall_backlog_ms")), r_lat.u(), r_lat.u(), r_lat.u(),
                                                    r_lat.u(), r_lat.expo(g("timing.reorder_delay_ms")))
        if u_st < -math.expm1(-g("timing.stall_rate_per_h") / 3600.0 * dt):
            backlog = min(qmax, backlog + ex_st)
            log(tc, "stall", backlog_ms=round(backlog, 1))
        backlog = max(0.0, backlog - g("timing.catchup_ms_per_s") * dt)
        creep += clock_rate * dt
        if creep + backlog >= qmax:
            creep, backlog, resets = 0.0, 0.0, resets + 1
            log(tc, "creep_reset")
        q_loss = 1.0 - (1.0 - e["per"]) ** plan["k"]
        lat = plan["lat_total"] + (u_v - g("latency.vsync_wait_frac")) * plan["t_disp"] + sched_jitter_ms(th, r_jit) + creep + backlog
        if u_fq < q_loss:
            lat += plan["fec_full_ms"] * u_fw
        if u_ro < min(1.0, plan["ppf"] * g("timing.reorder_prob")):
            lat += ex_ro
        if soft_now:
            lat += plan["lat_decode"] * (g("hw.throttle_decode_factor") - 1.0)
        # ---- bookkeeping
        if up:
            ok = res <= spec["residual"] and lat <= spec["g2g_ms"] and frz <= spec["freeze"]
            if lat > spec["g2g_ms"]:
                flags["latency_creep"] = True
            if plan["ovf"] > 0.0:
                flags["idr_freeze"] = True
            if res > spec["residual"]:
                n_fec_bad += 1
            up_n += 1
            res_l.append(res)
            mar_l.append(e["margin"])
            lat_l.append(lat)
            freeze_acc += frz
            st_hist.append(st)
        else:
            ok = False
            down_s += dt
            res_l.append(1.0)
        ok_l.append(ok)
        if not ok and ttff is None:
            ttff = t
    flags["idr_freeze"] = flags["idr_freeze"] or (up_n > 0 and freeze_acc / up_n > 0.30)
    flags["fec_exhaust"] = up_n > 0 and n_fec_bad / up_n >= 0.10
    avail = sum(ok_l) / len(ok_l)
    if st_hist:  # typical state = per-key median over up slices (numeric) -> range at the target residual
        typ = {k: pctl([s[k] for s in st_hist], 50) for k in ("plin", "p1db", "evm_shift", "ant", "shadow", "noise_extra")}
        typ["burst"] = sum(1 for s in st_hist if s["burst"]) * 2 > len(st_hist)
        rng_m, _cap = range_at_target(plan, typ, spec["residual"])
    else:
        rng_m = 0.0
    out.update({
        "residual": sum(res_l) / len(res_l), "margin_db": sum(mar_l) / len(mar_l) if mar_l else None,
        "margin_p5_db": pctl(mar_l, 5) if mar_l else None, "dead": not mar_l, "range_m": rng_m,
        "g2g_ms": pctl(lat_l, 95) if lat_l else plan["lat_total"], "g2g_mean_ms": sum(lat_l) / len(lat_l) if lat_l else plan["lat_total"],
        "availability": avail, "ttff_s": T if ttff is None else ttff, "ttff_censored": ttff is None,
        "freeze": freeze_acc / up_n if up_n else 1.0, "throttled": thr_sticky, "down_s": down_s, "creep_resets": resets,
        "tj_end_c": th_air.tj, "shocks": nf.shock_n,
    })
    return out
