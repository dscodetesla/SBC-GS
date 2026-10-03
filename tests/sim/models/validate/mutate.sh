#!/usr/bin/env bash
# Мутаційна перевірка тестів валідації: на КОПІЇ tests/sim/models (ніколи на місці) кожна мутація моделі мусить зламати
# validate/test_validate.py. Вивід: KILLED/SURVIVED на мутацію й підсумок; код виходу 0 лише якщо вбито всі.
#   validate/mutate.sh            усі мутації (приблизно 2-4 хв)
#   validate/mutate.sh V3 V7      лише названі
# Env: PY (python3 за замовчуванням). Копія пишеться в mktemp (працює і під nobody); у репозиторій нічого не пишеться.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$MODELS/../../.." && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET DEGRADE_PARAMS DEGRADE_MEASURED DEGRADE_SET VALIDATE_MODELS VALIDATE_LONG
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
want=("$@")

# id опис|файл (відносно models)|python-вираз, що змінює рядок s (текст файлу); мусить змінити текст
MUTS=(
"V1 знак втрат на трасі інвертовано|rf_model.py|s=s.replace('+ 10 * P.get(\"rf.path_loss_exponent\") * math.log10(d / d0)','- 10 * P.get(\"rf.path_loss_exponent\") * math.log10(d / d0)')"
"V2 ліміт USB Pi 5 для слабкого БЖ 0.6 -> 1.6|params.json|import re; s=re.sub(r'(\"usb_budget_weak_psu_a\": \{\s*\"value\": )0\.6', r'\g<1>1.6', s)"
"V3 шум без дрейфу (OU не оновлюється)|degrade_model.py|s=s.replace('self.x = self.x * a + self.sig * math.sqrt(1.0 - a * a) * self.rng.z()','self.x = self.x * a')"
"V4 P1dB зсунуто на 1 дБ (Rapp)|degrade_model.py|s=s.replace('dbm_to_mw(p1db_out_dbm + 1.0)','dbm_to_mw(p1db_out_dbm + 2.0)')"
"V5 стеля EVM неправильна (evm/20)|degrade_model.py|s=s.replace('+ 10.0 ** (evm / 10.0))','+ 10.0 ** (evm / 20.0))')"
"V6 теплова стала множиться, а не ділиться|degrade_model.py|s=s.replace('math.exp(-dt / self.tau)','math.exp(-dt * self.tau)',1)"
"V7 FEC: поріг невідновлення n-k -> n-k+1|rf_model.py|s=s.replace('need = n - k  #','need = n - k + 1  #')"
"V8 константа FSPL 32.44 -> 92.44|rf_model.py|s=s.replace('+ 32.44','+ 92.44')"
"V9 тепловий шум -174 -> -164 дБм/Гц|rf_model.py|s=s.replace('return -174.0 + 10 * math.log10','return -164.0 + 10 * math.log10')"
"V10 M/M/1/K: показник K -> K+1 у чисельнику|degrade_model.py|s=s.replace('return (1.0 - rho) * rho ** k / (1.0 - rho ** (k + 1))','return (1.0 - rho) * rho ** (k + 1) / (1.0 - rho ** (k + 1))')"
"V11 небезпека USB зростає із запасом (знак)|degrade_model.py|s=s.replace('x = -max(-30.0, v_margin_v) / v_scale - max(-30.0, i_margin_a) / i_scale','x = max(-30.0, v_margin_v) / v_scale + max(-30.0, i_margin_a) / i_scale')"
"V12 бюджет живлення: загальний струм занижено|power_model.py|s=s.replace('total = sum(a for _n, a, _c in items)','total = sum(a for _n, a, _c in items) - 0.1')"
"V13 дерейтинг PA від температури з протилежним знаком|degrade_model.py|s=s.replace('max(0.0, self.tj - self.ds) * self.dk','max(0.0, self.ds - self.tj) * self.dk')"
"V14 Gilbert: стаціонарна ймовірність p_gb без 1/(1-p)|rf_model.py|s=s.replace('p_gb = min(1.0, p_bg * p / (1.0 - p))','p_gb = min(1.0, p_bg * p)')"
"V15 антитетика вимкнена (u не дзеркалиться)|priors.py|s=s.replace('return 1.0 - x if self.anti else x','return x')"
"V16 провал живлення PA від протилежної напруги|degrade_model.py|s=s.replace('dv = max(0.0, knee_v - v)','dv = max(0.0, v - knee_v)')"
"V17 рушій бере ліміт USB 1.6 А завжди|degrade_model.py|s=s.replace('\"limit\": b_pk[\"usb_budget_a\"]','\"limit\": 1.6')"
"V18 затримка: радіо-доданок typ без паритету|latency_budget.py|s=s.replace('ppf * t * (1 + parity_per_pkt / 2)','ppf * t')"
"V19 availability ділиться на S-1 (вихід за [0,1])|degrade_model.py|s=s.replace('avail = sum(ok_l) / len(ok_l)','avail = sum(ok_l) / max(len(ok_l) - 1, 1)')"
"V20 поріг undervoltage 4.63 -> 4.40 В|params.json|import re; s=re.sub(r'(\"undervolt_threshold_v\": \{\s*\"value\": )4\.63', r'\g<1>4.4', s)"
"V21 SNR від шуму: інтерференція віднімається|rf_model.py|s=s.replace('n_mw += 10 ** (i / 10.0)','n_mw -= 10 ** (i / 10.0) * 0.5')"
"V22 PER не залежить від довжини кадру|rf_model.py|s=s.replace('return -math.expm1(8 * nbytes * math.log1p(-pe))','return -math.expm1(8 * 100 * math.log1p(-pe))')"
"V23 тепло AIR не залежить від ВЧ-потужності (D1)|degrade_model.py|s=s.replace('p_dc = p_idle + p_rf_w / eta','p_dc = p_idle + p_rf_ref_w / eta')"
"V24 виміряний струм більше не домінує над ККД-пріором: тепло не росте зі струмом (D1/D1b)|degrade_model.py|s=s.replace('max(1.0 - diss_frac, 1e-3, p_rf_ref_w / p_dc_ref if p_dc_ref > 0 else 1.0)','max(1.0 - diss_frac, 1e-3)')"
"V25 мертвий лінк знову дає margin -60 (D4)|degrade_model.py|s=s.replace('\"margin_db\": sum(mar_l) / len(mar_l) if mar_l else None','\"margin_db\": sum(mar_l) / len(mar_l) if mar_l else -60.0')"
"V26 усереднення завмирання втрачає хвіст глибоких завмирань (D9)|degrade_model.py|s=s.replace('_FADE_JLO, _FADE_JHI, _FADE_SUB = -240, 40, 16','_FADE_JLO, _FADE_JHI, _FADE_SUB = -24, 40, 16')"
"V27 Morris змішує перехід живий/мертвий з margin (D4)|scenario_engine.py|s=s.replace('if a is None or b is None:','if False:')"
"V28 вага Райса не нормована до одиничної середньої потужності (D9)|degrade_model.py|s=s.replace('    return tuple(x / tot for x in w)\n\n\n@functools.lru_cache(maxsize=64)\ndef fading_per_table','    return tuple(x / tot * 1.05 for x in w)\n\n\n@functools.lru_cache(maxsize=64)\ndef fading_per_table')"
)
fail=0
n=0
for m in "${MUTS[@]}"; do
	id="${m%% *}"
	if [ ${#want[@]} -gt 0 ]; then
		hit=0; for w in "${want[@]}"; do [ "$w" = "$id" ] && hit=1; done
		[ "$hit" = 1 ] || continue
	fi
	rest="${m#* }"; name="${rest%%|*}"; r2="${rest#*|}"; file="${r2%%|*}"; expr="${r2#*|}"
	n=$((n + 1))
	rm -rf "$tmp/c"; mkdir -p "$tmp/c/tests/sim" "$tmp/c/docs"
	cp -r "$MODELS" "$tmp/c/tests/sim/models"
	cp -r "$ROOT/gs" "$tmp/c/gs" 2>/dev/null
	cp "$ROOT"/docs/SIM-*.md "$tmp/c/docs/" 2>/dev/null
	find "$tmp/c" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
	if ! "$PY" - "$tmp/c/tests/sim/models/$file" "$expr" <<'PY'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p).read()
o = s
exec(expr)
if s == o:
    sys.exit("mutation did not change the file: " + p)
open(p, "w").write(s)
PY
	then
		echo "ERROR $id: mutation not applied ($name)"; fail=1; continue
	fi
	if (cd "$tmp/c/tests/sim/models/validate" && "$PY" test_validate.py -f >/dev/null 2>&1); then
		echo "SURVIVED $id: $name"; fail=1
	else
		echo "KILLED   $id: $name"
	fi
done
[ "$fail" = 0 ] && echo "validate mutation check: all $n mutations killed" || echo "validate mutation check: SURVIVORS or errors"
exit "$fail"
