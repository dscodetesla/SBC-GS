#!/usr/bin/env bash
# Sandbox helpers for running gs/*.sh without target hardware.
# Absolute paths in a COPY of the script are rewritten into a temporary root, and
# system-changing commands are replaced by logging shims. Sourced, not executed.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Shims: special ones live as files in tests/shims/ (copied into the sandbox PATH dir); the names below are
# generic log-only shims (log argv to $SHIM_LOG, succeed)
LOGONLY="chroot mount reboot modprobe poweroff systemd-run umount iptables ip hwclock fsck.exfat wfb_rx sgdisk resize2fs mkfs.exfat dtc setfont sync gst-launch-1.0 msposd wfb-ng-osd fbi jq yq"

sb_new() {
	ROOT="$(mktemp -d)"
	export SHIM_LOG="$ROOT/shim.log"; : > "$SHIM_LOG"
	mkdir -p "$ROOT"/{etc/kernel,etc/default,etc/samba,etc/systemd/network,etc/network/interfaces.d,media/root-ro/etc,boot/dtbo,config,sys/class/net,shims}
	local n
	for n in $LOGONLY; do
		printf '#!/bin/bash\necho "%s $*" >> "$SHIM_LOG"\nexit 0\n' "$n" > "$ROOT/shims/$n"; chmod +x "$ROOT/shims/$n"
	done
	cp "$REPO/tests/shims/"* "$ROOT/shims/"
	# stubs for the other scripts of the product, so cross-script calls are logged and never run for real
	mkdir -p "$ROOT/gs"
	for n in otg-gadget.sh channel-scan.sh stream.sh gs-applyconf.sh fan.sh wfb.sh button.sh; do
		printf '%s\n' '#!/bin/bash' 'echo "stub ${BASH_SOURCE[0]##*/} $*" >> "$SHIM_LOG"' > "$ROOT/gs/$n"; chmod +x "$ROOT/gs/$n"
	done
	# real libs and board profiles, so scripts that source /gs/lib/*.sh resolve them inside the sandbox
	cp -r "$REPO/gs/lib" "$REPO/gs/boards" "$ROOT/gs/"
	# the OTG role-switch file lives under /sys: point the sandbox copies (lib fallback + profile) at the sandbox /sys too
	sed -i -E "s#(^|[^A-Za-z0-9_./-])/sys/#\1$ROOT/sys/#g" "$ROOT/gs/lib/otg.sh" "$ROOT"/gs/boards/*/board.conf
	export SHIM_ROOT="$ROOT"
	# baseline target state (Radxa defaults taken from the repo)
	sed "s#^rec_dir=.*#rec_dir='$ROOT/Videos'#" "$REPO/gs/gs.conf" > "$ROOT/etc/gs.conf"
	cp "$ROOT/etc/gs.conf" "$ROOT/gs.conf.orig"
	echo "root=LABEL=rootfs console=ttyS2,1500000n8" > "$ROOT/etc/kernel/cmdline"
	printf '/dev/mmcblk0p5 /old exfat defaults,nofail 0 0\n' > "$ROOT/media/root-ro/etc/fstab"
	printf '[global]\n[Videos]\n   path = /old\n' > "$ROOT/etc/samba/smb.conf"
}

conf_set() { sed -i "s#^$1=.*#$1='$2'#" "$ROOT/etc/gs.conf"; }
dtbo()     { : > "$ROOT/boot/dtbo/$1"; }

# rewrite /etc /boot /media /config /sys /proc /run /gs /home /tmp when NOT preceded by a path character
# shellcheck disable=SC2154
sb_rewrite() {
	sed -E "s#(^|[^A-Za-z0-9_./-])/(etc|boot|media|config|sys|proc|run|gs|home|tmp)/#\1$ROOT/\2/#g" "$1" > "$2"
	# case option rewrite_extra=(/dev/shm ...): more absolute prefixes to move into the sandbox
	local x
	for x in ${rewrite_extra[@]+"${rewrite_extra[@]}"}; do
		mkdir -p "$ROOT$x"; sed -i -E "s#(^|[^A-Za-z0-9_./-])$x([^A-Za-z0-9_]|\$)#\1$ROOT$x\2#g;s#(^|[^A-Za-z0-9_./-])$x([^A-Za-z0-9_]|\$)#\1$ROOT$x\2#g" "$2"
	done
	# case option rewrite_console=1: gs-init.sh tees to /dev/ttyFIQ0 and /dev/tty1; keep that off the real /dev
	if [ "${rewrite_console:-0}" = 1 ]; then
		mkdir -p "$ROOT/dev"; sed -i -E "s#(^|[^A-Za-z0-9_./-])/dev/(ttyFIQ0|tty1)([^A-Za-z0-9_]|\$)#\1$ROOT/dev/\2\3#g;s#(^|[^A-Za-z0-9_./-])/dev/(ttyFIQ0|tty1)([^A-Za-z0-9_]|\$)#\1$ROOT/dev/\2\3#g" "$2"  # twice: adjacent matches share the separator
	fi
}

# shellcheck disable=SC2154  # script_args, sleep_limit, gpioset_limit, invocation are set by tests/run.sh / the case
# invocation=sourced         : like gs.sh:11 / gs-init.sh:186 (gs.conf already loaded in the caller)  [default]
# invocation=standalone      : like button.sh:138 (separate process; gs.conf variables are NOT inherited)
# invocation=standalone_wait : standalone, run with args ("${script_args[@]}") and wait for background jobs
#                              the script leaves behind, so the shim log is deterministic
sb_run() {  # sb_run <script relative to repo>
	sb_rewrite "$REPO/$1" "$ROOT/script.sh"
	local inv="${invocation:-sourced}"
	(
		cd "$ROOT" || exit 99
		export PATH="$ROOT/shims:/usr/bin:/bin" SLEEP_LIMIT="${sleep_limit:-0}" GPIOSET_LIMIT="${gpioset_limit:-0}"
		case "$inv" in
		sourced)
			timeout 30 bash -c 'source "$1"; source "$2"' _ "$ROOT/etc/gs.conf" "$ROOT/script.sh" > "$ROOT/stdout" 2> "$ROOT/stderr" < /dev/null ;;
		standalone_wait)
			timeout 30 bash -c 's="$1"; shift; trap wait EXIT; source "$s" "$@"' _ "$ROOT/script.sh" ${script_args[@]+"${script_args[@]}"} > "$ROOT/stdout" 2> "$ROOT/stderr" < /dev/null ;;
		*)
			timeout 30 bash "$ROOT/script.sh" > "$ROOT/stdout" 2> "$ROOT/stderr" < /dev/null ;;
		esac
		echo "exit=$?" > "$ROOT/exit"
	) 2>/dev/null || true   # silences the shell's "Terminated" job message when a shim ends a daemon loop
	pkill -f "$ROOT/script.sh" 2>/dev/null || true   # leftovers (e.g. a blinker that missed its limit)
}

sb_norm() { sed "s#$ROOT#<ROOT>#g"; }

sb_dump() {  # normalised, deterministic report
	echo "== invocation: ${invocation:-sourced}"
	echo "== exit";   sb_norm < "$ROOT/exit"
	echo "== stdout"; sb_norm < "$ROOT/stdout"
	if [ "${sort_shim_log:-0}" = 1 ]; then echo "== shim calls (sorted: background jobs make the order non-deterministic)"; sb_norm < "$SHIM_LOG" | LC_ALL=C sort
	else echo "== shim calls"; sb_norm < "$SHIM_LOG"; fi
	echo "== gs.conf changes"; diff "$ROOT/gs.conf.orig" "$ROOT/etc/gs.conf" | sb_norm || true
	local f
	if [ "${dump_baseline:-1}" = 1 ]; then
	for f in etc/kernel/cmdline etc/kernel/cmdline.bak etc/default/gpsd media/root-ro/etc/fstab etc/samba/smb.conf \
		         etc/systemd/network/br0.network etc/network/interfaces.d/radxa0 config/custom-merged.conf; do
			if [ -f "$ROOT/$f" ]; then echo "== file /$f"; sb_norm < "$ROOT/$f"; fi
		done
		echo "== dtbo listing"; (cd "$ROOT/boot/dtbo" && ls -1 | LC_ALL=C sort)
	fi
	for f in "${dump_files[@]:-}"; do
		[ -n "$f" ] && [ -f "$ROOT/$f" ] && { echo "== file /$f"; sb_norm < "$ROOT/$f"; }
	done
	for f in "${dump_trees[@]:-}"; do
		[ -n "$f" ] && [ -d "$ROOT/$f" ] && sb_tree "$f"
	done
}

sb_tree() {  # deterministic listing of a directory tree: directories, symlinks and file contents
	echo "== tree /$1"
	( cd "$ROOT/$1" && find . | LC_ALL=C sort | while read -r e; do
		if [ -L "$e" ]; then echo "L $e -> $(readlink "$e")"
		elif [ -d "$e" ]; then echo "D $e"
		else echo "F $e: $(head -c 200 "$e" | tr '\n' '|')"; fi
	done ) | sb_norm
}

sb_clean() { rm -rf "$ROOT"; }
