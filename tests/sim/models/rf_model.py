#!/usr/bin/env python3
"""RF link model: path loss -> SNR -> PER per 802.11n/ac MCS -> wfb-ng FEC residual video-packet loss.

A MODEL, not a proof: it ranks MCS/FEC/distance choices and shows the dominant risk before the hardware
exists. It cannot certify range. Every number comes from params.json (provenance per parameter); a
measured value overrides it via MODEL_MEASURED / MODEL_SET / --set (see common.py).

Formulas (assumptions in params.json -> sections.rf.assumptions and docs/SIM-MODELS.md):
  FSPL(d0) = 20log10(d0/1000) + 20log10(f_MHz) + 32.44            (textbook)
  PL(d)    = FSPL(d0) + 10 n log10(d/d0)                          (log-distance, n = path_loss_exponent)
  Rx       = Ptx + Gtx + Grx - PL - misc
  Noise    = -174 dBm/Hz + 10log10(BW) + NF ; SNR = Rx - 10log10(10^(N/10) + 10^(I/10))
  PER      = 1 - (1 - Pe(BER(SNR_eff)))^(8*bytes)   BER and union-bound Pe as in ns-3 NistErrorRateModel
  FEC      residual data loss = p * P(Bin(n-1, p) >= n-k)  (iid), exact 2-state DP for Gilbert bursts

Usage:
  rf_model.py table --mcs 1 --fec 8/12 [--bw 20] [--dmin 100 --dmax 8000 --steps 12 | --dists 100,500,1000]
  rf_model.py range --mcs 1 --fec 8/12
  rf_model.py sweep [--fecs 1/1,8/12]       # all MCS x FEC: SNR@10%PER, airtime utilisation, max range
  rf_model.py snr   --mcs 1                 # SNR needed for 10 % PER
Common: --fading none|rayleigh|rician  --loss-model iid|ge  --set key=val  --params file.json
"""
import argparse
import functools
import math
import random
import sys

import common

# (modulation, bits per subcarrier, constellation size, code rate (num, den), ns-3 bValue)
HT_MCS = {
    0: ("BPSK", 1, 2, (1, 2), 1), 1: ("QPSK", 2, 4, (1, 2), 1), 2: ("QPSK", 2, 4, (3, 4), 3),
    3: ("16QAM", 4, 16, (1, 2), 1), 4: ("16QAM", 4, 16, (3, 4), 3), 5: ("64QAM", 6, 64, (2, 3), 2),
    6: ("64QAM", 6, 64, (3, 4), 3), 7: ("64QAM", 6, 64, (5, 6), 5),
}
VHT_EXTRA = {8: ("256QAM", 8, 256, (3, 4), 3), 9: ("256QAM", 8, 256, (5, 6), 5)}
NSD = {20: 52, 40: 108}  # data subcarriers (802.11n/ac, INF: standard not read)

# Union-bound weight spectra of the K=7 (133,171) convolutional code, punctured; exponents of D.
# Same tables as ns-3 NistErrorRateModel::CalculatePe (cited in docs/SIM-MODELS.md), reimplemented here.
_SPECTRA = {
    1: (0.5, ((36, 10), (211, 12), (1404, 14), (11633, 16), (77433, 18), (502690, 20), (3322763, 22),
              (21292910, 24), (134365911, 26))),
    2: (1 / 4, ((3, 6), (70, 7), (285, 8), (1276, 9), (6160, 10), (27128, 11), (117019, 12), (498860, 13),
                (2103891, 14), (8784123, 15))),
    3: (1 / 6, ((42, 5), (201, 6), (1492, 7), (10469, 8), (62935, 9), (379644, 10), (2253373, 11),
                (13073811, 12), (75152755, 13), (428005675, 14))),
    5: (1 / 10, ((92, 4), (528, 5), (8694, 6), (79453, 7), (792114, 8), (7375573, 9), (67884974, 10),
                 (610875423, 11), (5427275376, 12), (47664215639, 13))),
}


def mcs_info(mcs, vht=False):
    tab = dict(HT_MCS)
    if vht:
        tab.update(VHT_EXTRA)
    if mcs not in tab:
        raise common.ParamError("MCS %s not valid (vht=%s)" % (mcs, vht))
    return tab[mcs]


