#!/usr/bin/env python3
"""Довгі дослідження валідації (run.sh --long): гіперкуб, збіжність, Morris між seed, антитетика, структурні альтернативи,
черга M/M/1/K проти Монте-Карло. Лише stdlib; паралелізм multiprocessing (до 4 процесів). Нічого не пише у моделі.

  studies.py hypercube|convergence|morris|antithetic|structural|queue|all [--quick]
Вивід: текстові таблиці (їх числа цитує docs/SIM-VALIDATION.md; SYNTH/модельні, НЕ виміри контуру).
"""
import itertools
import math
import random
import sys
from multiprocessing import Pool

import altforms
import vlib
from vlib import dm, se

SCN = "nominal_pi5_5a_150m"


# ---------------------------------------------------------------- 1. гіперкуб
def _scan_job(a):
    scn, n, seed, corner = a
    eng = vlib.engine(scn)
    ths = vlib.corner_thetas(eng, n, seed) if corner else vlib.thetas(eng, n, seed)
    bad, bounds = [], []
    for i, th in enumerate(ths):
        r = dm.run_session(th, eng.cfg, vlib.Rng(vlib.se.subseed(seed, i, 1)))
        for k, v in r.items():
            if isinstance(v, float) and not vlib.finite(v):
                bad.append((i, k, v))
        for k in ("residual", "availability", "freeze"):
            if not 0.0 <= r[k] <= 1.0:
                bounds.append((i, k, r[k]))
    return bad, bounds


