#!/usr/bin/env bash
# Batched shell side of the config differential fuzz (one bash process for many cases; the real loader config/load.sh is SOURCED
# unchanged). Usage:  cfg_harness.sh <repo> parse [--owner O] FILE...      -> per file: "@@ <file> <rc>" then "KEY<TAB>VALUE" lines
#                     cfg_harness.sh <repo> check CASEFILE                  -> per line "KEY<TAB>VALUE": "<rc><TAB><SBC_ERR>"
#                     cfg_harness.sh <repo> resolve CASEFILE                -> per line "regfile<TAB>ik<TAB>KEY<TAB>VALUE": "<rc><TAB><value><TAB><layer><TAB><warn><TAB><err>"
set -u
repo="$1"; mode="$2"; shift 2
# shellcheck source=/dev/null
. "$repo/config/load.sh"
[ "$mode" = resolve ] || sbc_cfg_registry
errf="$(mktemp)"; trap 'rm -f "$errf"' EXIT
case "$mode" in
	parse)
		owner=""
		if [ "${1:-}" = --owner ]; then owner="$2"; shift 2; fi
		for f in "$@"; do
			out="$(sbc_cfg_parse "$f" "$owner" 2>/dev/null)"; rc=$?
			printf '@@ %s %s\n' "$f" "$rc"
			[ -z "$out" ] || printf '%s\n' "$out"
		done ;;
	check)
		while IFS=$'\t' read -r k v; do
			rc=0; sbc_cfg_check "$k" "$v" || rc=$?
			printf '%s\t%s\n' "$rc" "$SBC_ERR"
		done < "$1" ;;
	resolve)
		while IFS=$'\t' read -r reg ik k v; do
			(
				SBC_CFG_REGISTRY="$reg"; export SBC_CFG_REGISTRY
				export "SBC_GS_$k=$v" SBC_GS_CONFIG=/nonexistent/fuzz.env SBC_GS_PROFILE=""
				unset SBC_GS_I_KNOW
				args=(); [ "$ik" = 1 ] && args+=(--i-know)
				# the EXIT trap prints the verdict both after a normal return and after sbc_die's `exit 2`
				trap 'rc=$?; printf "%s\t%s\t%s\t%s\t%s\n" "$rc" "${SBC_V[$k]:-}" "${SBC_L[$k]:-}" "$(grep -c "SAFETY OVERRIDE" "$errf")" "$(head -n1 "$errf" | tr "\t" " ")"' EXIT
				sbc_cfg_resolve "${args[@]}" 2>"$errf"
			)
		done < "$1" ;;
	*) echo "usage" >&2; exit 64 ;;
esac
