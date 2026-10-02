#!/usr/bin/env bash
# Static check of the DRAFT Raspberry Pi 4 board profile (gs/boards/rpi4/board.conf, milestone M6 step 1):
#  - validate.sh passes, and a copy with a required key removed / emptied is rejected (mutation check)
#  - every KEY= line has a "# src:" evidence comment directly above it
#  - every "# UNVERIFIED (" marker is listed in the output, and their number may only go DOWN (ratchet)
#  - the OTG 'none' sentinel is honoured by gs/lib/otg.sh (otg_supported) and Radxa behaviour is unchanged
#  - the profile renders the udev templates and feeds the hw/gpio helpers
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF="$REPO/gs/boards/rpi4/board.conf"
V="$REPO/gs/boards/validate.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
# Ratchet: lower this number (and update the golden) when a value gets verified; raising it fails the test.
UNVERIFIED_MAX=8
want() {  # want <label> <expected> <actual>
	if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}

echo "== validate"
out="$("$V" "$REPO/gs/boards/rpi4" 2>&1)"; rc=$?
echo "$out"
[ $rc -eq 0 ] && echo "ok rpi4 passes" || { echo "FAIL rpi4 rejected"; bad=1; }

echo "== mutation (copy): required key removed or emptied must be rejected"
mapfile -t required < <(grep -Ev '^[[:space:]]*(#|$)' "$REPO/gs/boards/REQUIRED_KEYS")
for k in "${required[@]}"; do
	mkdir -p "$T/m/rpi4"
	grep -Ev "^${k}=" "$CONF" > "$T/m/rpi4/board.conf"
	if "$V" "$T/m/rpi4" >/dev/null 2>&1; then echo "FAIL removing $k accepted"; bad=1; fi
	sed -E "s/^${k}=.*/${k}=''/" "$CONF" > "$T/m/rpi4/board.conf"
	if "$V" "$T/m/rpi4" >/dev/null 2>&1; then echo "FAIL emptying $k accepted"; bad=1; fi
done
echo "ok ${#required[@]} required keys: removal and emptying both rejected"

echo "== every key has a src comment directly above"
keys=0
# Optional contract-extension keys are checked by tests/static/contract-ext.sh (kept out of this count so the golden stays identical).
EXT_KEYS=" GPIO_PIN_NUMBERING GPIO_PIN_MAP DTBO_MODE PART_TABLE "
while IFS= read -r k; do
	case "$EXT_KEYS" in *" $k "*) continue ;; esac
	keys=$((keys+1))
	ln="$(grep -nE "^${k}=" "$CONF" | head -1 | cut -d: -f1)"
	found=0; i=$((ln-1))
	while [ "$i" -ge 1 ]; do
		line="$(sed -n "${i}p" "$CONF")"
		[[ "$line" =~ ^# ]] || break
		[[ "$line" =~ ^#\ src:\  ]] && { found=1; break; }
		i=$((i-1))
	done
	[ $found = 1 ] || { echo "FAIL $k has no '# src:' comment above it"; bad=1; }
done < <(grep -E '^[A-Z][A-Z0-9_]*=' "$CONF" | cut -d= -f1)
echo "ok $keys keys checked"

echo "== required keys all defined"
for k in "${required[@]}"; do grep -qE "^${k}=" "$CONF" || { echo "FAIL missing $k"; bad=1; }; done
echo "ok ${#required[@]} required keys present"

echo "== UNVERIFIED values (ratchet: must not grow)"
n=0
while IFS= read -r k; do
	ln="$(grep -nE "^${k}=" "$CONF" | head -1 | cut -d: -f1)"
	i=$((ln-1)); reason=""
	while [ "$i" -ge 1 ]; do
		line="$(sed -n "${i}p" "$CONF")"
		[[ "$line" =~ ^# ]] || break
		if [[ "$line" =~ ^#\ UNVERIFIED\ \((.*)\)$ ]]; then reason="${BASH_REMATCH[1]}"; break; fi
		i=$((i-1))
	done
	if [ -n "$reason" ]; then n=$((n+1)); echo "UNVERIFIED $k: $reason"; fi
done < <(grep -E '^[A-Z][A-Z0-9_]*=' "$CONF" | cut -d= -f1)
markers="$(grep -cE '^# UNVERIFIED \(' "$CONF")"
want "markers attached to a key" "$markers" "$n"
echo "unverified=$n max=$UNVERIFIED_MAX"
if [ "$n" -gt "$UNVERIFIED_MAX" ]; then echo "FAIL UNVERIFIED count grew ($n > $UNVERIFIED_MAX)"; bad=1; fi
[ "$n" -lt "$UNVERIFIED_MAX" ] && echo "note: fewer UNVERIFIED than the ratchet, lower UNVERIFIED_MAX to $n"

echo "== OTG 'none' sentinel (gs/lib/otg.sh)"
for h in otg_controller otg_mode_file otg_mass_default otg_mass_alt; do
	want "rpi4 $h" none "$(BOARD=rpi4 bash -c ". '$REPO/gs/lib/otg.sh' && $h")"
done
BOARD=rpi4 bash -c ". '$REPO/gs/lib/otg.sh' && otg_supported" && { echo "FAIL rpi4 otg_supported true"; bad=1; } || echo "ok rpi4 otg_supported false"
BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/otg.sh' && otg_supported" && echo "ok radxa-zero3 otg_supported true" || { echo "FAIL radxa-zero3 otg_supported false"; bad=1; }
BOARD=nope bash -c ". '$REPO/gs/lib/otg.sh' && otg_supported" && echo "ok unknown board falls back to supported" || { echo "FAIL unknown board unsupported"; bad=1; }
mkdir -p "$T/nolib"; cp "$REPO/gs/lib/otg.sh" "$T/nolib/"
bash -c ". '$T/nolib/otg.sh' && otg_supported" && echo "ok no board lib falls back to supported" || { echo "FAIL no board lib unsupported"; bad=1; }
bash -c "set -e; . '$REPO/gs/lib/otg.sh'; if otg_supported; then echo supported; else echo unsupported; fi; echo survived" >/dev/null || { echo "FAIL set -e"; bad=1; }
echo "ok set -e safe inside if"

echo "== hw and gpio helpers read the rpi4 profile"
for h in hw_cpu_temp_file hw_pwm_base hw_home_dir hw_wifi_iface hw_rtc_i2c_bus; do
	echo "$h=$(BOARD=rpi4 bash -c ". '$REPO/gs/lib/hw.sh' && $h")"
done
echo "gpio prefix=$(BOARD=rpi4 bash -c ". '$REPO/gs/lib/gpio.sh' && _gpio_pin_prefix")"
echo "NOTE: gpio_find 7 would run 'gpiofind GPIO7' = BCM 7 (header pin 26), not physical pin 7 (contract mismatch, see docs/BOARD-RPI4.md)"

echo "== udev templates render from the rpi4 profile"
if "$REPO/gs/boards/render-udev.sh" rpi4 "$T/udev" >/dev/null 2>&1; then
	for s in 'NAME="wifi0"' 'ID_NET_DRIVER}=="brcmfmac"' 'NAME="rpi0"'; do
		grep -qF "$s" "$T/udev/98-rename.rules" && echo "ok 98-rename.rules has $s" || { echo "FAIL 98-rename.rules lacks $s"; bad=1; }
	done
else echo "FAIL render-udev.sh rpi4"; bad=1; fi

echo "bad=$bad"
exit $bad
