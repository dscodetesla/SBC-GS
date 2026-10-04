#!/usr/bin/env bash
# Board auto-detection (gs/lib/board.sh) and the NetworkManager bridge helper (gs/lib/net.sh): precedence BOARD env > /etc/gs-board >
# device-tree model > radxa-zero3; hostile input is data, never code; br0 through nmcli is idempotent and validated.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
det() { # env... ; prints the detected board id
	env -i PATH="$PATH" "$@" bash -c ". '$REPO/gs/lib/board.sh' && printf %s \"\$BOARD\""
}
chk() { # name expected actual
	if [ "$2" = "$3" ]; then echo "ok $1 -> $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}
printf 'Raspberry Pi 5 Model B Rev 1.0\0' > "$T/dt5"
printf 'Raspberry Pi 4 Model B Rev 1.4\0' > "$T/dt4"
printf 'Radxa ZERO 3W\0' > "$T/dtr"
printf 'Some Other Board\0' > "$T/dtx"
echo rpi4 > "$T/id4"
printf '$(touch %s/pwned)\n' "$T" > "$T/idbad"
echo "== precedence"
chk "dt model Pi 5" rpi5 "$(det GS_BOARD_FILE=/nonexistent GS_DT_MODEL_FILE="$T/dt5")"
chk "dt model Pi 4" rpi4 "$(det GS_BOARD_FILE=/nonexistent GS_DT_MODEL_FILE="$T/dt4")"
chk "dt model Radxa" radxa-zero3 "$(det GS_BOARD_FILE=/nonexistent GS_DT_MODEL_FILE="$T/dtr")"
chk "unknown dt model falls back to radxa-zero3" radxa-zero3 "$(det GS_BOARD_FILE=/nonexistent GS_DT_MODEL_FILE="$T/dtx")"
chk "no dt model, no file" radxa-zero3 "$(det GS_BOARD_FILE=/nonexistent GS_DT_MODEL_FILE=/nonexistent)"
chk "/etc/gs-board beats dt model" rpi4 "$(det GS_BOARD_FILE="$T/id4" GS_DT_MODEL_FILE="$T/dt5")"
chk "BOARD env beats /etc/gs-board" rpi5 "$(det BOARD=rpi5 GS_BOARD_FILE="$T/id4" GS_DT_MODEL_FILE="$T/dt4")"
echo "== hostile input"
chk "invalid /etc/gs-board ignored, dt model used" rpi5 "$(det GS_BOARD_FILE="$T/idbad" GS_DT_MODEL_FILE="$T/dt5")"
[ -e "$T/pwned" ] && { echo "FAIL command in /etc/gs-board executed"; bad=1; } || echo "ok nothing executed"
env -i PATH="$PATH" BOARD='../x' bash -c ". '$REPO/gs/lib/board.sh'" >/dev/null 2>&1; rc=$?
[ "$rc" -ne 0 ] && echo "ok BOARD=../x rejected" || { echo "FAIL BOARD=../x accepted"; bad=1; }
echo "== profile keys the migration relies on"
for b in radxa-zero3 rpi4 rpi5; do
	for k in DT_MODEL_PREFIX NET_BACKEND; do
		grep -q "^$k='" "$REPO/gs/boards/$b/board.conf" && echo "ok $b $k" || { echo "FAIL $b lacks $k"; bad=1; }
	done
done
chk "radxa NET_BACKEND" networkd "$(env -i PATH="$PATH" BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/board.sh'; . '$REPO/gs/lib/hw.sh'; hw_net_backend")"
chk "rpi5 NET_BACKEND" networkmanager "$(env -i PATH="$PATH" BOARD=rpi5 bash -c ". '$REPO/gs/lib/board.sh'; . '$REPO/gs/lib/hw.sh'; hw_net_backend")"
echo "== nmcli bridge"
SB="$T/sb"; mkdir -p "$SB"
cp "$REPO/tests/shims/nmcli" "$SB/nmcli"; : > "$SB/sleep"; printf '#!/bin/sh\nexit 0\n' > "$SB/sleep"; chmod +x "$SB/sleep"
run() { env -i PATH="$SB:$PATH" SHIM_ROOT="$T" SHIM_LOG="$T/shim.log" bash -c ". '$REPO/gs/lib/net.sh'; $*" 2>&1; }
run "gs_net_nm_bridge br0 192.168.1.20/24 10.0.36.254/24 eth0 usb0" > "$T/o1"; echo "rc=$?"
grep -c "connection add" "$T/shim.log" 2>/dev/null | sed 's/^/adds: /'
run "gs_net_nm_bridge 'br0;x' 1.2.3.4/24 5.6.7.8/24" | sed "s#$T#<T>#"
run "gs_net_nm_bridge br0 1.2.3.4 5.6.7.8/24" | sed "s#$T#<T>#"
run "gs_net_nm_bridge br0 1.2.3.4/24 5.6.7.8/24 'e th'" | sed "s#$T#<T>#" | head -3
echo "bad=$bad"
exit $bad
