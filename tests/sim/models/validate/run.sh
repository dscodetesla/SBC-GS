#!/usr/bin/env bash
# Валідація моделей SBC-GS без заліза: фізичні інваріанти, крос-модельна узгодженість, статистика, back-test, структурні альтернативи.
#   run.sh --check   py_compile + test_validate.py (unittest, < 15 с, без мережі, без root); код виходу 0/1, 77 = немає python3
#   run.sh --long    --check з великими вибірками (VALIDATE_LONG=1) + studies.py all + backtest.py (мережа лише якщо VALIDATE_ONLINE=1)
#   run.sh           те саме, що --check
# Env: PY (python3 за замовчуванням), VALIDATE_MODELS (каталог моделей; для mutate.sh на копії).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP validate: $PY not found"; exit 77; }
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET DEGRADE_PARAMS DEGRADE_MEASURED DEGRADE_SET
mode="${1:---check}"
case "$mode" in
	--check|--long) ;;
	*) echo "usage: run.sh [--check|--long]" >&2; exit 2 ;;
esac
for f in "$HERE"/*.py; do
	"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL validate: syntax $f"; exit 1; }
done
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if [ "$mode" = "--long" ]; then export VALIDATE_LONG=1; fi
if (cd "$HERE" && "$PY" test_validate.py >"$log" 2>&1); then
	echo "PASS validate: $(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$log") tests ($mode)"
else
	cat "$log"
	echo "FAIL validate"
	exit 1
fi
if [ "$mode" = "--long" ]; then
	(cd "$HERE" && "$PY" backtest.py)
	(cd "$HERE" && "$PY" studies.py all)
	for s in pi5_3a_weak_psu hot_day_closed_case; do
		(cd "$HERE" && "$PY" studies.py structural "--scn=$s")
	done
fi
exit 0
