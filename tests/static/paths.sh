#!/usr/bin/env bash
# Static check of M3d: gs/lib/hw.sh (shell) and gs/lib/board_conf.py (python) return the Radxa values from the profile,
# identical fallbacks without board lib / key, another profile is honoured, helpers are set -e safe,
# and the migrated literals are gone from fan.sh, gs.sh, oled.py, gsmenu.sh.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
want() {  # want <label> <expected> <actual>
	if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}
helpers="hw_cpu_temp_file hw_pwm_base hw_home_dir hw_wifi_iface hw_rtc_i2c_bus"
exp=('/sys/class/thermal/thermal_zone0/temp' '/sys/class/pwm/pwmchip' '/home/radxa' 'wifi0' '4')
echo "== radxa-zero3 profile"
i=0; for h in $helpers; do want "profile $h" "${exp[$i]}" "$(bash -c ". '$REPO/gs/lib/hw.sh' && $h")"; i=$((i+1)); done
echo "== fallbacks (lib copied without board.sh)"
mkdir -p "$T/nolib"; cp "$REPO/gs/lib/hw.sh" "$T/nolib/"
i=0; for h in $helpers; do want "fallback $h" "${exp[$i]}" "$(bash -c ". '$T/nolib/hw.sh' && $h")"; i=$((i+1)); done
echo "== fallbacks (unknown BOARD)"
i=0; for h in $helpers; do want "unknown-board $h" "${exp[$i]}" "$(BOARD=nope bash -c ". '$REPO/gs/lib/hw.sh' && $h")"; i=$((i+1)); done
echo "== fallbacks (profile without the keys)"
mkdir -p "$T/gs/lib" "$T/gs/boards/empty"
cp "$REPO/gs/lib/hw.sh" "$REPO/gs/lib/board.sh" "$T/gs/lib/"; printf "BOARD_ID='empty'\n" > "$T/gs/boards/empty/board.conf"
i=0; for h in $helpers; do want "no-key $h" "${exp[$i]}" "$(BOARD=empty bash -c ". '$T/gs/lib/hw.sh' && $h")"; i=$((i+1)); done
echo "== other profile is honoured (valid-pi fixture and a synthetic one)"
mkdir -p "$T/gs/boards/x"
printf "THERMAL_CPU_TEMP_FILE='/t/x'\nPWM_SYSFS_BASE='/p/x'\nHOME_DIR='/home/x'\nWIFI_ONBOARD_IFACE='wlx'\nRTC_I2C_BUS='9'\n" > "$T/gs/boards/x/board.conf"
exp2=('/t/x' '/p/x' '/home/x' 'wlx' '9')
i=0; for h in $helpers; do want "board x $h" "${exp2[$i]}" "$(BOARD=x bash -c ". '$T/gs/lib/hw.sh' && $h")"; i=$((i+1)); done
cp -r "$REPO/tests/fixtures/boards/valid-pi" "$T/gs/boards/"
exp3=('/sys/class/thermal/thermal_zone0/temp' '/sys/class/pwm/pwmchip' '/home/radxa' 'wlan9' '1')
i=0; for h in $helpers; do want "valid-pi $h" "${exp3[$i]}" "$(BOARD=valid-pi bash -c ". '$T/gs/lib/hw.sh' && $h")"; i=$((i+1)); done
echo "== set -e safe"
bash -c "set -e; . '$T/nolib/hw.sh'; hw_cpu_temp_file >/dev/null; hw_rtc_i2c_bus >/dev/null; echo survived" || bad=1
echo "== profile values and src comments"
for k in THERMAL_CPU_TEMP_FILE RTC_I2C_BUS; do
	grep -qE "^$k=" "$REPO/gs/boards/radxa-zero3/board.conf" && echo "ok $k defined" || { echo "FAIL $k missing in radxa-zero3"; bad=1; }
	n="$(grep -B2 -E "^$k=" "$REPO/gs/boards/radxa-zero3/board.conf" | grep -c '^# src: ')"
	want "src comment $k" 1 "$n"
