#!/usr/bin/env bash
# shellcheck disable=SC2034  # registry arrays and SBC_CFG_SRC_* are read by sourcing scripts and sbc-gs-config
# SBC-GS layered configuration loader (shell). SOURCE this file; do not execute it. Design: docs/CONFIG.md.
#
# Precedence (high to low):  CLI flag > environment > per-host file > profile file > built-in default.
#   environment : the variable named in registry column "env" (SBC_GS_<KEY> unless the column names another one);
#                 an EMPTY variable counts as unset (same as the legacy ${VAR:=default} idiom).
#   extra file  : an optional script-specific file (gs-mavlink.conf), ranks just above the per-host file.
#   per-host    : $SBC_GS_CONFIG, default /config/sbc-gs.env
#   profile     : $SBC_GS_PROFILE_DIR (default <this dir>/profiles)/$SBC_GS_PROFILE.env
# Files are parsed as DATA (strict KEY=VALUE, quotes only); they are NEVER sourced or evaluated.
# Every value is validated against config/registry.tsv; unknown keys are rejected loudly.
# SAFETY keys have hard bounds that only SBC_GS_I_KNOW=1 (or the --i-know option) relaxes, with a loud warning.
#
#   . /path/to/config/load.sh
#   sbc_cfg_load [--i-know] [--no-value-check] [--extra-file FILE --extra-owner OWNER] OWNER...
# sets the shell variables <KEY> (and SBC_CFG_SRC_<KEY> = layer name) for every key owned by OWNER...
# Errors: message on stderr prefixed "sbc-gs-config:", exit status 2 (the whole shell exits, scripts are fail-closed).
# --no-value-check skips value validation (syntax and key checks stay); for scripts whose own validators are authoritative.

SBC_CFG_DIR="${SBC_CFG_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

declare -gA SBC_R_DEF=() SBC_R_TYPE=() SBC_R_MIN=() SBC_R_MAX=() SBC_R_UNIT=() SBC_R_SAFE=() SBC_R_OWN=() SBC_R_ENV=()
declare -ga SBC_R_KEYS=()
declare -gA SBC_V=() SBC_L=()   # effective value and layer per key (after sbc_cfg_resolve)

sbc_die() { echo "sbc-gs-config: error: $*" >&2; exit 2; }
sbc_warn() { echo "sbc-gs-config: $*" >&2; }

# --- registry -------------------------------------------------------------------------------------------------
sbc_cfg_registry() {
	[ "${#SBC_R_KEYS[@]}" -eq 0 ] || return 0
	local f="${SBC_CFG_REGISTRY:-$SBC_CFG_DIR/registry.tsv}" k d t mn mx u s o e desc
	[ -r "$f" ] || sbc_die "cannot read registry $f"
	while IFS=$'\t' read -r k d t mn mx u s o e desc; do
		case "$k" in ''|'#'*) continue ;; esac
		[ -n "$desc" ] || sbc_die "registry row for '$k' has fewer than 10 fields"
		[ -z "${SBC_R_TYPE[$k]+x}" ] || sbc_die "registry key '$k' is defined twice"
		[ "$d" = '~' ] && d=''
		SBC_R_DEF[$k]="$d"; SBC_R_TYPE[$k]="$t"; SBC_R_MIN[$k]="$mn"; SBC_R_MAX[$k]="$mx"
		SBC_R_UNIT[$k]="$u"; SBC_R_SAFE[$k]="$s"; SBC_R_OWN[$k]="$o"
		[ "$e" = '-' ] && e="SBC_GS_$k"
		SBC_R_ENV[$k]="$e"
		SBC_R_KEYS+=("$k")
	done < "$f"
	[ "${#SBC_R_KEYS[@]}" -gt 0 ] || sbc_die "registry $f is empty"
}

