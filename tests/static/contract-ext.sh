#!/usr/bin/env bash
# Static check of the optional board-contract extensions (docs/BOARD-RPI4.md section 4):
#  GPIO_PIN_NUMBERING / GPIO_PIN_MAP (gs/lib/gpio.sh), DTBO_MODE (hw_dtbo_mode), PART_TABLE (hw_part_table), validate.sh enum checks.
# Defaults (keys absent) must reproduce Radxa behaviour exactly. gpiofind is a PATH shim that logs argv.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
V="$REPO/gs/boards/validate.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
printf '#!/bin/bash\necho "gpiofind $*" >> "$SHIM_LOG"\necho "gpiochip0 ${1##*[A-Z_]}"\n' > "$T/bin/gpiofind"; chmod +x "$T/bin/gpiofind"
export SHIM_LOG="$T/log"
bad=0
want() {  # want <label> <expected> <actual>
	if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}
# find <board> <pin> [lib dir]: run gpio_find, print "rc=<n> out=<stdout> err=<stderr> argv=<shim log>"
find_pin() {
	local lib="${3:-$REPO/gs/lib}" o e rc
	: > "$SHIM_LOG"
	o="$(BOARD="$1" PATH="$T/bin:/usr/bin:/bin" bash -c ". '$lib/gpio.sh' && gpio_find '$2'" 2>"$T/err")"; rc=$?
	e="$(sed "s#$T#<T>#g;s#$REPO#<REPO>#g" "$T/err")"
	echo "rc=$rc out=[$o] err=[$e] argv=[$(tr '\n' ';' < "$SHIM_LOG")]"
}

echo "== radxa-zero3 (keys absent): legacy gpiofind PIN_<n>"
for n in 22 32; do want "radxa pin $n" "rc=0 out=[gpiochip0 $n] err=[] argv=[gpiofind PIN_$n;]" "$(find_pin radxa-zero3 $n)"; done
echo "== unknown board falls back to legacy"
want "unknown board pin 22" "rc=0 out=[gpiochip0 22] err=[] argv=[gpiofind PIN_22;]" "$(find_pin nope 22)"
echo "== rpi4 physical via pinmap.conf"
for pair in 7:4 3:2 5:3 11:17 12:18 26:7 27:0 28:1 40:21 32:12; do
	p="${pair%%:*}"; b="${pair#*:}"
	want "rpi4 physical $p" "rc=0 out=[gpiochip0 $b] err=[] argv=[gpiofind GPIO$b;]" "$(find_pin rpi4 $p)"
done
echo "== rpi4 non-GPIO and unknown pins"
for p in 1 2 6 9 39; do echo "pin $p: $(find_pin rpi4 $p)"; done
for p in 0 41 abc ''; do echo "pin '$p': $(find_pin rpi4 "$p")"; done
echo "== pinmap.conf is a complete 40-pin table"
n="$(grep -cE '^[0-9]+ ' "$REPO/gs/boards/rpi4/pinmap.conf")"; want "pinmap rows" 40 "$n"
want "pinmap pins 1..40 once" "$(seq 1 40 | tr '\n' ' ')" "$(grep -E '^[0-9]+ ' "$REPO/gs/boards/rpi4/pinmap.conf" | cut -d' ' -f1 | sort -n | tr '\n' ' ')"
g="$(grep -E '^[0-9]+ [0-9]+$' "$REPO/gs/boards/rpi4/pinmap.conf" | wc -l)"; want "GPIO rows (BCM 0..27 = 28)" 28 "$g"
want "BCM numbers 0..27 each once" "$(seq 0 27 | tr '\n' ' ')" "$(grep -E '^[0-9]+ [0-9]+$' "$REPO/gs/boards/rpi4/pinmap.conf" | cut -d' ' -f2 | sort -n | tr '\n' ' ')"
p="$(grep -cE '^[0-9]+ (3V3|5V|GND)$' "$REPO/gs/boards/rpi4/pinmap.conf")"; want "power/ground rows (2x3V3 2x5V 8xGND = 12)" 12 "$p"

echo "== synthetic board: bcm mode, missing map, unknown mode, other prefix"
mkdir -p "$T/gs/lib" "$T/gs/boards/b" "$T/gs/boards/m" "$T/gs/boards/u" "$T/gs/boards/p"
cp "$REPO/gs/lib/gpio.sh" "$REPO/gs/lib/board.sh" "$REPO/gs/lib/hw.sh" "$T/gs/lib/"
printf "GPIO_PIN_PREFIX='GPIO'\nGPIO_PIN_NUMBERING='bcm'\n" > "$T/gs/boards/b/board.conf"
printf "GPIO_PIN_PREFIX='GPIO'\nGPIO_PIN_MAP='nofile.conf'\n" > "$T/gs/boards/m/board.conf"
printf "GPIO_PIN_PREFIX='GPIO'\nGPIO_PIN_NUMBERING='weird'\n" > "$T/gs/boards/u/board.conf"
printf "GPIO_PIN_PREFIX='X_'\nGPIO_PIN_NUMBERING='physical'\n" > "$T/gs/boards/p/board.conf"
for n in 4 17; do want "bcm pin $n" "rc=0 out=[gpiochip0 $n] err=[] argv=[gpiofind GPIO$n;]" "$(find_pin b $n "$T/gs/lib")"; done
echo "bcm pin 'x': $(find_pin b x "$T/gs/lib")"
echo "missing map: $(find_pin m 7 "$T/gs/lib")"
echo "unknown mode: $(find_pin u 7 "$T/gs/lib")"
want "explicit physical, no map, prefix X_" "rc=0 out=[gpiochip0 7] err=[] argv=[gpiofind X_7;]" "$(find_pin p 7 "$T/gs/lib")"
echo "== failure status of gpiofind is passed through (physical and mapped)"
printf '#!/bin/bash\nexit 3\n' > "$T/bin/gpiofind"
want "radxa rc" 3 "$(PATH="$T/bin:/usr/bin:/bin" BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/gpio.sh' && gpio_find 22; echo \$?" | tail -1)"
want "rpi4 rc" 3 "$(PATH="$T/bin:/usr/bin:/bin" BOARD=rpi4 bash -c ". '$REPO/gs/lib/gpio.sh' && gpio_find 7; echo \$?" | tail -1)"

