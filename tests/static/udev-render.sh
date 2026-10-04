#!/usr/bin/env bash
# Static check of gs/boards/render-udev.sh: radxa-zero3 output is byte-identical to gs/98-rename.rules and gs/99-GS.rules,
# a fixture board renders its own values, and an unresolved/empty placeholder fails.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
R="$REPO/gs/boards/render-udev.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
echo "== radxa-zero3 byte identity"
"$R" radxa-zero3 "$T/radxa" || { echo "FAIL render radxa-zero3"; bad=1; }
for f in 98-rename.rules 99-GS.rules; do
	if cmp "$REPO/gs/$f" "$T/radxa/$f"; then echo "ok $f identical"; else echo "FAIL $f differs"; bad=1; fi
done
echo "== fixture board valid-pi"
"$REPO/gs/boards/validate.sh" "$REPO/tests/fixtures/boards/valid-pi" || { echo "FAIL fixture invalid"; bad=1; }
"$R" "$REPO/tests/fixtures/boards/valid-pi" "$T/pi" || { echo "FAIL render valid-pi"; bad=1; }
grep -Ev '^[[:space:]]*(#|$)' "$T/pi/98-rename.rules" "$T/pi/99-GS.rules" | sed "s#$T#<T>#"
if grep -qE 'wifi0|radxa0|aicwf_sdio|@' "$T/pi"/*.rules; then echo "FAIL radxa value or placeholder left in fixture output"; bad=1; else echo "ok no radxa literal / placeholder in fixture output"; fi
echo "== templates only use known placeholders"
grep -ohE '@[A-Z0-9_]+@' "$REPO"/gs/*.rules.in | sort -u
echo "== failures"
mkdir -p "$T/bad"; sed "s/^GADGET_IFNAME=.*/GADGET_IFNAME=''/" "$REPO/gs/boards/radxa-zero3/board.conf" > "$T/bad/board.conf"
"$R" "$T/bad" "$T/badout" 2>&1 | sed "s#$T#<T>#"; [ "${PIPESTATUS[0]}" -ne 0 ] && echo "ok empty key rejected" || { echo "FAIL empty key accepted"; bad=1; }
"$R" nope "$T/o" 2>&1 | sed "s#$REPO#<REPO>#"; [ "${PIPESTATUS[0]}" -ne 0 ] && echo "ok unknown board rejected" || { echo "FAIL unknown board accepted"; bad=1; }
echo "bad=$bad"
exit $bad