# --- strict parser (data only) --------------------------------------------------------------------------------
# sbc_cfg_parse FILE [OWNER]  -> prints "KEY<TAB>VALUE" lines (syntax and key names validated; values NOT yet).
# Runs in a command substitution: callers must test its exit status (sbc_die only ends that subshell).
sbc_cfg_parse() {
	local file="$1" owner="${2:-}" line n=0 key val tmp
	local sq="'" bt='`' dq='"' re seen=" "
	local LC_ALL=C   # byte semantics: [[:cntrl:]] and [[:space:]] must not depend on the caller's locale (D2); python checks ASCII controls only
	re="^[[:space:]]*([A-Z][A-Z0-9_]*)=(${sq}([^${sq}\$${bt}\\\\]*)${sq}|${dq}([^${dq}\$${bt}\\\\]*)${dq}|([A-Za-z0-9._:/@%+,-]*))([[:space:]]+#.*)?[[:space:]]*\$"
	[ -r "$file" ] || sbc_die "cannot read $file"
	# bash `read` silently drops NUL bytes (3<NUL>00 would be read as 300); python rejects them (D1): reject the whole file
	tr -d '\000' <"$file" | cmp -s - "$file" || sbc_die "$file: NUL byte in file (control character)"
	while IFS= read -r line || [ -n "$line" ]; do
		n=$((n + 1))
		line="${line%$'\r'}"
		[ "$n" -eq 1 ] && line="${line#$'\xef\xbb\xbf'}"
		if [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]]; then continue; fi
		tmp="${line//$'\t'/ }"
		[[ "$tmp" =~ [[:cntrl:]] ]] && sbc_die "$file:$n: control character in line"
		[[ "$line" =~ $re ]] || sbc_die "$file:$n: not a KEY=VALUE line (only KEY=VALUE, KEY='value' or KEY=\"value\"; no \$, backticks, backslashes, ';', '(' or other shell syntax): ${line:0:60}"
		key="${BASH_REMATCH[1]}"
		case "${BASH_REMATCH[2]:0:1}" in
			"'") val="${BASH_REMATCH[3]}" ;;
			'"') val="${BASH_REMATCH[4]}" ;;
			*) val="${BASH_REMATCH[5]}" ;;
		esac
		[ -n "${SBC_R_TYPE[$key]+x}" ] || sbc_die "$file:$n: unknown key '$key' (not in registry.tsv)"
		if [ -n "$owner" ]; then
			case ",${SBC_R_OWN[$key]}," in *",$owner,"*) ;; *) sbc_die "$file:$n: key '$key' does not belong to $owner" ;; esac
		fi
		case "$seen" in *" $key "*) sbc_die "$file:$n: key '$key' is set twice" ;; esac
		seen="$seen$key "
		printf '%s\t%s\n' "$key" "$val"
	done < "$file"
}

