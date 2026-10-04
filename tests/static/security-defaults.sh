#!/usr/bin/env bash
# Static check of insecure defaults (docs/SECURITY-DEFAULTS.md), no hardware/root needed:
#  1. the legacy root login lines in build/build.sh are reachable ONLY under the GS_LEGACY_ROOT_LOGIN=1 guard,
#  2. simulation: the guarded block (extracted between its markers) run against a temp rootfs for both flag states,
#  3. ratchet: remaining known insecure defaults (file:line); their count may not increase.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO" || exit 2
bad=0
B=build/build.sh
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT

echo "== legacy root login only under GS_LEGACY_ROOT_LOGIN guard"
start="$(grep -n '^# >>> root-login' "$B" | cut -d: -f1)"; end="$(grep -n '^# <<< root-login' "$B" | cut -d: -f1)"
guard="$(grep -n 'if \[ "\${GS_LEGACY_ROOT_LOGIN:-0}" = "1" \]; then' "$B" | cut -d: -f1)"
els="$(awk -v s="$start" -v e="$end" 'NR>s && NR<e && /^else$/ {print NR; exit}' "$B")"
echo "markers: block=$([ -n "$start" ] && [ -n "$end" ] && echo found || echo MISSING) guard=$([ -n "$guard" ] && echo found || echo MISSING) else=$([ -n "$els" ] && echo found || echo MISSING)"
[ -n "$start" ] && [ -n "$end" ] && [ -n "$guard" ] && [ -n "$els" ] || { echo "FAIL markers"; echo "bad=1"; exit 1; }
for pat in 'chpasswd' 'PermitRootLogin yes'; do
	while IFS=: read -r n _; do
		legacy_only=0
		[ "$n" -gt "$guard" ] && [ "$n" -lt "$els" ] && legacy_only=1
		if [ "$legacy_only" = 1 ]; then echo "ok   '$pat' (legacy line) inside guard"
		else
			# the only allowed use outside the guard is the GS_ROOT_PASSWORD branch (chpasswd of the variable)
			if [ "$pat" = chpasswd ] && sed -n "${n}p" "$B" | grep -q 'root:\${GS_ROOT_PASSWORD}'; then echo "ok   chpasswd of GS_ROOT_PASSWORD (not 'root:root')"
			else echo "FAIL '$pat' reachable outside guard"; bad=1; fi
		fi
	done < <(grep -n "$pat" "$B" | grep -v '^[0-9]*:[[:space:]]*#')
done
[ "$(grep -c '"root:root" | chpasswd' "$B")" = 1 ] && echo "ok   exactly one 'root:root' chpasswd in build.sh" || { echo "FAIL root:root count"; bad=1; }
[ "$(grep -c 'PermitRootLogin yes' "$B")" = 1 ] && echo "ok   exactly one 'PermitRootLogin yes' in build.sh" || { echo "FAIL PermitRootLogin yes count"; bad=1; }

echo "== simulation of the root-login block"
sed -n "$((start+1)),$((end-1))p" "$B" > "$T/block.sh"
sim() {  # sim <label> [env assignments...]
	local label="$1"; shift
	mkdir -p "$T/etc/ssh"; printf '%s\n' '#PermitRootLogin prohibit-password' > "$T/etc/ssh/sshd_config"; : > "$T/chpasswd.log"
	sed "s#/etc/ssh/sshd_config#$T/etc/ssh/sshd_config#" "$T/block.sh" > "$T/block.run.sh"
	( chpasswd() { cat >> "$T/chpasswd.log"; }
	  for kv in "$@"; do export "${kv?}"; done  # exported BEFORE set -x so only the block itself is traced
	  set -e -x
	  . "$T/block.run.sh" ) 2> "$T/xtrace.log"
	echo "-- $label (rc=$?)"
	echo "sshd_config: $(cat "$T/etc/ssh/sshd_config")"
	echo "chpasswd input: $(if [ -s "$T/chpasswd.log" ]; then cat "$T/chpasswd.log"; else echo none; fi)"
}
sim "default"
sim "GS_LEGACY_ROOT_LOGIN=1" GS_LEGACY_ROOT_LOGIN=1
sim "GS_ROOT_PASSWORD set" GS_ROOT_PASSWORD=Sim-Pw-123
if grep -q 'Sim-Pw-123' "$T/xtrace.log"; then echo "FAIL password leaked into xtrace"; bad=1; else echo "ok   password not in xtrace log"; fi
sim "both set (legacy wins)" GS_LEGACY_ROOT_LOGIN=1 GS_ROOT_PASSWORD=Sim-Pw-123

echo "== remaining known insecure defaults (ratchet: count may not increase)"
cnt=0
chk() {  # chk <file> <fixed pattern> <max> <label>
	local f="$1" p="$2" max="$3" label="$4" n
	n="$(grep -nF -- "$p" "$f" | grep -v '^[0-9]*:[[:space:]]*#' | wc -l)"
	grep -nF -- "$p" "$f" | grep -v '^[0-9]*:[[:space:]]*#' | cut -d: -f1 | sed "s#^#$f:#; s#\$#  $label#"
	cnt=$((cnt+n))
	[ "$n" -le "$max" ] || { echo "FAIL $label: $n > $max in $f"; bad=1; }
}
chk build/build.sh 'User=root' 1 "ttyd runs as root (documented)"
chk gs/gs-init.sh 'force user = root' 2 "anonymous samba as root (documented)"
chk gs/install.sh 'cp FPVue.key /config/gs.key' 1 "public FPVue.key as working wfb key (documented)"
chk gs/gs.conf "hotspot_password='12345678'" 1 "default AP password (documented)"
chk gs/gs.conf "ap_wifi_password='12345678'" 1 "default AP password (documented)"
chk gs/gs.conf "wifi_mode='hotspot'" 1 "hotspot as default mode (documented)"
echo "total=$cnt (max 7)"
[ "$cnt" -le 7 ] || { echo "FAIL total insecure defaults grew"; bad=1; }
echo "bad=$bad"
exit $bad
