#!/usr/bin/env bash
# Static check of the DRAFT Raspberry Pi 5 board profile (gs/boards/rpi5/, Bookworm base, docs/BOARD-RPI5.md):
#  - validate.sh passes for the profile and for every board, and a copy with a required key removed / emptied / bad is rejected (mutation check)
#  - every KEY= line has a "# src:" evidence comment directly above (all keys, optional ones included)
#  - every "# UNVERIFIED (" marker is attached to a key, listed in the output, documented in docs/BOARD-RPI5.md; their number may only go DOWN (ratchet)
#  - the OTG 'none' sentinel is honoured by gs/lib/otg.sh and by gs/button.sh change_otg_mode (no "cat: none" error); Radxa behaviour is unchanged
#  - pinmap.conf: 40 pins, RP1 header GPIO 2..27, pins 27/28 are ID lines that gpio_find refuses (no GPIO0/GPIO1 line exists on the RP1 chip)
#  - the profile renders the udev templates and feeds the hw/gpio helpers
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF="$REPO/gs/boards/rpi5/board.conf"
MAP="$REPO/gs/boards/rpi5/pinmap.conf"
V="$REPO/gs/boards/validate.sh"
DOC="$REPO/docs/BOARD-RPI5.md"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
# Ratchet: lower this number (and update the golden) when a value gets verified; raising it fails the test.
UNVERIFIED_MAX=4
want() {  # want <label> <expected> <actual>
	if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}
# mutant <label> <sed expr or APPEND:line or DROP:key or MAP:sed>: copy of rpi5 with one change; validate.sh must reject it
mutant() {
	rm -rf "$T/v"; mkdir -p "$T/v/rpi5"; cp "$REPO/gs/boards/rpi5/"* "$T/v/rpi5/"
	case "$2" in
		APPEND:*) printf '%s\n' "${2#APPEND:}" >> "$T/v/rpi5/board.conf" ;;
		DROP:*) grep -Ev "^${2#DROP:}=" "$CONF" > "$T/v/rpi5/board.conf" ;;
		*) sed -i -E "$2" "$T/v/rpi5/board.conf" ;;
	esac
	if o="$("$V" "$T/v/rpi5" 2>&1)"; then echo "FAIL $1 accepted"; bad=1; else echo "ok $1 rejected: $(echo "$o" | sed "s#$T#<T>#g")"; fi
}

echo "== validate"
out="$("$V" "$REPO/gs/boards/rpi5" 2>&1)"; rc=$?
echo "$out"
[ $rc -eq 0 ] && echo "ok rpi5 passes" || { echo "FAIL rpi5 rejected"; bad=1; }
"$V" >/dev/null 2>&1 && echo "ok validate.sh passes for every board" || { echo "FAIL validate.sh over all boards"; bad=1; }
# shellcheck disable=SC1090
want "BOARD_ID equals directory name" rpi5 "$(unset BOARD_ID; . "$CONF"; printf '%s' "$BOARD_ID")"

echo "== mutation (copy): required key removed or emptied must be rejected"
mapfile -t required < <(grep -Ev '^[[:space:]]*(#|$)' "$REPO/gs/boards/REQUIRED_KEYS")
for k in "${required[@]}"; do
	mkdir -p "$T/m/rpi5"
	grep -Ev "^${k}=" "$CONF" > "$T/m/rpi5/board.conf"
	if "$V" "$T/m/rpi5" >/dev/null 2>&1; then echo "FAIL removing $k accepted"; bad=1; fi
	sed -E "s/^${k}=.*/${k}=''/" "$CONF" > "$T/m/rpi5/board.conf"
	if "$V" "$T/m/rpi5" >/dev/null 2>&1; then echo "FAIL emptying $k accepted"; bad=1; fi
done
echo "ok ${#required[@]} required keys: removal and emptying both rejected"