def phy_rate_mbps(mcs, bw, sgi, vht=False, nss=1):
    _m, bits, _c, (cn, cd), _b = mcs_info(mcs, vht)
    if vht and mcs == 9 and bw == 20 and nss == 1:
        raise common.ParamError("VHT MCS9 is not defined for 20 MHz 1SS")
    return NSD[bw] * bits * cn / cd * nss / (3.6 if sgi else 4.0)


def airtime_us(mpdu_bytes, mcs, bw, sgi, vht, stbc, nss=1):
    """PPDU airtime in us: preamble (HT-mixed or VHT) + data symbols (INF: standard constants)."""
    _m, bits, _c, (cn, cd), _b = mcs_info(mcs, vht)
    ndbps = NSD[bw] * bits * cn / cd * nss
    nsts = nss + (1 if stbc else 0)
    nltf = {1: 1, 2: 2, 3: 4, 4: 4}[nsts]
    pre = (36 if vht else 32) + 4 * nltf
    nsym = math.ceil((16 + 8 * mpdu_bytes + 6) / ndbps)
    return pre + nsym * (3.6 if sgi else 4.0)


# ---------------------------------------------------------------- PHY error model
def _ber(mod_bits, m, snr):
    if m == 2:
        return 0.5 * math.erfc(math.sqrt(snr))
    if m == 4:
        return 0.5 * math.erfc(math.sqrt(snr / 2.0))
    z = math.sqrt(snr / ((2 * (m - 1)) // 3))
    b = int(math.sqrt(m))
    return ((b - 1) / (b * math.log2(b))) * math.erfc(z)


def _pe(p, bval):
    d = math.sqrt(4.0 * p * (1.0 - p))
    fac, terms = _SPECTRA[bval]
    return fac * sum(c * d ** e for c, e in terms)


def per_ideal(mcs, snr_db, nbytes, vht=False):
    """Frame error rate of the ideal receiver at per-subcarrier SNR (dB). Monotone non-increasing in SNR."""
    _mod, bits, m, _cr, bval = mcs_info(mcs, vht)
    snr = 10 ** (snr_db / 10.0)
    ber = _ber(bits, m, snr)
    if ber == 0.0:
        return 0.0
    pe = min(_pe(ber, bval), 1.0)
    if pe >= 1.0:
        return 1.0
    return -math.expm1(8 * nbytes * math.log1p(-pe))


# ---------------------------------------------------------------- link budget
def fspl_db(d_m, f_mhz):
    return 20 * math.log10(d_m / 1000.0) + 20 * math.log10(f_mhz) + 32.44


def path_loss_db(P, d_m):
    d0 = P.get("rf.ref_distance_m")
    d = max(d_m, d0)
    return fspl_db(d0, P.get("rf.freq_mhz")) + 10 * P.get("rf.path_loss_exponent") * math.log10(d / d0)


def noise_dbm(P):
    return -174.0 + 10 * math.log10(P.get("rf.bandwidth_mhz") * 1e6) + P.get("rf.noise_figure_db")


def rx_dbm(P, d_m):
    return (P.get("rf.tx_power_dbm") + P.get("rf.tx_antenna_gain_dbi") + P.get("rf.rx_antenna_gain_dbi")
            - path_loss_db(P, d_m) - P.get("rf.misc_loss_db"))


def snr_db(P, d_m):
    n_mw = 10 ** (noise_dbm(P) / 10.0)
    i = P.get("rf.interference_dbm")
    if i is not None:
        n_mw += 10 ** (i / 10.0)
    return rx_dbm(P, d_m) - 10 * math.log10(n_mw)


@functools.lru_cache(maxsize=16)
def _gains(model, k_db, n, seed):
    """Unit-mean power gains (fixed draws -> common random numbers, so loss is monotone in distance)."""
    if model == "rayleigh":
        g = [-math.log(1 - (i + 0.5) / n) for i in range(n)]
    elif model == "rician":
        rnd = random.Random(seed)
        k = 10 ** (k_db / 10.0)
        los = math.sqrt(k / (k + 1))
        s = math.sqrt(1 / (2 * (k + 1)))
        g = []
        for _ in range(n):
            x = los + s * rnd.gauss(0, 1)
            y = s * rnd.gauss(0, 1)
            g.append(x * x + y * y)
    else:
        return (1.0,)
    mean = sum(g) / len(g)
    return tuple(x / mean for x in g)


def eff_snr_db(P, mcs, link_snr_db):
    """SNR seen by the ideal decoder: link SNR minus implementation loss, plus coding/diversity credits."""
    off = P.get("rf.mcs_snr_offset_db")
    o = off[mcs] if mcs < len(off) else 0.0
    s = link_snr_db - P.get("rf.implementation_loss_db") - o + P.get("rf.diversity_gain_db")
    if P.get("rf.ldpc"):
        s += P.get("rf.ldpc_gain_db")
    return s


def frame_bytes(P):
    return P.get("rf.payload_bytes") + P.get("rf.wfb_overhead_bytes")


def frame_per(P, mcs, link_snr_db):
    """Average frame error rate incl. fading draws and the non-RF floor."""
    vht = bool(P.get("rf.vht"))
    nb = frame_bytes(P)
    gains = _gains(P.get("rf.fading_model"), P.get("rf.rician_k_db"), int(P.get("rf.fading_draws")),
                   int(P.get("rf.fading_seed")))
    tot = 0.0
    for g in gains:
        tot += per_ideal(mcs, eff_snr_db(P, mcs, link_snr_db + 10 * math.log10(g)), nb, vht)
    per = tot / len(gains)
    fl = P.get("rf.floor_per")
    return 1 - (1 - per) * (1 - fl)


# ---------------------------------------------------------------- FEC residual loss
def residual_iid(p, k, n):
    """Expected fraction of data packets NOT delivered with FEC k/n and iid frame loss p."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    m = n - 1
    need = n - k  # others lost >= need  => block unrecoverable
    tail = sum(math.comb(m, j) * p ** j * (1 - p) ** (m - j) for j in range(need, m + 1))
    return p * tail


def residual_ge(p, burst, k, n):
    """Same for a Gilbert channel (good = deliver, bad = lose) with mean bad-run `burst` frames. Exact DP."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    burst = max(burst, 1.0 / (1.0 - p))  # never less bursty than iid
    p_bg = 1.0 / burst
    p_gb = min(1.0, p_bg * p / (1.0 - p))
    pi_b = p_gb / (p_gb + p_bg)
    # state 0 = good, 1 = bad; P[s][L] prob, E[s][L] = E[lost data packets ; state, L]
    P = [[0.0] * (n + 1) for _ in range(2)]
    E = [[0.0] * (n + 1) for _ in range(2)]
    P[0][0] = 1 - pi_b
    P[1][0] = pi_b
    for i in range(n):
        P2 = [[0.0] * (n + 1) for _ in range(2)]
        E2 = [[0.0] * (n + 1) for _ in range(2)]
        for s in range(2):
            for L in range(i + 1):
                pr = P[s][L]
                if pr == 0.0:
                    continue
                lost = 1 if s == 1 else 0
                e = E[s][L] + (pr if (lost and i < k) else 0.0)
                for s2, tr in ((0, (1 - p_gb) if s == 0 else p_bg), (1, p_gb if s == 0 else (1 - p_bg))):
                    P2[s2][L + lost] += pr * tr
                    E2[s2][L + lost] += e * tr
        P, E = P2, E2
    tot = 0.0
    for s in range(2):
        for L in range(n - k + 1, n + 1):
            tot += E[s][L]
    return tot / k


def residual(P, p, k, n):
    if P.get("rf.loss_model") == "ge":
        return residual_ge(p, P.get("rf.ge_mean_burst_frames"), k, n)
    return residual_iid(p, k, n)


def iid_equivalent(P, p, k, n):
    """Per-frame iid loss that gives the same residual loss with FEC k/n as the configured loss model."""
    target = residual(P, p, k, n)
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if residual_iid(mid, k, n) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------- scenario evaluation
def packets_per_frame(P):
    bpf = P.get("video.bitrate_kbps") * 1000 / 8 / P.get("video.fps")
    return max(1, math.ceil(bpf / P.get("rf.payload_bytes")))


def frame_airtime_us(P, mcs=None):
    mcs = P.get("rf.mcs_index") if mcs is None else mcs
    return airtime_us(frame_bytes(P), mcs, int(P.get("rf.bandwidth_mhz")), bool(P.get("rf.short_gi")),
                      bool(P.get("rf.vht")), P.get("rf.stbc"))


def utilisation(P, mcs, k, n):
    pps = P.get("video.bitrate_kbps") * 1000 / 8 / P.get("rf.payload_bytes")
    return pps * (n / k) * (frame_airtime_us(P, mcs) + P.get("rf.mac_access_us")) / 1e6


def evaluate(P, d_m, mcs, k, n):
    s = snr_db(P, d_m)
    per = frame_per(P, mcs, s)
    res = residual(P, per, k, n)
    ppf = packets_per_frame(P)
    return {"d": d_m, "rx": rx_dbm(P, d_m), "snr": s, "per": per, "res": res,
            "frame_loss": 1 - (1 - res) ** ppf}


def max_range(P, mcs, k, n, target=None):
    """Largest distance with residual <= target. Returns (metres, capped_at_search_limit)."""
    target = P.get("rf.target_residual") if target is None else target
    lo = P.get("rf.ref_distance_m")
    hi = P.get("rf.max_search_m")
    if evaluate(P, hi, mcs, k, n)["res"] <= target:
        return hi, True
    if evaluate(P, lo, mcs, k, n)["res"] > target:
        return 0.0, False
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if evaluate(P, mid, mcs, k, n)["res"] <= target:
            lo = mid
        else:
            hi = mid
    return lo, False


def snr_for_per(P, mcs, per_target=0.1):
    """Link SNR (dB) at which the average frame error rate equals per_target."""
    lo, hi = -20.0, 80.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if frame_per(P, mcs, mid) > per_target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------- CLI
def parse_fec(s):
    try:
        k, n = (int(x) for x in s.split("/"))
    except ValueError:
        raise common.ParamError("--fec wants k/n, got " + s)
    if not 1 <= k <= n < 256:
        raise common.ParamError("need 1 <= k <= n < 256")
    return k, n


def apply_common(P, a):
    for flag, key in (("mcs", "rf.mcs_index"), ("bw", "rf.bandwidth_mhz"), ("fading", "rf.fading_model"),
                      ("loss_model", "rf.loss_model")):
        v = getattr(a, flag, None)
        if v is not None:
            P._override(key, v, "OVERRIDE")
    k, n = parse_fec(a.fec) if getattr(a, "fec", None) else (int(P.get("rf.fec_k")), int(P.get("rf.fec_n")))
    return k, n


def header(P, k, n):
    mcs = int(P.get("rf.mcs_index"))
    mod, bits, _m, (cn, cd), _b = mcs_info(mcs, bool(P.get("rf.vht")))
    rate = phy_rate_mbps(mcs, int(P.get("rf.bandwidth_mhz")), bool(P.get("rf.short_gi")), bool(P.get("rf.vht")))
    return [
        "# rf_model: mcs=%d (%s %d/%d) bw=%d vht=%d stbc=%d ldpc=%d sgi=%d fec=%d/%d fading=%s loss_model=%s"
        % (mcs, mod, cn, cd, P.get("rf.bandwidth_mhz"), P.get("rf.vht"), P.get("rf.stbc"), P.get("rf.ldpc"),
           P.get("rf.short_gi"), k, n, P.get("rf.fading_model"), P.get("rf.loss_model")),
        "# link: f=%.0fMHz n=%.2f tx=%.1fdBm gains=%.1f+%.1fdBi misc=%.1fdB NF=%.1fdB noise=%.1fdBm impl_loss=%.1fdB"
        % (P.get("rf.freq_mhz"), P.get("rf.path_loss_exponent"), P.get("rf.tx_power_dbm"),
           P.get("rf.tx_antenna_gain_dbi"), P.get("rf.rx_antenna_gain_dbi"), P.get("rf.misc_loss_db"),
           P.get("rf.noise_figure_db"), noise_dbm(P), P.get("rf.implementation_loss_db")),
        "# phy=%.2fMbps frame=%dB airtime=%.1fus video=%dkbps pkts/frame=%d airtime_util=%.2f%s"
        % (rate, frame_bytes(P), frame_airtime_us(P), P.get("video.bitrate_kbps"), packets_per_frame(P),
           utilisation(P, mcs, k, n),
           "  INFEASIBLE(bitrate x n/k exceeds the air)" if utilisation(P, mcs, k, n) > 1 else ""),
    ]


def dist_list(a):
    if a.dists:
        return [float(x) for x in a.dists.split(",")]
    if a.steps < 2:
        return [a.dmin]
    r = (a.dmax / a.dmin) ** (1.0 / (a.steps - 1))
    return [a.dmin * r ** i for i in range(a.steps)]


def cmd_table(P, a):
    k, n = apply_common(P, a)
    mcs = int(P.get("rf.mcs_index"))
    out = header(P, k, n)
    out.append("dist_m,rx_dbm,snr_db,per,residual,frame_loss")
    for d in dist_list(a):
        e = evaluate(P, d, mcs, k, n)
        out.append("%.0f,%.1f,%.1f,%.3e,%.3e,%.3e" % (e["d"], e["rx"], e["snr"], e["per"], e["res"], e["frame_loss"]))
    r, capped = max_range(P, mcs, k, n)
    out.append("max_range_m(residual<=%.3g)=%s%.0f" % (P.get("rf.target_residual"), ">=" if capped else "", r))
    return out


def cmd_range(P, a):
    k, n = apply_common(P, a)
    mcs = int(P.get("rf.mcs_index"))
    r, capped = max_range(P, mcs, k, n)
    out = header(P, k, n)
    out.append("max_range_m(residual<=%.3g)=%s%.0f" % (P.get("rf.target_residual"), ">=" if capped else "", r))
    return out


def cmd_snr(P, a):
    apply_common(P, a)
    mcs = int(P.get("rf.mcs_index"))
    return ["snr_at_10pct_per_db(mcs=%d)=%.2f" % (mcs, snr_for_per(P, mcs))]


def cmd_sweep(P, a):
    apply_common(P, a)
    vht = bool(P.get("rf.vht"))
    bw = int(P.get("rf.bandwidth_mhz"))
    fecs = [parse_fec(x) for x in a.fecs.split(",")]
    out = ["# rf_model sweep: bw=%d vht=%d fading=%s loss_model=%s video=%dkbps target=%.3g"
           % (bw, vht, P.get("rf.fading_model"), P.get("rf.loss_model"), P.get("video.bitrate_kbps"),
              P.get("rf.target_residual")),
           "mcs,mod,rate,phy_mbps,snr10_db,fec,util,max_range_m"]
    top = 9 if vht else 7
    for mcs in range(top + 1):
        if vht and mcs == 9 and bw == 20:
            continue
        mod, _b, _m, (cn, cd), _bv = mcs_info(mcs, vht)
        s10 = snr_for_per(P, mcs)
        for k, n in fecs:
            r, capped = max_range(P, mcs, k, n)
            u = utilisation(P, mcs, k, n)
            out.append("%d,%s,%d/%d,%.1f,%.1f,%d/%d,%.2f%s,%s%.0f" % (
                mcs, mod, cn, cd, phy_rate_mbps(mcs, bw, bool(P.get("rf.short_gi")), vht), s10, k, n, u,
                "!" if u > 1 else "", ">=" if capped else "", r))
    out.append("# '!' = airtime utilisation > 1: the stream does not fit the air at this MCS/FEC, the range is moot")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("table", "range", "sweep", "snr"):
        sp = sub.add_parser(name)
        common.add_cli(sp)
        sp.add_argument("--mcs", type=int)
        sp.add_argument("--bw", type=int, choices=(20, 40))
        sp.add_argument("--fading", choices=("none", "rayleigh", "rician"))
        sp.add_argument("--loss-model", choices=("iid", "ge"))
        sp.add_argument("--fec", help="k/n")
        if name == "table":
            sp.add_argument("--dmin", type=float, default=100.0)
            sp.add_argument("--dmax", type=float, default=8000.0)
            sp.add_argument("--steps", type=int, default=12)
            sp.add_argument("--dists", help="comma list of metres (overrides dmin/dmax/steps)")
        if name == "sweep":
            sp.add_argument("--fecs", default="1/1,8/12,4/12")
    a = ap.parse_args(argv)
    try:
        P = common.from_args(a)
        out = {"table": cmd_table, "range": cmd_range, "sweep": cmd_sweep, "snr": cmd_snr}[a.cmd](P, a)
    except common.ParamError as e:
        print("rf_model: error: %s" % e, file=sys.stderr)
        return 2
    out.append(P.footer())
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