# --- validation -----------------------------------------------------------------------------------------------
# sbc_cfg_check KEY VALUE  -> returns 0 ok; 1 type/format error (message in SBC_ERR); 2 hard-bound violation of a safety key
# (2 means "type is fine, only the bounds are exceeded", so that --i-know can relax exactly that).
sbc_cfg_check() {
	local k="$1" v="$2" t="${SBC_R_TYPE[$1]}" mn="${SBC_R_MIN[$1]}" mx="${SBC_R_MAX[$1]}" empty_ok=0 o
	SBC_ERR=""
	case "$t" in *\?) empty_ok=1; t="${t%\?}" ;; esac
	if [ -z "$v" ]; then
		if [ "$empty_ok" = 1 ] || [ "$t" = str ]; then return 0; fi
		SBC_ERR="$k is empty but type $t needs a value"; return 1
	fi
	case "$t" in
		int|port)
			[[ "$v" =~ ^-?[0-9]{1,15}$ ]] || { SBC_ERR="$k='$v' is not an integer"; return 1; }
			[ "$t" = port ] && { [ "$v" -ge 1 ] && [ "$v" -le 65535 ] || { SBC_ERR="$k='$v' is not a port in 1..65535"; return 1; }; }
			;;
		float)
			[[ "$v" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && [ "${#v}" -le 20 ] || { SBC_ERR="$k='$v' is not a decimal number"; return 1; }
			;;
		ip)
			[[ "$v" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || { SBC_ERR="$k='$v' is not an IPv4 address"; return 1; }
			for o in 1 2 3 4; do [ $((10#${BASH_REMATCH[o]})) -le 255 ] || { SBC_ERR="$k='$v' is not an IPv4 address"; return 1; }; done
			return 0 ;;
		path)
			[[ "$v" =~ ^/[A-Za-z0-9._/%:+@,-]*$ ]] && [[ "$v" != *'/../'* ]] && [[ "$v" != */.. ]] || { SBC_ERR="$k='$v' is not a safe absolute path"; return 1; }
			return 0 ;;
		bool)
			case "$v" in 0|1) return 0 ;; *) SBC_ERR="$k='$v' must be 0 or 1"; return 1 ;; esac ;;
		enum:*)
			local -a alts=(); local alt
			IFS='|' read -ra alts <<<"${t#enum:}"
			for alt in "${alts[@]}"; do [ "$v" = "$alt" ] && return 0; done   # compare with each alternative: "a|b" must not match enum:a|b (D3)
			SBC_ERR="$k='$v' must be one of ${t#enum:}"; return 1 ;;
		str) return 0 ;;
		*) SBC_ERR="registry type '$t' of $k is unknown"; return 1 ;;
	esac
	# numeric bounds (int, port, float)
	if [ "$mn" != '-' ] && ! awk -v v="$v" -v b="$mn" 'BEGIN{exit !(v+0 >= b+0)}'; then
		SBC_ERR="$k=$v is below the minimum $mn"; [ "${SBC_R_SAFE[$k]}" = y ] && return 2; return 1
	fi
	if [ "$mx" != '-' ] && ! awk -v v="$v" -v b="$mx" 'BEGIN{exit !(v+0 <= b+0)}'; then
		SBC_ERR="$k=$v is above the maximum $mx"; [ "${SBC_R_SAFE[$k]}" = y ] && return 2; return 1
	fi
	return 0
}

