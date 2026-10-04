#!/usr/bin/env bash
# FC-loss watchdog of gs-mavlink (gs/mavlink/gs-mavlink-watchdog.*): unit tests + (with a real mavp2p) the chain FC emulator -> router -> watchdog.
#   tests/sim/watchdog/run.sh --check    ~25 s; the router chain is skipped (reported) when no mavp2p binary is found (MAVP2P=<path> or
#                                        ~/.cache/sbc-gs-sim/bin/mavp2p or PATH). Exit 0 pass, 1 fail.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
case "${1:---check}" in --check) ;; *) echo "usage: run.sh [--check]" >&2; exit 2 ;; esac
log="$(mktemp)"; trap 'rm -f "$log"' EXIT
"$PY" -m py_compile "$HERE/../../../gs/mavlink/gs-mavlink-watchdog.py" "$HERE/test_watchdog.py" || { echo "FAIL watchdog: py_compile"; exit 1; }
if "$PY" "$HERE/test_watchdog.py" >"$log" 2>&1; then
	echo "PASS watchdog: $(grep -E '^Ran ' "$log" | tail -1), $(grep -E '^OK' "$log" | tail -1)"
else
	tail -n 30 "$log"; echo "FAIL watchdog"; exit 1
fi
