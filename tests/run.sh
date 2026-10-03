#!/usr/bin/env bash
# shellcheck disable=SC2034  # case variables (invocation, sleep_limit, dump_*) are read by lib/sandbox.sh
# Golden-output regression runner for gs/*.sh (no hardware needed).
#   tests/run.sh                 compare against tests/golden
#   tests/run.sh --update [case] rewrite golden output (do this ONLY on intended changes)
#   tests/run.sh <suite>/<case>  run one case, e.g. applyconf/default
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib/sandbox.sh"

update=0; [ "${1:-}" = "--update" ] && { update=1; shift; }
want="${1:-}"
fail=0; ran=0

for casefile in "$HERE"/cases/*/*.sh; do
	suite="$(basename "$(dirname "$casefile")")"; name="$(basename "$casefile" .sh)"
	[ -n "$want" ] && [ "$want" != "$suite/$name" ] && continue
	script_under_test=""
	invocation=sourced; sleep_limit=0; gpioset_limit=0; sort_shim_log=0; rewrite_console=0; rewrite_extra=(); script_args=(); dump_baseline=1; dump_files=(); dump_trees=()
	case_setup() { :; }
	# shellcheck disable=SC1090
	. "$casefile"
	sb_new; case_setup; sb_run "$script_under_test"
	out="$(mktemp)"; sb_dump > "$out"; sb_clean
	golden="$HERE/golden/$suite/$name.out"
	ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  $suite/$name"
	elif [ ! -f "$golden" ]; then echo "MISSING  $suite/$name (run: tests/run.sh --update $suite/$name)"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       $suite/$name"
	else echo "DIFF     $suite/$name"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
done
# static check of the udev rules (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/udev" ]; then
	out="$(mktemp)"; "$HERE/static/udev.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/udev.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/udev"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/udev"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/udev"
	else echo "DIFF     static/udev"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the board contract (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/boards" ]; then
	out="$(mktemp)"; "$HERE/static/boards.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/boards.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/boards"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/boards"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/boards"
	else echo "DIFF     static/boards"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the GPIO helper (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/gpio" ]; then
	out="$(mktemp)"; "$HERE/static/gpio.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/gpio.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/gpio"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/gpio"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/gpio"
	else echo "DIFF     static/gpio"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the unpinned-fetch ratchet (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/pins" ]; then
	out="$(mktemp)"; "$HERE/static/pins.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/pins.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/pins"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/pins"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/pins"
	else echo "DIFF     static/pins"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of insecure defaults (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/security-defaults" ]; then
	out="$(mktemp)"; "$HERE/static/security-defaults.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/security-defaults.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/security-defaults"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/security-defaults"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/security-defaults"
	else echo "DIFF     static/security-defaults"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the gs-mavlink command line (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/gs-mavlink" ]; then
	out="$(mktemp)"; "$HERE/static/gs-mavlink.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/gs-mavlink.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/gs-mavlink"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/gs-mavlink"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/gs-mavlink"
	else echo "DIFF     static/gs-mavlink"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the OTG helper (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/otg" ]; then
	out="$(mktemp)"; "$HERE/static/otg.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/otg.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/otg"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/otg"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/otg"
	else echo "DIFF     static/otg"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the DRAFT Raspberry Pi 4 profile (UNVERIFIED ratchet, M6) (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/rpi4-draft" ]; then
	out="$(mktemp)"; "$HERE/static/rpi4-draft.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/rpi4-draft.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/rpi4-draft"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/rpi4-draft"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/rpi4-draft"
	else echo "DIFF     static/rpi4-draft"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the DRAFT Raspberry Pi 5 profile (UNVERIFIED ratchet, Bookworm base) (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/rpi5-draft" ]; then
	out="$(mktemp)"; "$HERE/static/rpi5-draft.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/rpi5-draft.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/rpi5-draft"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/rpi5-draft"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/rpi5-draft"
	else echo "DIFF     static/rpi5-draft"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the Pi 5 / Ubuntu-host fixes: driver patch series, fan/OTG guards per board, blacklist (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/driver-guards" ]; then
	out="$(mktemp)"; "$HERE/static/driver-guards.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/driver-guards.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/driver-guards"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/driver-guards"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/driver-guards"
	else echo "DIFF     static/driver-guards"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the host decoder/sink choice of bench/video-rx.sh (V4L2 -> VA-API -> software) with shimmed gst tools
if [ -z "$want" ] || [ "$want" = "static/host-decode" ]; then
	out="$(mktemp)"; "$HERE/static/host-decode.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/host-decode.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/host-decode"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/host-decode"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/host-decode"
	else echo "DIFF     static/host-decode"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the udev template renderer (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/udev-render" ]; then
	out="$(mktemp)"; "$HERE/static/udev-render.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/udev-render.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/udev-render"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/udev-render"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/udev-render"
	else echo "DIFF     static/udev-render"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the hardware path helpers (M3d) (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/paths" ]; then
	out="$(mktemp)"; "$HERE/static/paths.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/paths.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/paths"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/paths"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/paths"
	else echo "DIFF     static/paths"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the optional board-contract extensions (GPIO_PIN_NUMBERING/MAP, DTBO_MODE, PART_TABLE) (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/contract-ext" ]; then
	out="$(mktemp)"; "$HERE/static/contract-ext.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/contract-ext.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/contract-ext"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/contract-ext"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/contract-ext"
	else echo "DIFF     static/contract-ext"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the verified-fetch helpers build/lib/fetch.sh (M5 step 1) (no sandbox, no network)
if [ -z "$want" ] || [ "$want" = "static/fetch" ]; then
	out="$(mktemp)"; "$HERE/static/fetch.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/fetch.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/fetch"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/fetch"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/fetch"
	else echo "DIFF     static/fetch"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static+functional check of the chroot-env block in build/release.sh (audit S2)
if [ -z "$want" ] || [ "$want" = "static/release-env" ]; then
	out="$(mktemp)"; "$HERE/static/release-env.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/release-env.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/release-env"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/release-env"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/release-env"
	else echo "DIFF     static/release-env"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
# static check of the layered configuration, the gs-mavlink S1 fix and the hardcode ratchet (no sandbox needed)
if [ -z "$want" ] || [ "$want" = "static/config" ]; then
	out="$(mktemp)"; "$HERE/static/config.sh" > "$out" 2>&1 || fail=1
	golden="$HERE/golden/static/config.out"; ran=$((ran+1))
	if [ "$update" = 1 ]; then mkdir -p "$(dirname "$golden")"; cp "$out" "$golden"; echo "updated  static/config"
	elif [ ! -f "$golden" ]; then echo "MISSING  static/config"; fail=1
	elif diff -u "$golden" "$out" > "$out.diff"; then echo "ok       static/config"
	else echo "DIFF     static/config"; sed 's/^/    /' "$out.diff" | head -40; fail=1; fi
	rm -f "$out" "$out.diff"
fi
[ "$ran" -gt 0 ] || { echo "no cases matched"; exit 2; }
exit $fail