# --- resolution -----------------------------------------------------------------------------------------------
# sbc_cfg_resolve [--i-know] [--no-value-check] [--extra-file F --extra-owner O] [--defaults-only] [OWNER...]
# fills SBC_V / SBC_L for the keys of OWNERs (all keys when none given).
sbc_cfg_resolve() {
	local ik=0 nocheck=0 extra="" extra_owner="" defaults_only=0 owners=() k v rc layer src
	while [ "$#" -gt 0 ]; do
		case "$1" in
			--i-know) ik=1 ;;
			--no-value-check) nocheck=1 ;;
			--defaults-only) defaults_only=1 ;;
			--extra-file) extra="$2"; shift ;;
			--extra-owner) extra_owner="$2"; shift ;;
			*) owners+=("$1") ;;
		esac
		shift
	done
	[ "${SBC_GS_I_KNOW:-0}" = 1 ] && ik=1
	sbc_cfg_registry
	SBC_V=(); SBC_L=()
	local -A prof=() host=() ext=()
	local plab="" hlab="" elab="" pfile="" hfile="" name out
	if [ "$defaults_only" = 0 ]; then
		name="${SBC_GS_PROFILE:-}"
		if [ -n "$name" ]; then
			[[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || sbc_die "SBC_GS_PROFILE='$name' must match [A-Za-z0-9_-]+"
			pfile="${SBC_GS_PROFILE_DIR:-$SBC_CFG_DIR/profiles}/$name.env"
			[ -f "$pfile" ] || sbc_die "profile '$name' not found ($pfile)"
			out="$(sbc_cfg_parse "$pfile")" || exit 2
			while IFS=$'\t' read -r k v; do [ -n "$k" ] && prof[$k]="$v"; done <<<"$out"
			plab="profile:$name"
		fi
		hfile="${SBC_GS_CONFIG:-/config/sbc-gs.env}"
		if [ -f "$hfile" ]; then
			out="$(sbc_cfg_parse "$hfile")" || exit 2
			while IFS=$'\t' read -r k v; do [ -n "$k" ] && host[$k]="$v"; done <<<"$out"
			hlab="host:$hfile"
		elif [ -e "$hfile" ]; then sbc_die "cannot read $hfile"
		fi
		if [ -n "$extra" ] && [ -f "$extra" ]; then
			out="$(sbc_cfg_parse "$extra" "$extra_owner")" || exit 2
			while IFS=$'\t' read -r k v; do [ -n "$k" ] && ext[$k]="$v"; done <<<"$out"
			elab="file:$extra"
		elif [ -n "$extra" ] && [ -e "$extra" ]; then sbc_die "cannot read $extra"
		fi
	fi
	for k in "${SBC_R_KEYS[@]}"; do
		if [ "${#owners[@]}" -gt 0 ]; then
			local hit=0 o
			for o in "${owners[@]}"; do case ",${SBC_R_OWN[$k]}," in *",$o,"*) hit=1 ;; esac; done
			[ "$hit" = 1 ] || continue
		fi
		v="${SBC_R_DEF[$k]}"; layer=default
		if [ "$defaults_only" = 0 ]; then
			[ -n "${prof[$k]+x}" ] && { v="${prof[$k]}"; layer="$plab"; }
			[ -n "${host[$k]+x}" ] && { v="${host[$k]}"; layer="$hlab"; }
			[ -n "${ext[$k]+x}" ] && { v="${ext[$k]}"; layer="$elab"; }
			src="${SBC_R_ENV[$k]}"
			if [ -n "${!src:-}" ]; then v="${!src}"; layer="env:$src"; fi
		fi
		if [ "$nocheck" = 0 ] || [ "$layer" = default ]; then
			rc=0; sbc_cfg_check "$k" "$v" || rc=$?
			if [ "$rc" = 2 ] && [ "$ik" = 1 ]; then
				sbc_warn "WARNING: SAFETY OVERRIDE (SBC_GS_I_KNOW/--i-know): $SBC_ERR (hard bounds ${SBC_R_MIN[$k]}..${SBC_R_MAX[$k]}, from $layer); accepted, YOU own the consequences"
			elif [ "$rc" != 0 ]; then
				[ "$rc" = 2 ] && SBC_ERR="$SBC_ERR (safety key, hard bounds ${SBC_R_MIN[$k]}..${SBC_R_MAX[$k]}; only SBC_GS_I_KNOW=1 or --i-know relaxes this)"
				sbc_die "$SBC_ERR [$layer]"
			fi
		fi
		SBC_V[$k]="$v"; SBC_L[$k]="$layer"
	done
}

# sbc_cfg_load ... OWNER...: resolve and export the variables <KEY> and SBC_CFG_SRC_<KEY> into the calling shell.
sbc_cfg_load() {
	sbc_cfg_resolve "$@"
	local k
	for k in "${!SBC_V[@]}"; do
		printf -v "$k" '%s' "${SBC_V[$k]}"
		printf -v "SBC_CFG_SRC_$k" '%s' "${SBC_L[$k]}"
	done
}

# sbc_cfg_show: effective config, registry order: KEY=VALUE <TAB># layer [SAFETY]
sbc_cfg_show() {
	local k
	for k in "${SBC_R_KEYS[@]}"; do
		[ -n "${SBC_V[$k]+x}" ] || continue
		printf '%s=%s\t# %s%s\n' "$k" "${SBC_V[$k]}" "${SBC_L[$k]}" "$([ "${SBC_R_SAFE[$k]}" = y ] && echo ' SAFETY')"
	done
}