echo "== mutation (copy): optional contract keys with bad values, bad syntax"
mutant "GPIO_PIN_MAP empty" "s/^GPIO_PIN_MAP=.*/GPIO_PIN_MAP=''/"
mutant "GPIO_PIN_MAP missing file" "s#^GPIO_PIN_MAP=.*#GPIO_PIN_MAP='nope.conf'#"
mutant "GPIO_PIN_MAP dotdot" "s#^GPIO_PIN_MAP=.*#GPIO_PIN_MAP='../rpi4/pinmap.conf'#"
mutant "GPIO_PIN_NUMBERING=sideways" "s/^GPIO_PIN_NUMBERING=.*/GPIO_PIN_NUMBERING='sideways'/"
mutant "DTBO_MODE=symlink" "s/^DTBO_MODE=.*/DTBO_MODE='symlink'/"
mutant "PART_TABLE=lvm" "s/^PART_TABLE=.*/PART_TABLE='lvm'/"
mutant "command line in the profile" 'APPEND:X=$(rm -rf /)'

echo "== every key has a src comment directly above"
keys=0
while IFS= read -r k; do
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
dup="$(grep -E '^[A-Z][A-Z0-9_]*=' "$CONF" | cut -d= -f1 | sort | uniq -d | tr '\n' ' ')"
want "duplicate keys" "" "$dup"

echo "== required keys all defined"
for k in "${required[@]}"; do grep -qE "^${k}=" "$CONF" || { echo "FAIL missing $k"; bad=1; }; done
echo "ok ${#required[@]} required keys present"

echo "== no Radxa literal in a value"
radxa="$(grep -E '^[A-Z][A-Z0-9_]*=' "$CONF" | grep -E "fcc00000|rk3568|radxa|ttyFIQ0|aicwf|/boot/dtbo|/media/root-ro|mmcblk[01]p4" || true)"
want "Radxa literals in values" "" "$radxa"

