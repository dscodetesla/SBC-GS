#!/usr/bin/env bash
# gs-mavlink: build (and exec) the MAVLink router command line from /config/gs-mavlink.conf.
#   gs-mavlink.sh           validate, then exec the router
#   gs-mavlink.sh --print   validate, print the command line (and notes as "# ..."), exit 0 without exec
# Config path: $GS_MAVLINK_CONF (tests) or /config/gs-mavlink.conf, parsed as data (KEY='value' only). Exit 2 = invalid configuration.
# Only flags confirmed SRC in docs/MAVLINK-ROUTER.md are emitted; anything else is a "# UNVERIFIED" note.
set -u

CONF="${GS_MAVLINK_CONF:-/config/gs-mavlink.conf}"
mode=run
case "${1:-}" in
	--print) mode=print ;;
	"") ;;
	*) echo "usage: gs-mavlink.sh [--print]" >&2; exit 2 ;;
esac

# Configuration: layered loader (config/load.sh, docs/CONFIG.md). The config file is parsed as DATA (strict KEY='value'
# lines, never sourced: /config is writable through anonymous Samba, audit S1) and the defaults live in config/registry.tsv.
# Precedence: environment SBC_GS_<KEY> > $CONF > /config/sbc-gs.env > profile ($SBC_GS_PROFILE) > built-in default.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOADER=""
for d in "${SBC_GS_CONFIG_DIR:-}" "$HERE/../../config" "$HERE/../config"; do
	[ -n "$d" ] && [ -r "$d/load.sh" ] || continue
	real="$(cd "$d" && pwd -P)"
	case "$real" in /config|/config/*) continue ;; esac   # never run code from the writable config partition
	LOADER="$real/load.sh"; break
done
[ -n "$LOADER" ] || { echo "gs-mavlink: error: config/load.sh not found (install gs/ together with config/)" >&2; exit 2; }
# shellcheck source=../../config/load.sh
. "$LOADER"
# value checks stay in this script (its messages are covered by tests/golden/static/gs-mavlink.out): --no-value-check
sbc_cfg_load --no-value-check --extra-file "$CONF" --extra-owner gs-mavlink gs-mavlink

die() { echo "gs-mavlink: error: $*" >&2; exit 2; }
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
chk_port() {  # name value
	is_int "$2" && [ "${#2}" -le 5 ] && [ "$2" -ge 1 ] && [ "$2" -le 65535 ] || die "$1='$2' is not a port in 1..65535"
}
chk_ipv4() {  # name value  (whole-string match: a trailing dot or a fifth field is not an address, D4)
	local o name="$1" v="$2"
	[[ "$v" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || die "$name is not an IPv4 address"  # cfg-ok: IPv4 syntax
	for o in 1 2 3 4; do [ $((10#${BASH_REMATCH[o]})) -le 255 ] || die "$name is not an IPv4 address"; done  # cfg-ok: octet max
}
chk_flag() { case "$2" in 0|1) ;; *) die "$1='$2' must be 0 or 1" ;; esac; }

case "$ROUTER" in
	mavp2p|mavlink-router) ;;
	*) die "ROUTER='$ROUTER' unknown (use mavp2p or mavlink-router)" ;;
esac
for f in TCP_ENABLE HB_DISABLE STREAMREQ_DISABLE DUMP_ENABLE; do chk_flag "$f" "${!f}"; done

chk_ipv4 UPSTREAM_BIND "$UPSTREAM_BIND"
chk_ipv4 LISTEN_ADDR "$LISTEN_ADDR"
chk_port UPSTREAM_PORT "$UPSTREAM_PORT"
chk_port TCP_PORT "$TCP_PORT"
is_int "$HB_SYSID" && [ "$HB_SYSID" -ge 1 ] && [ "$HB_SYSID" -le 254 ] || die "HB_SYSID='$HB_SYSID' must be an integer in 1..254 (255 is the GCS sysid)"
[ "$HB_SYSID" != 3 ] || die "HB_SYSID=3 is the sysid wfb-ng injects: ambiguous"

# every listening port must be unique
used=" $((10#$UPSTREAM_PORT))"   # cfg-ok: base 10; compared as numbers: 04560 and 4560 are the same port (D5)
[ "$TCP_ENABLE" = 1 ] && used="$used $((10#$TCP_PORT))"  # cfg-ok: base 10
gcs_ports=()
# shellcheck disable=SC2206  # intentional word splitting of a space-separated list
gcs_ports=($GCS_UDP_PORTS)
for p in "${gcs_ports[@]}"; do
	chk_port GCS_UDP_PORTS "$p"
	pn=$((10#$p))  # cfg-ok: base 10
	case "$used " in *" $pn "*) die "port $p is used twice (upstream, GCS UDP and TCP ports must differ)" ;; esac
	used="$used $pn"
done
gcs_clients=()
# shellcheck disable=SC2206
gcs_clients=($GCS_UDP_CLIENTS)
for c in "${gcs_clients[@]}"; do
	case "$c" in *:*) ;; *) die "GCS_UDP_CLIENTS entry '$c' must be ip:port" ;; esac
	chk_ipv4 "GCS_UDP_CLIENTS entry '$c'" "${c%:*}"
	chk_port GCS_UDP_CLIENTS "${c##*:}"
done
if [ -n "$SERIAL_DEV" ]; then
	case "$SERIAL_DEV" in /dev/*[!A-Za-z0-9._/-]*|/dev/) die "SERIAL_DEV='$SERIAL_DEV' has unsafe characters" ;; /dev/*) ;; *) die "SERIAL_DEV='$SERIAL_DEV' must be under /dev/" ;; esac
	case "$SERIAL_DEV" in */../*|*/..) die "SERIAL_DEV='$SERIAL_DEV' must not contain '..'" ;; esac   # D6
	is_int "$SERIAL_BAUD" && [ "$SERIAL_BAUD" -ge 1200 ] && [ "$SERIAL_BAUD" -le 3000000 ] || die "SERIAL_BAUD='$SERIAL_BAUD' must be 1200..3000000"
