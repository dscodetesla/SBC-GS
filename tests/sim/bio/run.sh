#!/usr/bin/env bash
# Biomimetic-inspired prototypes (lesion sweep + failsafe DES): no network, no root, < 5 s.
#   run.sh --check          unit/property/cross-check/golden tests
#   run.sh --mutate         mutation check on a copy (every mutant must be killed)
#   run.sh --update-golden  rewrite golden/*.txt after an INTENDED change
#   run.sh                  tests, then print both reports
# Env: PY (python3 default). Exit: 0 pass, 1 fail, 77 skipped (no python3).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP bio: $PY not found"; exit 77; }
case "${1:-}" in
	--check) "$PY" "$HERE/test_bio.py" ;;
	--mutate) "$PY" "$HERE/mutate.py" ;;
	--update-golden) UPDATE_GOLDEN=1 "$PY" "$HERE/test_bio.py" >/dev/null 2>&1; "$PY" "$HERE/test_bio.py" && echo "bio: golden updated" ;;
	"") "$PY" "$HERE/test_bio.py" || exit 1; "$PY" "$HERE/contour_graph.py"; "$PY" "$HERE/failsafe_des.py" ;;
	*) echo "usage: run.sh [--check|--mutate|--update-golden]" >&2; exit 2 ;;
esac
