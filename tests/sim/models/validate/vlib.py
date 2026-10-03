"""Спільна бібліотека валідації моделей SBC-GS (stdlib). Нічого не змінює в моделях.

Моделі імпортуються з каталогу-батька (tests/sim/models) або з $VALIDATE_MODELS (так працює mutate.sh на копії).
Усі числа тут перевіряють САМІ МОДЕЛІ; жодне не є виміром контуру (SYNTH/UNMEASURED лишаються такими).
"""
import contextlib
import math
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.abspath(os.environ.get("VALIDATE_MODELS") or os.path.dirname(HERE))
if MODELS not in sys.path:
    sys.path.insert(0, MODELS)
for _k in ("MODEL_PARAMS", "MODEL_MEASURED", "MODEL_SET", "DEGRADE_PARAMS", "DEGRADE_MEASURED", "DEGRADE_SET"):
    os.environ.pop(_k, None)

import common  # noqa: E402
import degrade_model as dm  # noqa: E402
import gpio_bounce  # noqa: E402
import latency_budget  # noqa: E402
import power_model  # noqa: E402
import priors  # noqa: E402
import rf_model  # noqa: E402
import scenario_engine as se  # noqa: E402
from priors import Rng, Theta  # noqa: E402,F401

OUT_LINK = ("residual", "margin_db", "range_m", "g2g_ms", "availability", "ttff_s")

# Нейтралізація всіх деградацій рушія: «нульова деградація = базова модель» (SYNTH-значення тут лише вимикачі, не фізика).
NEUTRAL = {
    "hw.pa_p1db_out_dbm": 80.0, "hw.evm_floor_db": -90.0, "hw.evm_comp_coeff": 0.0, "hw.evm_temp_db_per_c": 0.0,
    "hw.rx_agc_knee_dbm": 50.0, "hw.usb3_present_prob": 0.0, "hw.elrs900_present": 0, "hw.multi24_present": 0,
    "hw.ant_null_prob": 0.0, "hw.ant_pol_max_deg": 0.0, "hw.ant_body_prob": 0.0, "proc.shock_rate_per_h": 0.0,
    "proc.nf_ou_sigma_db": 0.0, "proc.age_hours": 0.0, "proc.shadow_sigma_db": 0.0, "ext.clash_rate_per_h": 0.0,
    "hw.air_board_heat_w": 0.0, "hw.air_rth_c_per_w": 0.1, "hw.tx_sag_knee_v": 0.0, "hw.air_supply_ripple_v": 0.0,
    "hw.air_supply_r_ohm": 0.0, "inj.ebusy_prob": 0.0, "inj.queue_pkts": 512.0, "inj.rate_cap_pps": 1e6,
    "bringup.usb_probe_fail_p": 0.0, "bringup.fw_fail_p": 0.0, "bringup.monitor_fail_p": 0.0, "bringup.inj_start_fail_p": 0.0,
    "vid.bitrate_overshoot": 1.0, "usb.drop_rate_per_h": 1e-9, "usb.reenum_fail_prob": 0.0, "usb.pi5_trip_tol": 0.0,
    "rf.floor_per": 0.0, "timing.stall_rate_per_h": 0.0, "timing.reorder_prob": 0.0, "timing.sched_spike_prob": 0.0,
    "timing.clock_ppm": 0.0,
}


def engine(name="nominal_pi5_5a_150m", sets=None, cfg_over=None):
    return se.Engine(se.load_scenario(name), sets=sets, cfg_over=cfg_over)


def neutral_engine(name="nominal_pi5_5a_150m", extra=None, cfg_over=None):
    s = dict(NEUTRAL)
    s.update(extra or {})
    return se.Engine(se.load_scenario(name), sets=s, cfg_over=cfg_over)


def thetas(eng, n, seed, lo=0.0, hi=1.0):
    """n рівномірних точок у гіперкубі пріорів (seeded)."""
    r = random.Random(seed)
    k = len(eng.space.dims)
    return [eng.space.theta([lo + (hi - lo) * r.random() for _ in range(k)]) for _ in range(n)]


def corner_thetas(eng, n, seed, eps=1e-4):
    """Вершини гіперкуба (кожен вимір на 0+eps або 1-eps): найекстремальніші допустимі комбінації."""
    r = random.Random(seed)
    k = len(eng.space.dims)
    return [eng.space.theta([(eps if r.random() < 0.5 else 1.0 - eps) for _ in range(k)]) for _ in range(n)]


def finite(x):
    return not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def pctl(xs, q):
    return dm.pctl(xs, q)


def ranks(vals):
    order = sorted(range(len(vals)), key=lambda i: -vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(a, b):
    n = len(a)
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    c = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return c / (da * db) if da * db > 0 else float("nan")


def bootstrap_halfwidth(xs, n, q, rnd, B=200):
    """Півширина 90 % бутстреп-інтервалу квантиля q для вибірки розміру n із пулу xs."""
    est = []
    m = len(xs)
    for _ in range(B):
        est.append(pctl([xs[rnd.randrange(m)] for _ in range(n)], q))
    est.sort()
    return (est[int(0.95 * (B - 1))] - est[int(0.05 * (B - 1))]) / 2.0


def var(xs):
    return statistics.variance(xs) if len(xs) > 1 else 0.0


@contextlib.contextmanager
def patched(mod, name, new):
    old = getattr(mod, name)
    setattr(mod, name, new)
    try:
        yield
    finally:
        setattr(mod, name, old)


def clear_caches():
    dm.per_table.cache_clear()
    dm._residual_q.cache_clear()
    dm.fading_weights.cache_clear()
    dm.fading_per_table.cache_clear()
