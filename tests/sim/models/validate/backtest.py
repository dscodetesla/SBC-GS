#!/usr/bin/env python3
"""Back-test моделей проти ЗОВНІШНІХ довідкових даних. Дані нижче ПЕРЕЧИТАНІ напряму з першоджерел 2026-10-03 (curl через дозволений
проксі, TLS перевірявся); кожен блок має тег SRC + URL. Нічого не вигадано: чого не вдалося прочитати, записано як «недоступно».

  backtest.py            таблиця порівнянь модель/довідник
  backtest.py --online   додатково повторно завантажити джерела й перевірити, що ключові рядки досі на місці (потрібна мережа/проксі)
"""
import math
import os
import re
import sys

import vlib
from vlib import common, dm, power_model, rf_model

# SRC https://www.mathworks.com/help/wlan/ug/802-11ac-receiver-minimum-input-sensitivity-test.html (код rxMinSensitivityTable,
# «Table 21-25 of IEEE Std 802.11-2020», 20 МГц, +3 дБ на подвоєння смуги, PER 10 %), і
# SRC https://www.eurecom.fr/en/publication/5471/download/comsys-publi-5471.pdf (Table 2, 20/40/80/160 МГц, Nss=1).
# УВАГА: це вимога стандарту (мінімум), не типова чутливість конкретного чипа.
STD_MIN_SENS_20MHZ = [-82, -79, -77, -74, -70, -66, -65, -64, -59, -57]
STD_URL = ("https://www.mathworks.com/help/wlan/ug/802-11ac-receiver-minimum-input-sensitivity-test.html; "
           "https://www.eurecom.fr/en/publication/5471/download/comsys-publi-5471.pdf")

# SRC https://en.wikipedia.org/wiki/Free-space_path_loss : FSPL(dB)=20log10(d_km)+20log10(f_GHz)+92.45
FSPL_URL = "https://en.wikipedia.org/wiki/Free-space_path_loss"

# SRC https://raw.githubusercontent.com/nsnam/ns-3-dev-git/master/src/wifi/model/nist-error-rate-model.cc  (CalculatePe, GetQamBer)
NS3_URL = "https://raw.githubusercontent.com/nsnam/ns-3-dev-git/master/src/wifi/model/nist-error-rate-model.cc"
NS3_PE = {1: (0.5, [(36, 10), (211, 12), (1404, 14), (11633, 16), (77433, 18), (502690, 20), (3322763, 22), (21292910, 24), (134365911, 26)]),
          2: (1 / 4, [(3, 6), (70, 7), (285, 8), (1276, 9), (6160, 10), (27128, 11), (117019, 12), (498860, 13), (2103891, 14), (8784123, 15)]),
          3: (1 / 6, [(42, 5), (201, 6), (1492, 7), (10469, 8), (62935, 9), (379644, 10), (2253373, 11), (13073811, 12), (75152755, 13), (428005675, 14)]),
          5: (1 / 10, [(92, 4), (528, 5), (8694, 6), (79453, 7), (792114, 8), (7375573, 9), (67884974, 10), (610875423, 11), (5427275376, 12), (47664215639, 13)])}

# SRC https://raw.githubusercontent.com/raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc
PI_URL = "https://raw.githubusercontent.com/raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc"
PI_DOC = {  # board: (рекомендований БЖ, А; макс. загальний струм USB, А; типовий активний струм голої плати, А)
    "pi3bp": (2.5, 1.2, 0.5), "pi4": (3.0, 1.2, 0.6), "pi5": (5.0, 1.6, 0.8)}
PI5_USB_WEAK_PSU_A = 0.6  # «1.6A (600mA if using a 3A power supply)» та NOTE: будь-який інший сумісний БЖ -> 600 mA
PI_UV_V, PI_UV_TOL = 4.63, 0.05  # «drops below 4.63 V (±5%)»
PI_PSU_V = 5.1  # «All models require a 5.1V supply»
# Таблиця струмів (стовпець Raspberry Pi 3B, НЕ 3B+; 4B окремо): idle avg, stress avg, stress max
PI_DOC_TABLE = {"pi3b_column": {"idle_avg": 0.30, "stress_avg": 0.85, "stress_max": 1.34},
                "pi4b": {"idle_avg": 0.6, "stress_avg": 1.2, "stress_max": 1.25}}

