#!/usr/bin/env bash
# Sandbox helpers for running gs/*.sh without target hardware.
# Absolute paths in a COPY of the script are rewritten into a temporary root, and
# system-changing commands are replaced by logging shims. Sourced, not executed.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# command shims: log argv to $SHIM_LOG and succeed (sleep is a silent no-op)
SHIMMED="chroot mount reboot systemctl nmcli sleep modprobe uname hostname mkdir"

sb_new() {
	ROOT="$(mktemp -d)"
	export SHIM_LOG="$ROOT/shim.log"; : > "$SHIM_LOG"
	mkdir -p "$ROOT"/{etc/kernel,etc/default,etc/samba,etc/systemd/network,etc/network/interfaces.d,media/root-ro/etc,boot/dtbo,config,sys/class/net,shims}
	local n
	for n in $SHIMMED; do
		case "$n" in
		sleep)  # no-op; if SLEEP_LIMIT>0 the Nth call terminates the calling script (ends daemon loops)
			printf '%s\n' '#!/bin/bash' 'c="$SHIM_ROOT/sleep.count"; n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$c"' \
				'if [ "${SLEEP_LIMIT:-0}" -gt 0 ] && [ "$n" -ge "$SLEEP_LIMIT" ]; then kill -TERM "$PPID"; fi' 'exit 0' > "$ROOT/shims/$n" ;;
		uname)    printf '#!/bin/bash\necho "6.1.0-test"\n' > "$ROOT/shims/$n" ;;
		hostname) printf '#!/bin/bash\necho "gs-test"\n' > "$ROOT/shims/$n" ;;
		mkdir)    # like configfs: creating a mass_storage function also creates its lun.0
			printf '%s\n' '#!/bin/bash' '/bin/mkdir "$@" || exit $?' \
				'for a in "$@"; do case "$(basename "$a")" in mass_storage.*) /bin/mkdir -p "$a/lun.0";; esac; done' > "$ROOT/shims/$n" ;;
		*) printf '#!/bin/bash\necho "%s $*" >> "$SHIM_LOG"\nexit 0\n' "$n" > "$ROOT/shims/$n" ;;
		esac
		chmod +x "$ROOT/shims/$n"
	done
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

# rewrite /etc /boot /media /config /sys /proc /run when NOT preceded by a path character
sb_rewrite() {
	sed -E "s#(^|[^A-Za-z0-9_./-])/(etc|boot|media|config|sys|proc|run)/#\1$ROOT/\2/#g" "$1" > "$2"
}

# invocation=sourced   : like gs.sh:11 / gs-init.sh:186 (gs.conf already loaded in the caller)  [default]
# invocation=standalone: like button.sh:138 (separate process; gs.conf variables are NOT inherited)
sb_run() {  # sb_run <script relative to repo>
	sb_rewrite "$REPO/$1" "$ROOT/script.sh"
	local inv="${invocation:-sourced}"
	(
		cd "$ROOT" || exit 99
		export PATH="$ROOT/shims:$PATH" SLEEP_LIMIT="${sleep_limit:-0}"
		if [ "$inv" = sourced ]; then
			bash -c 'source "$1"; source "$2"' _ "$ROOT/etc/gs.conf" "$ROOT/script.sh" > "$ROOT/stdout" 2> "$ROOT/stderr"
		else
			bash "$ROOT/script.sh" > "$ROOT/stdout" 2> "$ROOT/stderr"
		fi
		echo "exit=$?" > "$ROOT/exit"
	) 2>/dev/null || true   # silences the shell's "Terminated" job message when a shim ends a daemon loop
}

sb_norm() { sed "s#$ROOT#<ROOT>#g"; }

sb_dump() {  # normalised, deterministic report
	echo "== invocation: ${invocation:-sourced}"
	echo "== exit";   sb_norm < "$ROOT/exit"
	echo "== stdout"; sb_norm < "$ROOT/stdout"
	echo "== shim calls"; sb_norm < "$SHIM_LOG"
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