done
grep -qx THERMAL_CPU_TEMP_FILE "$REPO/gs/boards/REQUIRED_KEYS" && echo "ok THERMAL_CPU_TEMP_FILE required" || { echo "FAIL THERMAL_CPU_TEMP_FILE not in REQUIRED_KEYS"; bad=1; }
grep -qx RTC_I2C_BUS "$REPO/gs/boards/REQUIRED_KEYS" && { echo "FAIL RTC_I2C_BUS must stay optional"; bad=1; } || echo "ok RTC_I2C_BUS optional"
echo "== scripts use the helpers, no migrated literal left"
lit() {  # lit <label> <regex> <files...>
	local l="$1" re="$2"; shift 2
	if grep -nE "$re" "$@"; then echo "FAIL literal $l"; bad=1; else echo "ok no $l literal"; fi
}
cd "$REPO/gs" || exit 2
lit thermal_zone 'thermal_zone' fan.sh gs.sh gsmenu.sh
# oled.py keeps the Radxa literal only as the board_conf.get() fallback
lit thermal_zone-oled-outside-fallback 'thermal_zone' <(grep thermal_zone oled.py | grep -v -e "board_conf.get('THERMAL_CPU_TEMP_FILE'" -e '^_CPU_TEMP_FALLBACK = ')
lit pwmchip-path '/sys/class/pwm' fan.sh
lit home-radxa '/home/radxa' gs.sh gsmenu.sh oled.py fan.sh
lit i2c-4 'i2c-4' gs.sh
lit wifi0 '^[^#]*wifi0' gs.sh fan.sh oled.py
grep -nE '^[^#]*wifi0' gsmenu.sh | grep -vE 'ifname wifi0' && { echo "FAIL literal wifi0 in gsmenu.sh"; bad=1; } || echo "ok no wifi0 literal in gsmenu.sh (except commented/nmcli ifname)"
for f in fan.sh gs.sh gsmenu.sh; do grep -q 'source /gs/lib/hw.sh' "$f" && echo "ok $f sources hw.sh" || { echo "FAIL $f does not source hw.sh"; bad=1; }; done
grep -q 'import board_conf' oled.py && echo "ok oled.py uses board_conf" || { echo "FAIL oled.py does not use board_conf"; bad=1; }
echo "== python: board_conf.py"
if command -v python3 >/dev/null 2>&1; then
	python3 -m py_compile "$REPO/gs/lib/board_conf.py" "$REPO/gs/oled.py" && echo "ok py_compile board_conf.py oled.py" || { echo "FAIL py_compile"; bad=1; }
	find "$REPO/gs" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
	mkdir -p "$T/py/boards/x" "$T/py/boards/emptyval"
	printf "# comment\nBOARD_ID='x'\nTHERMAL_CPU_TEMP_FILE='/t/py'\nBAD LINE\nDQ=\"dq val\"\nBARE=/bare/path\nINLINE='in' # trailing\n" > "$T/py/boards/x/board.conf"
	printf "THERMAL_CPU_TEMP_FILE=''\n" > "$T/py/boards/emptyval/board.conf"
	out="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/gs/lib" python3 - "$T/py/boards" <<'PY'
import sys, board_conf as b
d = [sys.argv[1]]
R = '/sys/class/thermal/thermal_zone0/temp'
print('profile', b.get('THERMAL_CPU_TEMP_FILE', R, 'x', d))
print('missing-key', b.get('NOPE', 'dflt', 'x', d))
print('unknown-board', b.get('THERMAL_CPU_TEMP_FILE', R, 'nope', d))
print('empty-value', b.get('THERMAL_CPU_TEMP_FILE', R, 'emptyval', d))
p = b.parse(d[0] + '/x/board.conf')
print('double-quoted', p['DQ'], '| bare', p['BARE'], '| inline-comment', p['INLINE'], '| bad line skipped', 'BAD' not in p)
print('missing-file', b.parse('/nonexistent/board.conf'))
print('repo-profile', b.get('THERMAL_CPU_TEMP_FILE', 'FALLBACK'), b.get('RTC_I2C_BUS', 'FALLBACK'))
PY
)" || { echo "FAIL python run"; bad=1; }
	echo "$out"
	want "py profile" "profile /t/py" "$(echo "$out" | sed -n 1p)"
	want "py missing-key" "missing-key dflt" "$(echo "$out" | sed -n 2p)"
	want "py unknown-board" "unknown-board $(printf '%s' "${exp[0]}")" "$(echo "$out" | sed -n 3p)"
	want "py empty-value" "empty-value ${exp[0]}" "$(echo "$out" | sed -n 4p)"
	want "py repo-profile" "repo-profile ${exp[0]} 4" "$(echo "$out" | sed -n 7p)"
else
	echo "SKIP python3 not found"
fi
echo "bad=$bad"
exit $bad
