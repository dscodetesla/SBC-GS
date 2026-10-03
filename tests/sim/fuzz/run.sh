#!/usr/bin/env bash
# Deterministic fuzz / property / fault-injection layer for the REAL SBC-GS scripts (no hardware, no network, stdlib only).
#   tests/sim/fuzz/run.sh --check          fast mode (budget < 15 s on 4 cores): FUZZ_ITERS=1
#   tests/sim/fuzz/run.sh --long           more iterations (FUZZ_ITERS=8 unless set), a few minutes
#   tests/sim/fuzz/run.sh --check --strict-defects   assert the pinned REPO defects as FIXED (fails today; see docs/SIM-FUZZ.md)
#   tests/sim/fuzz/run.sh --check -v       verbose per-test lines
# Env: FUZZ_SEED (default 20261003), FUZZ_ITERS (iteration scale), FUZZ_PY (python with pymavlink for the bridge tests),
#      FUZZ_REPO (repository root to test; mutate.sh points it at a COPY), PY (python3 default), FUZZ_SHARDS=0 (run in one process).
# Exit: 0 pass, 1 fail, 77 skipped (no python3).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP fuzz: $PY not found"; exit 77; }

mode="" verbose="" strict=0
for a in "$@"; do
	case "$a" in
		--check|--long) mode="$a" ;;
		--strict-defects) strict=1 ;;
		-v) verbose="-v" ;;
		*) echo "usage: run.sh --check|--long [--strict-defects] [-v]" >&2; exit 2 ;;
	esac
done
[ -n "$mode" ] || { echo "usage: run.sh --check|--long [--strict-defects] [-v]" >&2; exit 2; }
if [ "$mode" = --long ]; then export FUZZ_ITERS="${FUZZ_ITERS:-8}"; else export FUZZ_ITERS="${FUZZ_ITERS:-1}"; fi
[ "$strict" = 1 ] && export FUZZ_STRICT_DEFECTS=1
export FUZZ_SEED="${FUZZ_SEED:-20261003}"

for f in "$HERE"/*.py; do
	"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL fuzz: syntax $f"; exit 1; }
done
for f in "$HERE"/*.sh; do bash -n "$f" || { echo "FAIL fuzz: bash -n $f"; exit 1; }; done

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
# shards = groups of test classes with similar run time; they run in parallel (each also parallelises its subprocesses)
shards=(
	"TestBridgeFaults TestBridgeInputValidation TestConfigPinnedDefects TestFetch"
	"TestApplyconfFaults TestModelProperties TestFanFaults"
	"TestConfigDifferential TestConfigResolveDifferential TestBoard"
	"TestConfigInjection TestGsMavlink"
	"TestStreamMissingTools TestUdevRender"
)
[ "${FUZZ_SHARDS:-1}" = 0 ] && shards=("")
t0=$SECONDS
pids=()
for i in "${!shards[@]}"; do
	# shellcheck disable=SC2086  # intentional word splitting of the class list
	FUZZ_REPORT="$tmp/rep$i.json" "$PY" "$HERE/test_fuzz.py" $verbose ${shards[$i]} > "$tmp/out$i.log" 2>&1 &
	pids+=($!)
done
fail=0
for i in "${!pids[@]}"; do wait "${pids[$i]}" || fail=1; done

n="$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$tmp"/out*.log | awk '{s+=$1} END {print s+0}')"
skipped="$(grep -ho 'skipped=[0-9]*' "$tmp"/out*.log | sed 's/skipped=//' | awk '{s+=$1} END {print s+0}')"
if [ "$fail" != 0 ]; then
	for i in "${!shards[@]}"; do grep -v '^sbc-gs-config: WARNING' "$tmp/out$i.log" | grep -v 'ResourceWarning\|^  ' ; done
	echo "FAIL fuzz: $n tests ($((SECONDS - t0)) s, seed $FUZZ_SEED, iters x$FUZZ_ITERS)"
	exit 1
fi
defects="$("$PY" - "$tmp"/rep*.json <<'PYEOF'
import json, sys
seen = {}
for p in sys.argv[1:]:
    seen.update(json.load(open(p)).get("defects", {}))
print(" ".join(sorted(k for k, v in seen.items() if v)))
PYEOF
)"
echo "PASS fuzz: $n tests ($skipped skipped), $((SECONDS - t0)) s, seed $FUZZ_SEED, iterations x$FUZZ_ITERS; pinned REPO defects still present: ${defects:-none}"
[ -z "$verbose" ] || cat "$tmp"/out*.log
exit 0
