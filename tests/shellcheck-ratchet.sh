#!/usr/bin/env bash
# Ratchet for shellcheck findings in the legacy scripts (gs/ and build/):
# the per-file, per-code count may only go DOWN. New findings fail; fixed ones
# must be locked in with --update. Needs: shellcheck.
#   tests/shellcheck-ratchet.sh            check against tests/shellcheck-baseline.txt
#   tests/shellcheck-ratchet.sh --update   rewrite the baseline (after fixing findings)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
BASE="$HERE/shellcheck-baseline.txt"
cd "$REPO" || exit 2

current() {  # "<file> <SCcode> <count>", sorted
	shellcheck -x -S warning -f gcc gs/*.sh build/*.sh 2>/dev/null \
		| sed -nE 's#^([^:]+):[0-9]+:[0-9]+: [a-z]+: .*\[(SC[0-9]+)\]$#\1 \2#p' \
		| LC_ALL=C sort | uniq -c | awk '{print $2" "$3" "$1}'
}

if [ "${1:-}" = "--update" ]; then current > "$BASE"; echo "baseline updated: $(awk '{s+=$3} END{print s}' "$BASE") findings in $(wc -l < "$BASE") (file,code) groups"; exit 0; fi
[ -f "$BASE" ] || { echo "missing $BASE (run with --update)"; exit 2; }

cur="$(mktemp)"; trap 'rm -f "$cur"' EXIT
current > "$cur"      # run shellcheck once
fail=0
# findings above the baseline fail the check
awk 'NR==FNR {b[$1" "$2]=$3; next}
     {k=$1" "$2; if ($3 > b[k]+0) {printf "NEW     %s %s: %d (baseline %d)\n", $1, $2, $3, b[k]+0; bad=1}}
     END {exit bad}' "$BASE" "$cur" || fail=1
# improvements are reported so they get locked in
awk 'NR==FNR {c[$1" "$2]=$3; next}
     {k=$1" "$2; if (c[k]+0 < $3) printf "better  %s %s: %d (baseline %d), run --update to lock in\n", $1, $2, c[k]+0, $3}' "$cur" "$BASE"
[ "$fail" = 0 ] && echo "shellcheck ratchet: ok (no new findings)"
exit $fail
