#!/usr/bin/env bash
# gs-mavlink: build (and exec) the MAVLink router command line from /config/gs-mavlink.conf.
#   gs-mavlink.sh           validate, then exec the router
#   gs-mavlink.sh --print   validate, print the command line (and notes as "# ..."), exit 0 without exec
# Config path: $GS_MAVLINK_CONF (tests) or /config/gs-mavlink.conf. Exit 2 = invalid configuration.
# Only flags confirmed SRC in docs/MAVLINK-ROUTER.md are emitted; anything else is a "# UNVERIFIED" note.
set -u

CONF="${GS_MAVLINK_CONF:-/config/gs-mavlink.conf}"
mode=run
case "${1:-}" in
	--print) mode=print ;;
	"") ;;
	*) echo "usage: gs-mavlink.sh [--print]" >&2; exit 2 ;;
esac

# defaults (see gs-mavlink.conf.example)
ROUTER='mavp2p'
UPSTREAM_BIND='0.0.0.0'; UPSTREAM_PORT='14550'
GCS_UDP_PORTS='14560'; GCS_UDP_CLIENTS=''
TCP_ENABLE='0'; TCP_PORT='5760'
SERIAL_DEV=''; SERIAL_BAUD='115200'
HB_SYSID='125'; HB_DISABLE='0'; STREAMREQ_DISABLE='0'
DUMP_ENABLE='0'; DUMP_PATH='/var/log/gs-mavlink/2006-01-02_15-04-05.tlog'

if [ -f "$CONF" ]; then
	# shellcheck disable=SC1090  # user config, path is dynamic
	. "$CONF" || { echo "gs-mavlink: cannot read $CONF" >&2; exit 2; }
fi

die() { echo "gs-mavlink: error: $*" >&2; exit 2; }
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
chk_port() {  # name value
	is_int "$2" && [ "${#2}" -le 5 ] && [ "$2" -ge 1 ] && [ "$2" -le 65535 ] || die "$1='$2' is not a port in 1..65535"
}
chk_ipv4() {  # name value
	local o IFS=.
	# shellcheck disable=SC2086  # intentional split on dots
	set -- $2
	[ "$#" -eq 4 ] || die "$1 is not an IPv4 address"
	for o in "$@"; do is_int "$o" && [ "${#o}" -le 3 ] && [ "$o" -le 255 ] || die "$1 is not an IPv4 address"; done
}
chk_flag() { case "$2" in 0|1) ;; *) die "$1='$2' must be 0 or 1" ;; esac; }

case "$ROUTER" in
	mavp2p|mavlink-router) ;;
	*) die "ROUTER='$ROUTER' unknown (use mavp2p or mavlink-router)" ;;
esac
for f in TCP_ENABLE HB_DISABLE STREAMREQ_DISABLE DUMP_ENABLE; do chk_flag "$f" "${!f}"; done

chk_ipv4 UPSTREAM_BIND "$UPSTREAM_BIND"
chk_port UPSTREAM_PORT "$UPSTREAM_PORT"
chk_port TCP_PORT "$TCP_PORT"
is_int "$HB_SYSID" && [ "$HB_SYSID" -ge 1 ] && [ "$HB_SYSID" -le 254 ] || die "HB_SYSID='$HB_SYSID' must be an integer in 1..254 (255 is the GCS sysid)"
[ "$HB_SYSID" != 3 ] || die "HB_SYSID=3 is the sysid wfb-ng injects: ambiguous"

# every listening port must be unique
used=" $UPSTREAM_PORT"
[ "$TCP_ENABLE" = 1 ] && used="$used $TCP_PORT"
gcs_ports=()
# shellcheck disable=SC2206  # intentional word splitting of a space-separated list
gcs_ports=($GCS_UDP_PORTS)
for p in "${gcs_ports[@]}"; do
	chk_port GCS_UDP_PORTS "$p"
	case "$used " in *" $p "*) die "port $p is used twice (upstream, GCS UDP and TCP ports must differ)" ;; esac
	used="$used $p"
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
	is_int "$SERIAL_BAUD" && [ "$SERIAL_BAUD" -ge 1200 ] && [ "$SERIAL_BAUD" -le 3000000 ] || die "SERIAL_BAUD='$SERIAL_BAUD' must be 1200..3000000"
fi
case "$DUMP_PATH" in /*[!A-Za-z0-9._/%:-]*|/) die "DUMP_PATH has unsafe characters" ;; /*) ;; *) die "DUMP_PATH must be absolute" ;; esac

notes=(); cmd=()
if [ "$ROUTER" = mavp2p ]; then
	cmd=(mavp2p)
	[ -n "$SERIAL_DEV" ] && cmd+=("serial:$SERIAL_DEV:$SERIAL_BAUD")
	cmd+=("udps:$UPSTREAM_BIND:$UPSTREAM_PORT")
	for p in "${gcs_ports[@]}"; do cmd+=("udps:0.0.0.0:$p"); done
	for c in "${gcs_clients[@]}"; do cmd+=("udpc:$c"); done
	[ "$TCP_ENABLE" = 1 ] && cmd+=("tcps:0.0.0.0:$TCP_PORT")
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