echo "== hw_dtbo_mode / hw_part_table"
for h in hw_dtbo_mode hw_part_table; do
	case $h in hw_dtbo_mode) r=rename; p=config-txt ;; *) r=gpt; p=mbr ;; esac
	want "radxa $h" "$r" "$(BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/hw.sh' && $h")"
	want "rpi4 $h" "$p" "$(BOARD=rpi4 bash -c ". '$REPO/gs/lib/hw.sh' && $h")"
	want "unknown board $h" "$r" "$(BOARD=nope bash -c ". '$REPO/gs/lib/hw.sh' && $h")"
	want "key absent (board b) $h" "$r" "$(BOARD=b bash -c ". '$T/gs/lib/hw.sh' && $h")"
	mkdir -p "$T/nolib"; cp "$REPO/gs/lib/hw.sh" "$T/nolib/"
	want "no board lib $h" "$r" "$(bash -c ". '$T/nolib/hw.sh' && $h")"
	bash -c "set -e; . '$T/nolib/hw.sh'; $h >/dev/null; echo survived" >/dev/null || { echo "FAIL $h set -e"; bad=1; }
done
echo "ok helpers set -e safe"

echo "== radxa profile does not define the optional keys (defaults are in force)"
for k in GPIO_PIN_NUMBERING GPIO_PIN_MAP DTBO_MODE PART_TABLE; do
	grep -qE "^$k=" "$REPO/gs/boards/radxa-zero3/board.conf" && { echo "FAIL radxa defines $k"; bad=1; }
done
echo "ok radxa-zero3 defines none of the 4 optional keys"
echo "== rpi4 defines them, each with a src comment directly above"
for k in GPIO_PIN_NUMBERING GPIO_PIN_MAP DTBO_MODE PART_TABLE; do
	ln="$(grep -nE "^$k=" "$REPO/gs/boards/rpi4/board.conf" | head -1 | cut -d: -f1)"
	if [ -n "$ln" ] && sed -n "$((ln-1))p" "$REPO/gs/boards/rpi4/board.conf" | grep -q '^# src:'; then echo "ok $k has src"; else echo "FAIL $k missing or no src"; bad=1; fi
done

echo "== validate.sh"
"$V" 2>&1 | sed 's/^/  /'; [ "${PIPESTATUS[0]}" = 0 ] && echo "ok both boards pass" || { echo "FAIL validate"; bad=1; }
mut() {  # mut <label> <sed expr or APPEND:line> ; copy of rpi4 with one change, must be rejected
	rm -rf "$T/v"; mkdir -p "$T/v/rpi4"; cp "$REPO/gs/boards/rpi4/"* "$T/v/rpi4/"
	case "$2" in APPEND:*) printf '%s\n' "${2#APPEND:}" >> "$T/v/rpi4/board.conf" ;; *) sed -i -E "$2" "$T/v/rpi4/board.conf" ;; esac
	if o="$("$V" "$T/v/rpi4" 2>&1)"; then echo "FAIL $1 accepted"; bad=1; else echo "ok $1 rejected: $(echo "$o" | sed "s#$T#<T>#g")"; fi
}
mut "GPIO_PIN_NUMBERING=sideways" "s/^GPIO_PIN_NUMBERING=.*/GPIO_PIN_NUMBERING='sideways'/"
mut "DTBO_MODE=symlink" "s/^DTBO_MODE=.*/DTBO_MODE='symlink'/"
mut "PART_TABLE=lvm" "s/^PART_TABLE=.*/PART_TABLE='lvm'/"
mut "PART_TABLE empty" "s/^PART_TABLE=.*/PART_TABLE=''/"
mut "GPIO_PIN_MAP absolute" "s#^GPIO_PIN_MAP=.*#GPIO_PIN_MAP='/etc/passwd'#"
mut "GPIO_PIN_MAP dotdot" "s#^GPIO_PIN_MAP=.*#GPIO_PIN_MAP='../radxa-zero3/board.conf'#"
mut "GPIO_PIN_MAP missing file" "s#^GPIO_PIN_MAP=.*#GPIO_PIN_MAP='nope.conf'#"
echo "bad=$bad"
exit $bad