fi
case "$DUMP_PATH" in /*[!A-Za-z0-9._/%:-]*|/) die "DUMP_PATH has unsafe characters" ;; /*) ;; *) die "DUMP_PATH must be absolute" ;; esac
case "$DUMP_PATH" in */../*|*/..) die "DUMP_PATH must not contain '..'" ;; esac   # D6

notes=(); cmd=()
if [ "$ROUTER" = mavp2p ]; then
	cmd=(mavp2p)
	[ -n "$SERIAL_DEV" ] && cmd+=("serial:$SERIAL_DEV:$SERIAL_BAUD")
	cmd+=("udps:$UPSTREAM_BIND:$UPSTREAM_PORT")
	for p in "${gcs_ports[@]}"; do cmd+=("udps:$LISTEN_ADDR:$p"); done
	for c in "${gcs_clients[@]}"; do cmd+=("udpc:$c"); done
	[ "$TCP_ENABLE" = 1 ] && cmd+=("tcps:$LISTEN_ADDR:$TCP_PORT")
	if [ "$HB_DISABLE" = 1 ]; then cmd+=(--hb-disable); else cmd+=("--hb-systemid=$HB_SYSID"); fi
	[ "$STREAMREQ_DISABLE" = 1 ] && cmd+=(--streamreq-disable)
	[ "$DUMP_ENABLE" = 1 ] && cmd+=(--dump "--dump-path=$DUMP_PATH")
	notes+=("mavp2p has no filters: single RC writer is enforced by MAV_GCS_SYSID on the FC and by the network (docs/GS-MAVLINK.md)")
else
	[ -z "$SERIAL_DEV" ] || die "SERIAL_DEV with ROUTER=mavlink-router is UNVERIFIED (one positional argument only confirmed); use mavp2p"
	[ "${#gcs_ports[@]}" -eq 0 ] || die "GCS_UDP_PORTS is mavp2p-only; use GCS_UDP_CLIENTS (-e ip:port) or TCP with ROUTER=mavlink-router"
	cmd=(mavlink-routerd)
	for c in "${gcs_clients[@]}"; do cmd+=(-e "$c"); done
	if [ "$TCP_ENABLE" = 1 ]; then cmd+=(-t "$TCP_PORT"); else cmd+=(-t 0); fi
	cmd+=("$UPSTREAM_BIND:$UPSTREAM_PORT")
	notes+=("UNVERIFIED: mavlink-router heartbeat sysid (HB_SYSID not applied), tlog dump flags, BlockMsgIdIn=70 needs a config file, none generated")
fi

if [ "$mode" = print ]; then
	for n in "${notes[@]}"; do echo "# $n"; done
	echo "${cmd[*]}"
	exit 0
fi
for n in "${notes[@]}"; do echo "gs-mavlink: note: $n" >&2; done
exec "${cmd[@]}"
