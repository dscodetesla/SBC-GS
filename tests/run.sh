#!/usr/bin/env bash
# shellcheck disable=SC2034  # case variables (invocation, sleep_limit, dump_*) are read by lib/sandbox.sh
# Golden-output regression runner for gs/*.sh (no hardware needed).
#   tests/run.sh                 compare against tests/golden
#   tests/run.sh --update [case] rewrite golden output (do this ONLY on intended changes)
#   tests/run.sh <suite>/<case>  run one case, e.g. applyconf/default
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib/sandbox.sh"

update=0; [ "${1:-}" = "--update" ] && { update=1; shift; }
want="${1:-}"
fail=0; ran=0

for casefile in "$HERE"/cases/*/*.sh; do
	suite="$(basename "$(dirname "$casefile")")"; name="$(basename "$casefile" .sh)"
	[ -n "$want" ] && [ "$want" != "$suite/$name" ] && continue
	script_under_test=""
	invocation=sourced; sleep_limit=0; dump_baseline=1; dump_files=(); dump_trees=()
	case_setup() { :; }
	# shellcheck disable=SC1090
	. "$casefile"
	sb_new; case_setup; sb_run "$script_under_test"
	out="$(mktemp)"; sb_dump > "$out"; sb_clean
	golden="$HERE/golden/$suite/$name.out"
	ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  $suite/$name"
	elif [ ! -f "$golden" ]; then echo "MISSING  $suite/$name (run: tests/run.sh --update $suite/$name)"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       $suite/$name"
	else echo "DIFF     $suite/$name"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
done
[ "$ran" -gt 0 ] || { echo "no cases matched"; exit 2; }
exit $fail
