#!/usr/bin/env bash
# Shared helpers. Source this file; do not execute it.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layered configuration (docs/CONFIG.md): environment > $BENCH_DIR/env (read as DATA, never sourced) > per-host file
# > profile (SBC_GS_PROFILE) > built-in default from config/registry.tsv. The legacy bare variable names still work.
# shellcheck source=../config/load.sh
. "$BENCH_DIR/../config/load.sh" || { echo "bench: cannot load $BENCH_DIR/../config/load.sh" >&2; exit 1; }
sbc_cfg_load --extra-file "$BENCH_DIR/env" --extra-owner bench bench

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
run() { if [ "$DRY_RUN" = 1 ]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi; }
cfgpath() { printf '%s%s' "$CFG_ROOT" "$1"; }