echo "== UNVERIFIED values (ratchet: must not grow)"
n=0; listed=" "
while IFS= read -r k; do
	ln="$(grep -nE "^${k}=" "$CONF" | head -1 | cut -d: -f1)"
	i=$((ln-1)); reason=""
	while [ "$i" -ge 1 ]; do
		line="$(sed -n "${i}p" "$CONF")"
		[[ "$line" =~ ^# ]] || break
		if [[ "$line" =~ ^#\ UNVERIFIED\ \((.*)\)$ ]]; then reason="${BASH_REMATCH[1]}"; break; fi
		i=$((i-1))
	done
	if [ -n "$reason" ]; then n=$((n+1)); listed="$listed$k "; echo "UNVERIFIED $k: $reason"; fi
done < <(grep -E '^[A-Z][A-Z0-9_]*=' "$CONF" | cut -d= -f1)
markers="$(grep -cE '^# UNVERIFIED \(' "$CONF")"
want "markers attached to a key" "$markers" "$n"
echo "unverified=$n max=$UNVERIFIED_MAX"
if [ "$n" -gt "$UNVERIFIED_MAX" ]; then echo "FAIL UNVERIFIED count grew ($n > $UNVERIFIED_MAX)"; bad=1; fi
[ "$n" -lt "$UNVERIFIED_MAX" ] && echo "note: fewer UNVERIFIED than the ratchet, lower UNVERIFIED_MAX to $n"
for k in $listed; do
	grep -qF "\`$k\`" "$DOC" 2>/dev/null || { echo "FAIL $k is UNVERIFIED but not listed in docs/BOARD-RPI5.md"; bad=1; }
done
echo "ok every UNVERIFIED key is documented in docs/BOARD-RPI5.md"
grep -q 'DTBO_DIR_LOWER' "$DOC" 2>/dev/null && echo "ok docs name the deliberately undefined DTBO_DIR_LOWER" || { echo "FAIL docs lack DTBO_DIR_LOWER note"; bad=1; }
grep -qE '^DTBO_DIR_LOWER=' "$CONF" && { echo "FAIL DTBO_DIR_LOWER defined (decision: undefined until an overlayroot image exists)"; bad=1; }

echo "== contract values that make this a Pi 5 / Bookworm profile (not Radxa)"
# shellcheck disable=SC1090
pv() { (unset "$1"; . "$CONF"; printf '%s' "${!1}"); }
for kv in GPIO_PIN_PREFIX=GPIO GPIO_PIN_NUMBERING=physical GPIO_PIN_MAP=pinmap.conf GPIO_CHIP_LABEL=pinctrl-rp1 DTBO_MODE=config-txt DTBO_SOC_PREFIX=none \
	DTBO_DIR=/boot/firmware/overlays WIFI_ONBOARD_IFACE=wifi0 WIFI_ONBOARD_DRIVER=brcmfmac CONSOLE_TTY=/dev/ttyAMA10 PART_TABLE=mbr PART_SEP=p ROOTFS_LABEL=rootfs \
	CONFIG_TXT=/boot/firmware/config.txt RTC_I2C_BUS=1; do
	want "${kv%%=*}" "${kv#*=}" "$(pv "${kv%%=*}")"
done

echo "== OTG 'none' sentinel (gs/lib/otg.sh)"
for h in otg_controller otg_mode_file otg_mass_default otg_mass_alt; do
	want "rpi5 $h" none "$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/otg.sh' && $h")"
done
BOARD=rpi5 bash -c ". '$REPO/gs/lib/otg.sh' && otg_supported" && { echo "FAIL rpi5 otg_supported true"; bad=1; } || echo "ok rpi5 otg_supported false"
BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/otg.sh' && otg_supported" && echo "ok radxa-zero3 otg_supported true" || { echo "FAIL radxa-zero3 otg_supported false"; bad=1; }

echo "== gs/button.sh change_otg_mode with the sentinel (function text cut out of button.sh)"
awk '/^function change_otg_mode\(\)/{p=1} p{print} p&&/^}/{exit}' "$REPO/gs/button.sh" > "$T/fn.sh"
[ -s "$T/fn.sh" ] && echo "ok change_otg_mode extracted" || { echo "FAIL change_otg_mode not found in gs/button.sh"; bad=1; }
# run <board> <mode-file-content or ->: prints "rc=<n> out=[...]" and whether the stub gpio_find was called
run_otg() {
	local b="$1" mode="$2" o rc
	rm -f "$T/gpio_called" "$T/mode"; [ "$mode" = "-" ] || printf '%s\n' "$mode" > "$T/mode"
	o="$(cd "$T" && BOARD="$b" bash -c "
		set -e
		. '$REPO/gs/lib/otg.sh'
		. '$T/fn.sh'
		gpio_find() { echo called > '$T/gpio_called'; echo gpiochip0; }
		[ '$b' = rpi5 ] || otg_mode_file() { echo '$T/mode'; }
		otg_mode_led_pin=led; led=15
		change_otg_mode" 2>&1)"; rc=$?
	o="${o//$T/<T>}"
	echo "rc=$rc out=[$o] gpio_find_called=$([ -e "$T/gpio_called" ] && echo yes || echo no)"
}
want "rpi5 (no mode file) message, no cat error, no gpio" "rc=0 out=[otg mode switch is not supported on this board] gpio_find_called=no" "$(run_otg rpi5 -)"
want "rpi5 even if a stray file named none exists" "rc=0 out=[otg mode switch is not supported on this board] gpio_find_called=no" "$(printf 'host\n' > "$T/none"; run_otg rpi5 -)"
want "radxa-zero3 unknown mode (legacy path)" "rc=0 out=[otg mode is unkonw] gpio_find_called=yes" "$(run_otg radxa-zero3 weird)"
want "unknown board falls back to the legacy path" "rc=0 out=[otg mode is unkonw] gpio_find_called=yes" "$(run_otg nope weird)"

echo "== hw and gpio helpers read the rpi5 profile"
for h in hw_cpu_temp_file hw_pwm_base hw_home_dir hw_wifi_iface hw_rtc_i2c_bus hw_dtbo_mode hw_part_table; do
	echo "$h=$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/hw.sh' && $h")"
done
echo "gpio prefix=$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/gpio.sh' && _gpio_pin_prefix")"
echo "gpio chip label=$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/board.sh' && board_get GPIO_CHIP_LABEL")"
echo "console=$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/board.sh' && board_get CONSOLE_TTY")"

