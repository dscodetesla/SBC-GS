#!/usr/bin/env bash
# Static check of the board contract: validate.sh must pass on gs/boards/radxa-zero3
# and must fail on every negative fixture under tests/fixtures/boards/.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
V="$REPO/gs/boards/validate.sh"
bad=0
echo "== positive"
out="$("$V" "$REPO/gs/boards/radxa-zero3" 2>&1)"; rc=$?
echo "$out"
[ $rc -eq 0 ] && echo "ok radxa-zero3 passes" || { echo "FAIL radxa-zero3 rejected"; bad=1; }
echo "== negative"
for d in "$REPO"/tests/fixtures/boards/*/; do
	[ -f "$d/VALID" ] && continue   # positive fixture (used by static/udev-render)
	n="$(basename "$d")"
	out="$("$V" "${d%/}" 2>&1)"; rc=$?
	echo "$out" | sed "s#$REPO#<REPO>#g"
	[ $rc -ne 0 ] && echo "ok $n rejected" || { echo "FAIL $n accepted"; bad=1; }
done
echo "== loader"
for k in BOARD_ID OTG_CONTROLLER; do
	echo "$k=$(BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/board.sh' && board_get $k" 2>&1)"
done
BOARD=nope bash -c ". '$REPO/gs/lib/board.sh'" >/dev/null 2>&1 && { echo "FAIL unknown board accepted"; bad=1; } || echo "ok unknown board rejected"
echo "bad=$bad"
exit $bad