def hypercube(quick=False):
    n = 500 if quick else 2000
    out = ["## hypercube: %d випадкових точок + %d вершин, сценарій %s" % (n, n // 4, SCN)]
    jobs = [(SCN, n // 4, 10 + j, False) for j in range(4)] + [(SCN, n // 16, 90 + j, True) for j in range(4)]
    with Pool(4) as p:
        res = p.map(_scan_job, jobs)
    bad = [x for r in res for x in r[0]]
    bnd = [x for r in res for x in r[1]]
    out.append("sessions=%d NaN/inf=%d out_of_[0,1]=%d" % (sum(j[1] for j in jobs), len(bad), len(bnd)))
    return out


# ---------------------------------------------------------------- 3a. збіжність
def _pool_job(a):
    scn, n, seed = a
    return vlib.engine(scn).run(n, seed)


def convergence(quick=False, scn=SCN):
    total = 800 if quick else 3200
    with Pool(4) as p:
        parts = p.map(_pool_job, [(scn, total // 4, 100 + j) for j in range(4)])
    pool = [r for part in parts for r in part]
    rnd = random.Random(5)
    out = ["## convergence (бутстреп 90 %% ДІ, пул %d draw, сценарій %s): півширина ДІ як частка (p95-p5) пулу" % (len(pool), scn),
           "output,q,pool_value,spread,n=50,100,200,400,800,1600 (півширина/spread); n* = мін. n з півширина<=0.10 spread"]
    for o in vlib.OUT_LINK:
        xs = [r[o] for r in pool if r[o] is not None]  # мертвий лінк не має margin (D4)
        sp = vlib.pctl(xs, 95) - vlib.pctl(xs, 5)
        for q in (5, 50, 95):
            row, nstar = [], None
            for n in (50, 100, 200, 400, 800, 1600):
                if n > len(pool) // 2:
                    row.append("-")
                    continue
                hw = vlib.bootstrap_halfwidth(xs, n, q, rnd, B=120)
                f = hw / sp if sp > 0 else 0.0
                row.append("%.3f" % f)
                if nstar is None and f <= 0.10:
                    nstar = n
            out.append("%s,p%d,%.4g,%.4g,%s,n*=%s" % (o, q, vlib.pctl(xs, q), sp, "/".join(row), nstar if nstar else ">%d" % (len(pool) // 2)))
    return out


# ---------------------------------------------------------------- 3b. Morris між seed
def measure_first(table):
    mf = {}
    for o, rows in table.items():
        tot = sum(r[0] for r in rows) or 1.0
        for mu, _sg, key, prov in rows:
            if prov in ("UNMEASURED", "SYNTH"):
                mf[key] = mf.get(key, 0.0) + 100.0 * mu / tot
    return mf


def top(d, k):
    return [x for x, _ in sorted(d.items(), key=lambda t: (-t[1], t[0]))[:k]]


def _morris_job(a):
    scn, r, seed = a
    eng = vlib.engine(scn)
    table, _noise = se.morris(eng, r, seed, list(se.SENS_LINK))
    return seed, table


def morris_stability(rs=(6, 20), seeds=(1, 2, 3, 4, 5), scn=SCN):
    out = ["## Morris: стабільність рейтингу між %d seed (%s)" % (len(seeds), scn)]
    for r in rs:
        with Pool(4) as p:
            res = dict(p.map(_morris_job, [(scn, r, s) for s in seeds]))
        out.append("r=%d" % r)
        out.append("output,spearman_all_mean,spearman_all_min,top10_overlap_mean,top10_overlap_min")
        for o in se.SENS_LINK:
            mu = {s: {k: m for m, _sg, k, _p in res[s][o]} for s in seeds}
            keys = sorted(mu[seeds[0]])
            rho, ov = [], []
            for a, b in itertools.combinations(seeds, 2):
                rho.append(vlib.spearman([mu[a][k] for k in keys], [mu[b][k] for k in keys]))
                ov.append(len(set(top(mu[a], 10)) & set(top(mu[b], 10))))
            out.append("%s,%.2f,%.2f,%.1f,%d" % (o, sum(rho) / len(rho), min(rho), sum(ov) / len(ov), min(ov)))
        mfs = {s: measure_first(res[s]) for s in seeds}
        keys = sorted(mfs[seeds[0]])
        rho, ov5, ov10 = [], [], []
        for a, b in itertools.combinations(seeds, 2):
            rho.append(vlib.spearman([mfs[a][k] for k in keys], [mfs[b][k] for k in keys]))
            ov5.append(len(set(top(mfs[a], 5)) & set(top(mfs[b], 5))))
            ov10.append(len(set(top(mfs[a], 10)) & set(top(mfs[b], 10))))
        out.append("measure_first(sum share),%.2f,%.2f,top5_overlap_mean=%.1f(min %d),top10_overlap_mean=%.1f(min %d)"
                   % (sum(rho) / len(rho), min(rho), sum(ov5) / len(ov5), min(ov5), sum(ov10) / len(ov10), min(ov10)))
        agg = {}
        for s in seeds:
            for k, v in mfs[s].items():
                agg[k] = agg.get(k, 0.0) + v / len(seeds)
        out.append("measure_first №1 по seed: " + ", ".join("s%d=%s" % (s, top(mfs[s], 1)[0]) for s in seeds))
        out.append("measure_first top-8 (середнє по seed): " + ", ".join("%s=%.1f" % (k, agg[k]) for k in top(agg, 8)))
    return out


# ---------------------------------------------------------------- 3c. антитетика
def _anti_job(a):
    scn, seed, anti, n = a
    res = vlib.engine(scn).run(n, seed, anti)
    d = {}
    for o in vlib.OUT_LINK:
        xs = [r[o] for r in res if r[o] is not None]  # мертвий лінк не має margin (D4)
        d[o] = (sum(xs) / len(xs), vlib.pctl(xs, 50), vlib.pctl(xs, 95))
    return d


def antithetic(quick=False, scn=SCN):
    n, reps = (60, 16) if quick else (100, 60)
    with Pool(4) as p:
        plain = p.map(_anti_job, [(scn, 500 + s, False, n) for s in range(reps)])
        anti = p.map(_anti_job, [(scn, 500 + s, True, n) for s in range(reps)])
    out = ["## антитетика: var(оцінки) із антитетикою / без, n=%d, %d незалежних seed (F-довірчий 90 %%: приблизно 0.65..1.55 при 60)" % (n, reps),
           "output,mean_ratio,p50_ratio,p95_ratio"]
    for o in vlib.OUT_LINK:
        row = []
        for j in range(3):
            vp, va = vlib.var([d[o][j] for d in plain]), vlib.var([d[o][j] for d in anti])
            row.append("%.2f" % (va / vp) if vp > 0 else "nan")
        out.append("%s,%s" % (o, ",".join(row)))
    return out


# ---------------------------------------------------------------- 5. структурні альтернативи
def _form_job(a):
    form, scn, n, seed, r = a
    with altforms.FORMS[form]():
        eng = vlib.engine(scn)
        res = eng.run(n, seed)
        summ = se.summarize(eng, res)
        table = None
        if r:
            table, _ = se.morris(eng, r, seed, list(se.SENS_LINK))
    return form, summ, table


def structural(quick=False, scn=SCN):
    n, r = (150, 6) if quick else (600, 12)
    forms = list(altforms.FORMS)
    with Pool(4) as p:
        res = {f: (s, t) for f, s, t in p.map(_form_job, [(f, scn, n, 3, r) for f in forms])}
    # шумовий еталон: база з іншим seed
    _f, s2, t2 = _form_job(("base", scn, n, 4, r))
    base_s, base_t = res["base"]
    out = ["## структурні альтернативи (%s, n=%d, Morris r=%d, ті самі seed = спільні випадкові числа)" % (scn, n, r),
           "Крутизна ln-шансів базової форми PER у точці 10 %% (MCS1): %.2f 1/дБ" % altforms.baseline_log_odds_slope(1)]
    out.append("form,out,p5,p50,p95  (база: p50 +- 90%%-півширина бутстрепу в дужках)")
    rnd = random.Random(2)
    eng0 = vlib.engine(scn)
    pool = eng0.run(n, 3)
    for form in forms:
        s, _t = res[form]
        for o in ("residual", "margin_db", "range_m", "g2g_ms", "availability"):
            p = s["outputs"][o]["p"]
            b = base_s["outputs"][o]["p"]
            hw = vlib.bootstrap_halfwidth([x[o] for x in pool if x[o] is not None], n, 50, rnd, B=100)  # мертвий лінк не має margin (D4)
            sig = abs(p[50] - b[50]) > hw
            out.append("%s,%s,%.4g,%.4g,%.4g  dp50=%+.3g (ДІ бази +-%.3g)%s" % (form, o, p[5], p[50], p[95], p[50] - b[50], hw,
                                                                           " ЗНАЧУЩА ЗМІНА" if sig and form != "base" else ""))
    out.append("failure_mode,probability: база / альтернативи (макс |Δ| по формі)")
    for m in base_s["modes"]:
        row = ["%s base=%.3f" % (m, base_s["modes"][m])]
        for form in forms[1:]:
            row.append("%s=%.3f" % (form, res[form][0]["modes"][m]))
        out.append("; ".join(row))
    out.append("measure_first (UNMEASURED/SYNTH, сума часток): top-8 і збіг із базою")
    mf0 = measure_first(base_t)
    ref = measure_first(t2)
    keys = sorted(mf0)
    out.append("base(seed 3) top-8: " + ", ".join(top(mf0, 8)))
    out.append("база seed3 vs seed4 (шумовий еталон): top5 overlap=%d top10 overlap=%d spearman=%.2f"
               % (len(set(top(mf0, 5)) & set(top(ref, 5))), len(set(top(mf0, 10)) & set(top(ref, 10))),
                  vlib.spearman([mf0[k] for k in keys], [ref[k] for k in keys])))
    for form in forms[1:]:
        mf = measure_first(res[form][1])
        out.append("%s: top5 overlap=%d top10 overlap=%d spearman=%.2f; top-5: %s" % (
            form, len(set(top(mf0, 5)) & set(top(mf, 5))), len(set(top(mf0, 10)) & set(top(mf, 10))),
            vlib.spearman([mf0[k] for k in keys], [mf[k] for k in keys]), ", ".join(top(mf, 5))))
    return out


# ---------------------------------------------------------------- черга: M/M/1/K vs MC (і M/D/1/K)
def queue_sim(rho, k, n_arr, service, seed):
    """Подієва симуляція черги з пуассонівськими приходами (інтенсивність rho, середня служба 1) і K місцями в системі
    (K = місткість СИСТЕМИ (черга + служба), як у формулі injection_block_prob). service: 'exp'|'det'."""
    rnd = random.Random(seed)
    cap = k
    t = 0.0
    dep = []  # моменти відходу тих, хто в системі
    block = 0
    last_dep = 0.0
    for _ in range(n_arr):
        t += rnd.expovariate(rho)
        while dep and dep[0] <= t:
            dep.pop(0)
        if len(dep) >= cap:
            block += 1
            continue
        s = rnd.expovariate(1.0) if service == "exp" else 1.0
        start = max(t, last_dep)
        last_dep = start + s
        dep.append(last_dep)
    return block / n_arr


def queue(quick=False):
    n = 60000 if quick else 400000
    out = ["## черга ін'єкції: M/M/1/K (замкнена), M/D/1/K (замкнена, точна; типова для рушія з D10) проти подієвого MC (експоненційна й детермінована служба)",
           "rho,K,closed_M/M/1/K,closed_M/D/1/K,MC_M/M/1/K,MC_M/D/1/K"]
    for rho, k in ((0.8, 3), (0.95, 5), (1.0, 5), (1.1, 5), (1.5, 10), (2.0, 20), (0.9, 30), (1.0, 288)):
        cf = dm.injection_block_prob(rho, k)
        cd = dm.injection_block_prob_det(rho, k)
        mm = queue_sim(rho, k, n, "exp", 1)
        md = queue_sim(rho, k, n, "det", 1)
        out.append("%.2f,%d,%.4f,%.4f,%.4f,%.4f" % (rho, k, cf, cd, mm, md))
    return out


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    quick = "--quick" in a
    scn = next((x.split("=", 1)[1] for x in a if x.startswith("--scn=")), SCN)
    a = [x for x in a if x != "--quick" and not x.startswith("--scn=")]
    what = a[0] if a else "all"
    fn = {"hypercube": lambda: hypercube(quick), "convergence": lambda: convergence(quick, scn),
          "morris": lambda: morris_stability((6,) if quick else (6, 20), (1, 2, 3) if quick else (1, 2, 3, 4, 5), scn),
          "antithetic": lambda: antithetic(quick, scn), "structural": lambda: structural(quick, scn), "queue": lambda: queue(quick)}
    names = list(fn) if what == "all" else [what]
    for nme in names:
        print("\n".join(fn[nme]()))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
