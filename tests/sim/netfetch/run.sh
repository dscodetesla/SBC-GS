#!/usr/bin/env bash
# build/lib/fetch.sh against a REAL curl and a deterministic loopback HTTPS server (no external network, no root, stdlib + openssl CLI).
#   tests/sim/netfetch/run.sh --check         fast mode (budget < 15 s, 9 parallel shards)
#   tests/sim/netfetch/run.sh --long          adds the slow scenarios (full retry chains, burst-then-silence) and 250 random scenarios
#   tests/sim/netfetch/run.sh --check -v      verbose per-test lines
# Env: PY (python3 default), NETFETCH_REPO (repository root to test; mutate.sh points it at a COPY), NETFETCH_SEED, NETFETCH_ITERS.
# Exit: 0 pass, 1 fail, 77 skipped (python3, curl or openssl missing). Details: docs/SIM-FETCH.md
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
for t in "$PY" curl openssl sha256sum; do
	command -v "$t" >/dev/null 2>&1 || { echo "SKIP netfetch: $t not found"; exit 77; }
done

mode="" verbose=""
for a in "$@"; do
	case "$a" in
		--check|--long) mode="$a" ;;
		-v) verbose="-v" ;;
		*) echo "usage: run.sh --check|--long [-v]" >&2; exit 2 ;;
	esac
done
[ -n "$mode" ] || { echo "usage: run.sh --check|--long [-v]" >&2; exit 2; }
if [ "$mode" = --long ]; then export NETFETCH_LONG=1; else export NETFETCH_LONG=0; fi

for f in "$HERE"/*.py; do
	"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL netfetch: syntax $f"; exit 1; }
done
for f in "$HERE"/*.sh; do bash -n "$f" || { echo "FAIL netfetch: bash -n $f"; exit 1; }; done

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
# shards = groups of test classes of similar run time (the slow ones sleep in retry pauses / stall timeouts); they run in parallel
shards=(
	"TestHttpRetryChain"
	"TestStall TestUnpinned"
	"TestStallRetry"
	"TestTransferFaults TestTls"
	"TestRedirects"
	"TestHttpErrors TestIdempotence"
	"TestBasics"
	"TestSignals"
	"TestRandomized"
)
t0=$SECONDS
pids=()
for i in "${!shards[@]}"; do
	# shellcheck disable=SC2086  # intentional word splitting of the class list
	"$PY" "$HERE/test_netfetch.py" $verbose ${shards[$i]} > "$tmp/out$i.log" 2>&1 &
	pids+=($!)
done
fail=0
for i in "${!pids[@]}"; do wait "${pids[$i]}" || fail=1; done

n="$(sed -n 's/^Ran \([0-9]*\) tests\? in.*/\1/p' "$tmp"/out*.log | awk '{s+=$1} END {print s+0}')"
skipped="$(grep -ho 'skipped=[0-9]*' "$tmp"/out*.log | sed 's/skipped=//' | awk '{s+=$1} END {print s+0}')"
if [ "$fail" != 0 ]; then
	for i in "${!shards[@]}"; do cat "$tmp/out$i.log"; done
	echo "FAIL netfetch: $n tests ($((SECONDS - t0)) s, mode $mode)"
	exit 1
fi
[ -z "$verbose" ] || cat "$tmp"/out*.log
echo "PASS netfetch: $n tests ($skipped skipped), $((SECONDS - t0)) s, mode $mode"
exit 0
