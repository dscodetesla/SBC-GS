#!/usr/bin/env bash
# Shared helpers. Source this file; do not execute it.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
[ -f "$BENCH_DIR/env" ] && . "$BENCH_DIR/env"

: "${WFB_REGION:=}"
: "${WFB_CHANNEL:=165}"
: "${WFB_NICS:=}"
: "${TX_PWR_IDX:=1}"
: "${RTL8812AU_REF:=6e75916416de1dce5ecd37f824896bebf96aaf8f}"
: "${DRIVER:=8812au}"
: "${VIDEO_CODEC:=h264}"
: "${VIDEO_W:=1280}"
: "${VIDEO_H:=720}"
: "${VIDEO_FPS:=30}"
: "${VIDEO_BITRATE_KBPS:=4000}"
: "${VIDEO_PATTERN:=ball}"
: "${AIR_VIDEO_PORT:=5602}"
: "${GS_VIDEO_PORT:=5600}"
: "${MAV_PORT:=14550}"
: "${FC_SYSID:=1}"
: "${DECODER:=}"
: "${SINK:=}"
: "${VENV:=/opt/gs-bench/venv}"
: "${SOURCE:=test}"
: "${WEBCAM_DEV:=/dev/video0}"
: "${WEBCAM_FORMAT:=jpeg}"
: "${ENCODER:=x264}"
: "${FC_SERIAL:=}"
: "${FC_BAUD:=115200}"
: "${GS_FORWARD_IP:=}"

log()  { printf '\033[1;34m[bench]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bench:warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[bench:error]\033[0m %s\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" = "0" ] || die "run as root: sudo $0 $*"; }

role_check() {
	case "${1:-}" in
		air|gs) ;;
		*) die "role must be 'air' or 'gs'" ;;
	esac
}

have_gst_element() { gst-inspect-1.0 "$1" >/dev/null 2>&1; }

# DRY_RUN=1 prints system-changing commands instead of running them (for testing
# the scripts off-device). CFG_ROOT lets file writes go to a scratch directory.
: "${DRY_RUN:=0}"
: "${CFG_ROOT:=}"
run() { if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi; }
cfgpath() { printf '%s%s' "$CFG_ROOT" "$1"; }
