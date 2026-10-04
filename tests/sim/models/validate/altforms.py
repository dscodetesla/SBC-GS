"""Альтернативні ФОРМИ моделей (структурна невизначеність). Підміняють функції моделей у пам'яті лише на час `with`,
файли моделей не чіпають. Усі альтернативи калібровані так, щоб збігатися з базовою формою в «якорі» (10 % PER;
базовий рівень небезпеки), тож різниця виходу рушія є наслідком САМОЇ ФОРМИ, а не зсуву параметрів. SYNTH/INF.
"""
import contextlib
import math

import vlib
from vlib import dm, rf_model

_S10 = {}


def _s10(orig, mcs, vht, nbytes):
    key = (mcs, vht, nbytes)
    if key not in _S10:
        lo, hi = -20.0, 80.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if orig(mcs, mid, nbytes, vht) > 0.1:
                lo = mid
            else:
                hi = mid
        _S10[key] = (lo + hi) / 2
    return _S10[key]


@contextlib.contextmanager
def per_logistic(k_per_db):
    """PER(SNR) = 1/(1+exp(k (SNR - s10) + ln 9)): логістична форма з тим самим 10 %-порогом, що й базова
    (спектр Вітербі/union-bound); k = крутизна логарифма шансів у 1/дБ (менше = пологіше)."""
    orig = rf_model.per_ideal
    _S10.clear()

    def alt(mcs, snr_db, nbytes, vht=False):
        s10 = _s10(orig, mcs, vht, nbytes)
        z = k_per_db * (snr_db - s10) + math.log(9.0)
        if z > 700:
            return 0.0
        if z < -700:
            return 1.0
        return 1.0 / (1.0 + math.exp(z))

    rf_model.per_ideal = alt
    vlib.clear_caches()
    try:
        yield
    finally:
        rf_model.per_ideal = orig
        vlib.clear_caches()
        _S10.clear()


def baseline_log_odds_slope(mcs=1, nbytes=1456):
    """Крутизна ln(PER/(1-PER)) по SNR у точці 10 % для базової форми (1/дБ)."""
    s = _s10(rf_model.per_ideal, mcs, False, nbytes)
    f = lambda x: math.log(rf_model.per_ideal(mcs, x, nbytes, False) / (1.0 - rf_model.per_ideal(mcs, x, nbytes, False)))  # noqa: E731
    return (f(s + 0.25) - f(s - 0.25)) / 0.5


@contextlib.contextmanager
def hazard_form(kind):
    """Форма небезпеки відвалу USB. Базова: base*exp(x), x = -v_margin/Vs - i_margin/Is (експонента, спадає із запасом, зростає без насичення).
    floored : base*exp(max(x,0))  - запас не зменшує небезпеку нижче базової (немає «бонусу за запас»);
    capped  : base*min(exp(x),100) - небезпека насичується на 100*base (обмежений ризик замість експоненційного росту);
    step    : base при x<=0, 0.02 1/с при x>0 - жорсткий поріг: втрата запасу = майже детермінований скид."""
    orig = dm.usb_drop_rate_per_s

    def alt(v_margin_v, i_margin_a, base_per_h, v_scale, i_scale):
        x = -max(-30.0, v_margin_v) / v_scale - max(-30.0, i_margin_a) / i_scale
        b = base_per_h / 3600.0
        if kind == "floored":
            return min(1.0, b * math.exp(min(max(x, 0.0), 30.0)))
        if kind == "capped":
            return min(1.0, b * min(math.exp(min(x, 30.0)), 100.0))
        return b if x <= 0.0 else 0.02

    dm.usb_drop_rate_per_s = alt
    try:
        yield
    finally:
        dm.usb_drop_rate_per_s = orig


@contextlib.contextmanager
def thermal_two_pole(fast_share=0.4, fast_tau_ratio=0.2, slow_tau_ratio=2.5):
    """Двополюсна теплова модель (перехід-корпус швидко, корпус-повітря повільно) з тим самим усталеним станом
    Rth*P, що й однополюсна; τ1 = fast_tau_ratio*τ, τ2 = slow_tau_ratio*τ (довільні, SYNTH)."""
    orig = dm.Thermal

    class Thermal2(orig):
        def __init__(self, t0, rth, tau, derate_start, derate_db_per_c, shutdown, hyst):
            super().__init__(t0, rth, tau, derate_start, derate_db_per_c, shutdown, hyst)
            self.r = (rth * fast_share, rth * (1 - fast_share))
            self.t = (tau * fast_tau_ratio, tau * slow_tau_ratio)
            self.x = None
            self._t0 = t0

        def step(self, dt, p_w, t_amb):
            if self.x is None:
                d = self._t0 - t_amb
                self.x = [d * fast_share, d * (1 - fast_share)]
            for i in (0, 1):
                tss = self.r[i] * p_w
                self.x[i] = tss + (self.x[i] - tss) * math.exp(-dt / self.t[i])
            self.tj = t_amb + self.x[0] + self.x[1]
            if not self.shut and self.tj >= self.sd:
                self.shut = True
            elif self.shut and self.tj <= self.sd - self.hy:
                self.shut = False
            return self.tj, max(0.0, self.tj - self.ds) * self.dk, self.shut

    dm.Thermal = Thermal2
    try:
        yield
    finally:
        dm.Thermal = orig


@contextlib.contextmanager
def queue_service(kind):
    """Служба черги ін'єкції: 'exp' = M/M/1/K (форма до D10, песимістична), 'det' = M/D/1/K (типова для рушія). Підміняє dm.injection_block."""
    orig = dm.injection_block

    def alt(rho, k, service="det"):
        return orig(rho, k, kind)

    dm.injection_block = alt
    try:
        yield
    finally:
        dm.injection_block = orig


FORMS = {
    "base": lambda: contextlib.nullcontext(),
    "per_logistic_k1.0": lambda: per_logistic(1.0),
    "per_logistic_k3.0": lambda: per_logistic(3.0),
    "hazard_floored": lambda: hazard_form("floored"),
    "hazard_capped": lambda: hazard_form("capped"),
    "hazard_step": lambda: hazard_form("step"),
    "thermal_two_pole": lambda: thermal_two_pole(),
    "queue_mm1k": lambda: queue_service("exp"),
}