# SRC https://raw.githubusercontent.com/raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/frequency-management.adoc
THERMAL_URL = "https://raw.githubusercontent.com/raspberrypi/documentation/master/documentation/asciidoc/computers/raspberry-pi/frequency-management.adoc"
PI_THERMAL_LIMIT_C = 85.0       # «temperatures do not exceed a limit which we define as 85°C on all models»
PI_THROTTLE_START_C = 80.0      # «between 80°C and 85°C, the Arm cores will be progressively throttled back»
# SRC https://raw.githubusercontent.com/raspberrypi/documentation/master/documentation/asciidoc/computers/os/graphics-utilities.adoc (get_throttled)
THROT_URL = "https://raw.githubusercontent.com/raspberrypi/documentation/master/documentation/asciidoc/computers/os/graphics-utilities.adoc"
THROT_BITS_DOC = {0: "Undervoltage detected", 1: "Arm frequency capped", 2: "Currently throttled", 3: "Soft temperature limit active"}

UNAVAILABLE = [
    ("Realtek RTL8812AU datasheet (Rev 0.2, 18 стор., elinfor.com PDF, прочитано, текст витягнуто pypdf)",
     "документ прочитано, але він НЕ містить таблиці чутливості, EVM, вихідної потужності й споживання (жодного «dBm» у тексті): порівняти нема з чим"),
    ("fcc.report FCC-ID для RTL8812AU модулів", "недоступно: Cloudflare-перевірка повертає HTTP 403 (обхід не робився)"),
    ("manuals.plus LB-LINK BL-R8812AF1 specification", "недоступно: HTTP 403 (обхід не робився)"),
    ("datasheetcafe.com RTL8812AU-CG", "недоступно: HTTP 403 (обхід не робився)"),
    ("струми RTL8812AU/EU, EEPROM-таблиці потужності, поріг термозахисту адаптера, USB-hazard", "джерел не знайдено: лишаються UNMEASURED/SYNTH"),
]


def sens_rows(vht=False):
    """(mcs, std_dBm, model_dBm, delta) для HT MCS0..7 (+VHT MCS8) 20 МГц, без завмирання, дефолтні параметри."""
    rows = []
    top = 8 if vht else 7
    for m in range(top + 1):
        P = common.load({"rf.mcs_index": m, "rf.vht": bool(m >= 8), "rf.fading_model": "none"})
        s10 = rf_model.snr_for_per(P, m)
        model = rf_model.noise_dbm(P) + s10
        rows.append((m, STD_MIN_SENS_20MHZ[m], model, model - STD_MIN_SENS_20MHZ[m]))
    return rows


def sens_summary(rows):
    d = [r[3] for r in rows]
    steps = [(rows[i + 1][2] - rows[i][2]) - (rows[i + 1][1] - rows[i][1]) for i in range(len(rows) - 1)]
    return {"offset_min": min(d), "offset_max": max(d), "offset_spread": max(d) - min(d),
            "step_err_max": max(abs(x) for x in steps), "monotone": all(rows[i + 1][2] > rows[i][2] for i in range(len(rows) - 1))}


def fspl_ref(d_m, f_mhz):
    return 20 * math.log10(d_m / 1000.0) + 20 * math.log10(f_mhz / 1000.0) + 92.45


def fspl_rows():
    P = common.load()
    out = []
    for d, f in ((1, 5745), (100, 5745), (1000, 5745), (10000, 5180), (500, 2437), (150, 5745)):
        out.append((d, f, fspl_ref(d, f), rf_model.fspl_db(d, f)))
    # path_loss_db при n=2 і d>=d0 має збігатися з FSPL
    P.leaves["rf.path_loss_exponent"]["value"] = 2.0
    out.append(("PL(n=2) 150", "m,5745", fspl_ref(150, 5745), rf_model.path_loss_db(P, 150)))
    return out


