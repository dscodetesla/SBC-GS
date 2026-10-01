#!/usr/bin/env bash
# Dispatcher. Usage:
#   sudo ./bench.sh setup <air|gs>   # packages, driver, wfb-ng, config, services (reboot between driver and the rest)
#   ./bench.sh check <air|gs>
#   ./bench.sh loopback              # no-radio self test of the application layer
. "$(dirname "$0")/lib.sh"
cmd="${1:-}"; role="${2:-}"
case "$cmd" in
	setup)
		role_check "$role"; need_root "$@"
		"$BENCH_DIR/setup-common.sh" "$role"
		"$BENCH_DIR/install-driver.sh"
		warn "REBOOT now if this is the first driver install, then run:  sudo ./bench.sh finish $role"
		;;
	finish)
		role_check "$role"; need_root "$@"
		"$BENCH_DIR/install-wfb.sh" "$role"
		"$BENCH_DIR/configure-wfb.sh" "$role"
		"$BENCH_DIR/install-services.sh" "$role"
		"$BENCH_DIR/check.sh" "$role" || true
		;;
	check)    role_check "$role"; exec "$BENCH_DIR/check.sh" "$role" ;;
	loopback) exec "$BENCH_DIR/loopback-test.sh" ;;
	*) sed -n '2,6p' "$0"; echo "  sudo ./bench.sh finish <air|gs>  # after the reboot"; exit 2 ;;
esac
