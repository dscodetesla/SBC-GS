#!/usr/bin/env bash
# One command that mirrors the CI `lint` and `golden` jobs, so nothing can be forgotten before a commit.
#   tests/precommit.sh          lint + golden
#   tests/precommit.sh --quick  lint only (seconds)
# Exit status is non-zero if any step fails; every step is reported.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 2
quick=0; [ "${1:-}" = "--quick" ] && quick=1
fail=0
step() { # step <name> <command...>
	local name="$1"; shift
	if "$@" > /tmp/precommit.$$.out 2>&1; then echo "ok    $name"; else echo "FAIL  $name"; sed 's/^/      /' /tmp/precommit.$$.out | head -15; fail=1; fi
	rm -f /tmp/precommit.$$.out
}
# --- mirrors .github/workflows/ci.yml job `lint` (keep in sync) ---
step "shellcheck bench"                     bash -c 'cd bench && shellcheck -x -S warning *.sh'
step "py_compile"                           python3 -m py_compile bench/*.py gs/*.py tests/sim/*.py
step "shellcheck ratchet (gs/, build/)"     tests/shellcheck-ratchet.sh
step "shellcheck harness"                   shellcheck -x -S warning tests/*.sh tests/lib/*.sh tests/static/*.sh tests/sim/*.sh tests/sim/dkms/*.sh tests/sim/netfetch/*.sh tests/sim/powerlab/*.sh tests/sim/evdev/*.sh tests/sim/watchdog/*.sh tests/sim/models/*.sh tests/sim/models/validate/*.sh tests/sim/fuzz/*.sh tests/sim/twin/*.sh tests/sim/virt/*.sh tests/sim/virt/guest/*.sh build/lib/*.sh
find "$REPO" -name __pycache__ -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null
# --- mirrors job `golden` ---
[ "$quick" = 1 ] || step "golden + static (tests/run.sh)"  tests/run.sh
echo; [ "$fail" = 0 ] && echo "precommit: all green" || echo "precommit: FAILED"
exit "$fail"
