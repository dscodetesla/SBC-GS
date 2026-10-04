#!/usr/bin/env bash
# Static check of gs/lib/otg.sh: Radxa values from the profile, identical fallbacks without board lib / key,
# a different board profile is honoured, and every helper succeeds (set -e safe).
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
want() {  # want <label> <expected> <actual>
	if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}
helpers="otg_controller otg_mode_file otg_mass_default otg_mass_alt"
echo "== radxa-zero3 profile"
for h in $helpers; do echo "$h=$(bash -c ". '$REPO/gs/lib/otg.sh' && $h")"; done
exp=('fcc00000.dwc3' '/sys/kernel/debug/usb/fcc00000.dwc3/mode' '/dev/mmcblk0p4' '/dev/mmcblk1p4')
i=0; for h in $helpers; do want "profile $h" "${exp[$i]}" "$(bash -c ". '$REPO/gs/lib/otg.sh' && $h")"; i=$((i+1)); done
echo "== fallbacks (lib copied without board.sh)"
mkdir -p "$T/nolib"; cp "$REPO/gs/lib/otg.sh" "$T/nolib/"
i=0; for h in $helpers; do want "fallback $h" "${exp[$i]}" "$(bash -c ". '$T/nolib/otg.sh' && $h")"; i=$((i+1)); done
echo "== fallbacks (unknown BOARD)"
i=0; for h in $helpers; do want "unknown-board $h" "${exp[$i]}" "$(BOARD=nope bash -c ". '$REPO/gs/lib/otg.sh' && $h")"; i=$((i+1)); done
echo "== other profile is honoured"
mkdir -p "$T/gs/lib" "$T/gs/boards/x"
cp "$REPO/gs/lib/otg.sh" "$REPO/gs/lib/board.sh" "$T/gs/lib/"
printf "OTG_CONTROLLER='ctl.x'\nOTG_MODE_FILE='/m/x'\nOTG_MASS_STORAGE_DEFAULT='/dev/a'\nOTG_MASS_STORAGE_ALT='/dev/b'\n" > "$T/gs/boards/x/board.conf"
exp2=('ctl.x' '/m/x' '/dev/a' '/dev/b')
i=0; for h in $helpers; do want "board x $h" "${exp2[$i]}" "$(BOARD=x bash -c ". '$T/gs/lib/otg.sh' && $h")"; i=$((i+1)); done
echo "== set -e safe"
bash -c "set -e; . '$T/nolib/otg.sh'; otg_mode_file >/dev/null; otg_mass_alt >/dev/null; echo survived" || bad=1
echo "== scripts use the helper, no hard-coded controller left"
if grep -nE 'fcc00000|mmcblk[01]p4' "$REPO/gs/otg-gadget.sh" "$REPO/gs/button.sh"; then echo "FAIL literals in scripts"; bad=1; else echo "ok no literals in otg-gadget.sh/button.sh"; fi
echo "bad=$bad"
exit $bad
