#!/usr/bin/env bash
# Mutation check of the scenario-engine tests, on a COPY of tests/sim/models (never in place): every mutation must make
# test_degrade.py (or test_models.py) FAIL. Prints one line per mutation and a summary; exit 0 only if all were caught.
#   tests/sim/models/mutate.sh            run all mutations (about 1-2 min)
#   tests/sim/models/mutate.sh M3 M7      only the named ones
# Env: PY (python3 default).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET DEGRADE_PARAMS DEGRADE_MEASURED DEGRADE_SET
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
want=("$@")

# id|file|python expression that edits `s` (the file text); must change the text
MUTS=(
"M1 remove PA compression (Pout = Plin)|degrade_model.py|s=s.replace('return mw_to_dbm(plin / (1.0 + (plin / ps) ** p) ** (1.0 / p))','return plin_dbm')"
"M2 flip the Poisson rate (shocks never happen)|priors.py|s=s.replace('return self.u() < -math.expm1(-rate_per_s * dt)','return self.u() < -math.expm1(-0.0 * rate_per_s * dt)')"
"M3 drop the 50 ms settle (debounce) in gs/button.sh model|params.degrade.json|import re; s=re.sub(r'(\"settle_s\": \{\s*\"value\": )0\.05', r'\g<1>0.0', s)"
"M4 EVM ceiling removed (SNR not capped)|degrade_model.py|s=s.replace('return -10.0 * math.log10(10.0 ** (-snr_db / 10.0) + 10.0 ** (evm / 10.0))','return snr_db')"
"M5 thermal shutdown never triggers|degrade_model.py|s=s.replace('if not self.shut and self.tj >= self.sd:','if not self.shut and self.tj >= self.sd + 1e9:')"
"M6 injection queue never blocks|degrade_model.py|s=s.replace('    if rho <= 0:\n        return 0.0\n    if abs(rho - 1.0)','    return 0.0\n    if abs(rho - 1.0)')"
"M7 UNMEASURED ratchet exceeded (one INF becomes UNMEASURED)|params.degrade.json|s=s.replace('\"provenance\": \"INF\"','\"provenance\": \"UNMEASURED\"',1)"
"M8 catalog probability not labelled SYNTH|scenarios/catalog.json|s=s.replace('\"provenance\": \"SYNTH\"','\"provenance\": \"INF\"',1)"
"M9 Pi 5 limiter never trips|degrade_model.py|s=s.replace('if i_a > self.limit * (1.0 + self.tol):','if i_a > self.limit * 1000.0:')"
"M10 USB hazard grows with margin (sign flip)|degrade_model.py|s=s.replace('x = -max(-30.0, v_margin_v) / v_scale - max(-30.0, i_margin_a) / i_scale','x = max(-30.0, v_margin_v) / v_scale + max(-30.0, i_margin_a) / i_scale')"
"M11 seed ignored (draws not reproducible per seed)|scenario_engine.py|s=s.replace('ru, rp = Rng(subseed(seed, j, 0), odd), Rng(subseed(seed, j, 1), odd)','ru, rp = Rng(subseed(0, j, 0), odd), Rng(subseed(0, j, 1), odd)')"
"M12 button long threshold 200 -> 100 cs|params.degrade.json|s=s.replace('\"value\": 200,','\"value\": 100,')"
"M13 debounce filter disabled|gpio_bounce.py|s=s.replace('    if window_s <= 0:\n        return list(trace)','    return list(trace)')"
"M14 bring-up never fails|degrade_model.py|s=s.replace('            if rng.u() >= p:','            if rng.u() >= 0.0 * p:')"
"M15 AIR heat independent of the radiated power (D1 restored)|degrade_model.py|s=s.replace('p_dc = p_idle + p_rf_w / eta','p_dc = p_idle + p_rf_ref_w / eta')"
"M16 AIR idle power may go negative (energy not conserved, D1b restored)|degrade_model.py|s=s.replace('p_idle = max(0.0, p_dc_ref - p_rf_ref_w / eta)','p_idle = p_dc_ref - p_rf_ref_w / eta')"
"M17 measured current no longer wins over the efficiency prior (heat flat in the current)|degrade_model.py|s=s.replace('max(1.0 - diss_frac, 1e-3, p_rf_ref_w / p_dc_ref if p_dc_ref > 0 else 1.0)','max(1.0 - diss_frac, 1e-3)')"
"M18 dead link reports the stub margin -60 dB again (D4 restored)|degrade_model.py|s=s.replace('\"margin_db\": sum(mar_l) / len(mar_l) if mar_l else None','\"margin_db\": sum(mar_l) / len(mar_l) if mar_l else -60.0')"
"M19 Morris takes the alive<->dead step as a margin effect|scenario_engine.py|s=s.replace('if a is None or b is None:','if False:')"
"M20 fading average loses the deep-fade tail (G >= -6 dB only, D9 restored)|degrade_model.py|s=s.replace('_FADE_JLO, _FADE_JHI, _FADE_SUB = -240, 40, 16','_FADE_JLO, _FADE_JHI, _FADE_SUB = -24, 40, 16')"
"M21 fading average = 32 equiprobable quantiles again (D9 restored)|degrade_model.py|s=s.replace('        w.append(m)\n    tot = sum(w)','        w.append(m if j % 8 == 0 else 0.0)\n    tot = sum(w)')"
"M22 dead flag lost (margin None but dead False)|degrade_model.py|s=s.replace('\"dead\": not mar_l,','\"dead\": False,')"
)
fail=0
for m in "${MUTS[@]}"; do
	id="${m%% *}"
	if [ ${#want[@]} -gt 0 ]; then
		hit=0; for w in "${want[@]}"; do [ "$w" = "$id" ] && hit=1; done
		[ "$hit" = 1 ] || continue
	fi
	rest="${m#* }"; name="${rest%%|*}"; r2="${rest#*|}"; file="${r2%%|*}"; expr="${r2#*|}"
	rm -rf "$tmp/c"; mkdir -p "$tmp/c/tests/sim"
	cp -r "$HERE" "$tmp/c/tests/sim/models"
	cp -r "$HERE/../../../gs" "$tmp/c/gs" 2>/dev/null; mkdir -p "$tmp/c/docs"; cp "$HERE"/../../../docs/SIM-*.md "$tmp/c/docs/" 2>/dev/null
	rm -rf "$tmp/c/tests/sim/models/__pycache__"
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
	if (cd "$tmp/c/tests/sim/models" && "$PY" test_degrade.py >/dev/null 2>&1 && "$PY" test_models.py >/dev/null 2>&1); then
		echo "SURVIVED $id: $name"; fail=1
	else
		echo "KILLED   $id: $name"
	fi
done
[ "$fail" = 0 ] && echo "mutation check: all mutations killed" || echo "mutation check: SURVIVORS or errors"
exit "$fail"