echo "== gpio_find on rpi5: physical pin -> GPIO<n> via pinmap.conf (gpiofind is a PATH shim that logs argv)"
mkdir -p "$T/bin"
printf '#!/bin/bash\necho "gpiofind $*" >> "$SHIM_LOG"\necho "gpiochip0 ${1##*[A-Z_]}"\n' > "$T/bin/gpiofind"; chmod +x "$T/bin/gpiofind"
export SHIM_LOG="$T/log"
find_pin() {  # find_pin <pin> -> "rc=<n> out=[..] err=[..] argv=[..]"
	local o e rc
	: > "$SHIM_LOG"
	o="$(BOARD=rpi5 PATH="$T/bin:/usr/bin:/bin" bash -c ". '$REPO/gs/lib/gpio.sh' && gpio_find '$1'" 2>"$T/err")"; rc=$?
	e="$(sed "s#$T#<T>#g;s#$REPO#<REPO>#g" "$T/err")"
	echo "rc=$rc out=[$o] err=[$e] argv=[$(tr '\n' ';' < "$SHIM_LOG")]"
}
for pair in 3:2 5:3 7:4 8:14 10:15 11:17 12:18 13:27 15:22 26:7 29:5 32:12 33:13 35:19 37:26 38:20 40:21; do
	p="${pair%%:*}"; b="${pair#*:}"
	want "rpi5 physical $p" "rc=0 out=[gpiochip0 $b] err=[] argv=[gpiofind GPIO$b;]" "$(find_pin "$p")"
done
echo "== rpi5 ID lines, power/ground and unknown pins are refused without calling gpiofind"
want "pin 27 (ID_SDA)" "rc=1 out=[] err=[gpio.sh: physical pin 27 is ID_SDA, not a GPIO line] argv=[]" "$(find_pin 27)"
want "pin 28 (ID_SCL)" "rc=1 out=[] err=[gpio.sh: physical pin 28 is ID_SCL, not a GPIO line] argv=[]" "$(find_pin 28)"
for p in 1 2 6 9 39; do echo "pin $p: $(find_pin "$p")"; done
for p in 0 41 abc ''; do echo "pin '$p': $(find_pin "$p")"; done

echo "== pinmap.conf is a complete 40-pin table, no line named GPIO0/GPIO1"
n="$(grep -cE '^[0-9]+ ' "$MAP")"; want "pinmap rows" 40 "$n"
want "pinmap pins 1..40 once" "$(seq 1 40 | tr '\n' ' ')" "$(grep -E '^[0-9]+ ' "$MAP" | cut -d' ' -f1 | sort -n | tr '\n' ' ')"
g="$(grep -cE '^[0-9]+ [0-9]+$' "$MAP")"; want "GPIO rows (GPIO2..27 = 26)" 26 "$g"
want "GPIO numbers 2..27 each once" "$(seq 2 27 | tr '\n' ' ')" "$(grep -E '^[0-9]+ [0-9]+$' "$MAP" | cut -d' ' -f2 | sort -n | tr '\n' ' ')"
want "ID rows" "27 ID_SDA;28 ID_SCL;" "$(grep -E '^[0-9]+ ID_' "$MAP" | tr ' \n' ' ;' | sed 's/ ;/;/g')"
p="$(grep -cE '^[0-9]+ (3V3|5V|GND)$' "$MAP")"; want "power/ground rows (2x3V3 2x5V 8xGND = 12)" 12 "$p"
d="$(diff <(grep -E '^[0-9]+ ' "$REPO/gs/boards/rpi4/pinmap.conf") <(grep -E '^[0-9]+ ' "$MAP") | grep -E '^[<>]' | tr '\n' ';')"
want "difference to rpi4 pinmap (only pins 27/28)" "< 27 0;< 28 1;> 27 ID_SDA;> 28 ID_SCL;" "$d"
grep -q 'UNVERIFIED marker of GPIO_PIN_PREFIX' "$MAP" && echo "ok pinmap.conf points at the GPIO_PIN_PREFIX marker" || { echo "FAIL pinmap.conf lacks the pointer to the marker"; bad=1; }

echo "== udev templates render from the rpi5 profile"
if "$REPO/gs/boards/render-udev.sh" rpi5 "$T/udev" >/dev/null 2>&1; then
	for s in 'NAME="wifi0"' 'ID_NET_DRIVER}=="brcmfmac"' 'NAME="rpi0"'; do
		grep -qF "$s" "$T/udev/98-rename.rules" && echo "ok 98-rename.rules has $s" || { echo "FAIL 98-rename.rules lacks $s"; bad=1; }
	done
else echo "FAIL render-udev.sh rpi5"; bad=1; fi

echo "bad=$bad"
exit $bad
