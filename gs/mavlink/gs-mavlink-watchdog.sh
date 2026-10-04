#!/usr/bin/env bash
# Launcher of the FC-loss watchdog (gs-mavlink-watchdog.py): reads the same configuration as gs-mavlink.sh (docs/CONFIG.md), then execs the
# watchdog against the first GCS UDP port of the router. Exit 2 = invalid configuration. Print only: gs-mavlink-watchdog.sh --print.
set -u
CONF="${GS_MAVLINK_CONF:-/config/gs-mavlink.conf}"
mode=run
case "${1:-}" in
	--print) mode=print ;;
	"") ;;
	*) echo "usage: gs-mavlink-watchdog.sh [--print]" >&2; exit 2 ;;
esac
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOADER=""
for d in "${SBC_GS_CONFIG_DIR:-}" "$HERE/../../config" "$HERE/../config"; do
	[ -n "$d" ] && [ -r "$d/load.sh" ] || continue
	real="$(cd "$d" && pwd -P)"
	case "$real" in /config|/config/*) continue ;; esac   # never run code from the writable config partition
	LOADER="$real/load.sh"; break
done
[ -n "$LOADER" ] || { echo "gs-mavlink-watchdog: error: config/load.sh not found (install gs/ together with config/)" >&2; exit 2; }
# shellcheck source=../../config/load.sh
. "$LOADER"
sbc_cfg_load --extra-file "$CONF" --extra-owner gs-mavlink gs-mavlink
# shellcheck disable=SC2206  # a space-separated list of ports
ports=($GCS_UDP_PORTS)
[ "${#ports[@]}" -ge 1 ] || { echo "gs-mavlink-watchdog: error: GCS_UDP_PORTS is empty (the watchdog needs a router UDP server port)" >&2; exit 2; }
args=(--target "${WD_TARGET_HOST}:$((10#${ports[0]}))" --fc-sysid "$WD_FC_SYSID" --loss-s "$WD_LOSS_S" --restart-after-s "$WD_RESTART_AFTER_S"   # cfg-ok: base 10
	--max-restarts-per-h "$WD_MAX_RESTARTS_PER_H" --hb-sysid "$WD_HB_SYSID" --state-file "$WD_STATE_FILE")
[ -z "$SERIAL_DEV" ] || args+=(--serial-dev "$SERIAL_DEV")
if [ "$mode" = print ]; then echo "gs-mavlink-watchdog.py ${args[*]}"; exit 0; fi
exec python3 "$HERE/gs-mavlink-watchdog.py" "${args[@]}"