def ns3_ber(m, snr):
    """Незалежна транскрипція GetBpskBer/GetQpskBer/GetQamBer з SRC ns-3 (C++ -> Python)."""
    if m == 2:
        return 0.5 * math.erfc(math.sqrt(snr))
    if m == 4:
        return 0.5 * math.erfc(math.sqrt(snr / 2.0))
    z = math.sqrt(snr / ((2 * (m - 1)) // 3))
    b = int(math.sqrt(m))
    return ((b - 1) / (b * math.log2(b))) * math.erfc(z)


def ns3_pe(p, bvalue):
    d = math.sqrt(4.0 * p * (1.0 - p))
    fac, terms = NS3_PE[bvalue]
    return fac * sum(c * d ** e for c, e in terms)


def ns3_max_rel_diff():
    """Найбільша відносна різниця між union-bound Pe моделі та транскрипцією ns-3 по сітці BER."""
    worst = 0.0
    for bv in (1, 2, 3, 5):
        for i in range(1, 60):
            p = 10 ** (-0.1 * i * 0.6 - 0.3)
            a, b = rf_model._pe(p, bv), ns3_pe(p, bv)
            worst = max(worst, abs(a - b) / b)
    for m in (2, 4, 16, 64, 256):
        for snr_db in range(-5, 40, 3):
            snr = 10 ** (snr_db / 10)
            bits = {2: 1, 4: 2, 16: 4, 64: 6, 256: 8}[m]
            a, b = rf_model._ber(bits, m, snr), ns3_ber(m, snr)
            if b > 1e-300:
                worst = max(worst, abs(a - b) / b)
    return worst


def pi_rows():
    """(опис, довідник, модель, збіг?)"""
    P = common.load()
    rows = []
    for b, (psu, usb, act) in PI_DOC.items():
        rows.append(("%s psu_recommended_a" % b, psu, P.get("power.boards.%s.psu_recommended_a" % b)))
        rows.append(("%s usb_budget_a" % b, usb, P.get("power.boards.%s.usb_budget_a" % b)))
        rows.append(("%s board_active_a" % b, act, P.get("power.boards.%s.board_active_a" % b)))
    rows.append(("pi5 usb limit, 3 A PSU", PI5_USB_WEAK_PSU_A, power_model.usb_budget_a(P, "pi5", 3.0, False)))
    rows.append(("pi5 usb limit, 5 A PSU", 1.6, power_model.usb_budget_a(P, "pi5", 5.0, False)))
    rows.append(("pi5 usb limit, 4 A PSU (будь-який інший БЖ)", PI5_USB_WEAK_PSU_A, power_model.usb_budget_a(P, "pi5", 4.0, False)))
    rows.append(("undervolt threshold V", PI_UV_V, P.get("power.undervolt_threshold_v")))
    rows.append(("psu nominal V", PI_PSU_V, P.get("power.psu_nominal_v")))
    rows.append(("pi4 idle avg A", PI_DOC_TABLE["pi4b"]["idle_avg"], P.get("power.boards.pi4.board_idle_a")))
    rows.append(("pi4 stress avg A", PI_DOC_TABLE["pi4b"]["stress_avg"], P.get("power.boards.pi4.board_load_a")))
    rows.append(("pi3bp idle (док: стовпець 3B) A", PI_DOC_TABLE["pi3b_column"]["idle_avg"], P.get("power.boards.pi3bp.board_idle_a")))
    rows.append(("pi3bp stress avg (док: стовпець 3B) A", PI_DOC_TABLE["pi3b_column"]["stress_avg"], P.get("power.boards.pi3bp.board_load_a")))
    return rows


def throttled_rows():
    return [(b, name, power_model.THROTTLED_BITS.get(b)) for b, name in sorted(THROT_BITS_DOC.items())]


def soc_prior_rows():
    """Пріор hw.soc_soft_limit_c проти документації Pi (прогресивний дросель 80..85 °C; жорсткий ліміт 85 °C). Носій і P(>85 °C) рахуються
    з квантилів самого пріора (будь-який вид розподілу), а не з припущення про нормальний розподіл."""
    deg = vlib.priors.load_degrade()
    d = deg.dist("hw.soc_soft_limit_c")
    n = 20001
    xs = [d.ppf((i + 0.5) / n) for i in range(n)]
    return {"kind": d.kind, "lo": min(xs), "hi": max(xs), "median": d.ppf(0.5),
            "p_above_hard_limit": sum(1 for x in xs if x > PI_THERMAL_LIMIT_C) / n,
            "p_below_doc_start": sum(1 for x in xs if x < PI_THROTTLE_START_C) / n,
            "doc_start": PI_THROTTLE_START_C, "doc_limit": PI_THERMAL_LIMIT_C}


def online_verify():
    """Повторне читання джерел (мережа через дозволений проксі; TLS не вимикається). Повертає [(url, ok|недоступно, деталь)]."""
    import urllib.request
    checks = [
        (PI_URL, ["1.6A (600mA if using a 3A power supply)", "4.63 V", "5.1V supply",
                  "| Raspberry Pi 2B | Raspberry Pi 3B | Raspberry Pi Zero | Raspberry Pi 4B"]),  # D11: the current table has no 3B+ column
        (THERMAL_URL, ["limit which we define as 85", "between 80", "There is currently no soft limit defined"]),  # D5: Pi 4 has no soft limit
        (THROT_URL, ["Soft temperature limit active", "Undervoltage detected"]),
        (NS3_URL, ["134365911.0", "47664215639.0", "1.0 / (2.0 * bValue)"]),
        ("https://www.mathworks.com/help/wlan/ug/802-11ac-receiver-minimum-input-sensitivity-test.html",
         ["[-82 -79 -77 -74 -70 -66 -65 -64 -59 -57]"]),
        (FSPL_URL, ["92.45"]),
    ]
    out = []
    for url, needles in checks:
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                txt = r.read().decode("utf-8", "replace")
            miss = [n for n in needles if n not in txt]
            out.append((url, "ok" if not miss else "РІЗНИЦЯ", "відсутні рядки: %s" % miss if miss else "усі %d рядків знайдено" % len(needles)))
        except Exception as e:  # noqa: BLE001 - мережа/політика: фіксуємо як недоступно, не обходимо
            out.append((url, "недоступно", "%s: %s" % (type(e).__name__, e)))
    return out


def report(online=False):
    out = ["## back-test проти зовнішніх даних (SRC перечитано 2026-10-03)"]
    rows = sens_rows(vht=True)
    s = sens_summary(rows)
    out.append("### B1 мінімальна чутливість (SRC %s)" % STD_URL)
    out.append("mcs,std_min_dBm,model_dBm(10%PER,NF=6,impl=3),model-std")
    for m, a, b, d in rows:
        out.append("%d,%d,%.1f,%+.1f" % (m, a, b, d))
    out.append("зсув модель-стандарт: %.1f..%.1f дБ (розкид %.1f дБ); найбільша помилка кроку між сусідніми MCS %.2f дБ; монотонність: %s"
               % (s["offset_min"], s["offset_max"], s["offset_spread"], s["step_err_max"], s["monotone"]))
    out.append("### B2 FSPL (SRC %s)" % FSPL_URL)
    for d, f, ref, mod in fspl_rows():
        out.append("%s m %s MHz: довідник %.3f дБ, модель %.3f дБ, Δ=%+.3f" % (d, f, ref, mod, mod - ref))
    out.append("### B3 ns-3 NistErrorRateModel (SRC %s): макс. відносна різниця BER/Pe = %.2e" % (NS3_URL, ns3_max_rel_diff()))
    out.append("### B4 Raspberry Pi (SRC %s)" % PI_URL)
    for name, ref, mod in pi_rows():
        out.append("%s: довідник %s, модель %s%s" % (name, ref, mod, "" if abs(ref - mod) < 1e-9 else "  <-- РІЗНИЦЯ"))
    out.append("### B5 get_throttled біти (SRC %s)" % THROT_URL)
    for b, doc, mod in throttled_rows():
        out.append("біт %d: довідник %r, модель %r%s" % (b, doc, mod, "" if doc == mod else "  <-- РІЗНИЦЯ"))
    sp = soc_prior_rows()
    out.append("### B6 термодросель (SRC %s)" % THERMAL_URL)
    out.append("довідник: прогресивний дросель 80..85 °C, жорсткий ліміт 85 °C; пріор hw.soc_soft_limit_c (%s): носій [%.1f, %.1f], медіана %.1f; P(>85 °C)=%.3f, P(<80 °C)=%.3f"
               % (sp["kind"], sp["lo"], sp["hi"], sp["median"], sp["p_above_hard_limit"], sp["p_below_doc_start"]))
    out.append("### Недоступно / не знайдено (нічого не вигадано)")
    for what, why in UNAVAILABLE:
        out.append("- %s: %s" % (what, why))
    if online:
        out.append("### Повторне читання джерел")
        for url, st, det in online_verify():
            out.append("- %s: %s (%s)%s" % (url, st, det, " [curl цей URL читав успішно; urllib без UA отримує 403, обхід не робився]" if st == "недоступно" and "403" in det else ""))
    return out


if __name__ == "__main__":
    print("\n".join(report(online="--online" in sys.argv[1:] or os.environ.get("VALIDATE_ONLINE") == "1")))
