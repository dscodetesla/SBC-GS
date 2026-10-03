#!/bin/bash
# Atomic, validated updates of /etc/gs.conf (milestone D16, second half). Sourced, never executed:
#   . /gs/lib/gsconf.sh
#   gsconf_set_quoted /etc/gs.conf wfb_channel 161 [KEY VALUE]...   # always writes KEY='VALUE'
#   gsconf_set_many   /etc/gs.conf KEY RHS [KEY RHS]...             # RHS is already a safe shell word (see gsconf_norm_value)
#   gsconf_check      /etc/gs.conf [KEY...]                         # non-empty, sources cleanly, required keys non-empty
#   gsconf_can_write  /etc/gs.conf                                  # a temp file can be created next to the REAL target
#
# Why: /etc/gs.conf is a symlink to a file on the writable /config partition (gs/install.sh) and is `source`d as root. It is rewritten
# by gs-applyconf.sh (custom.conf merge, button layout), gsmenu.sh (menu settings) and so on; a half-written, empty or hostile file
# breaks the next boot (empty rec_dir -> "need reboot" at every start, D16) or runs code as root (D14).
# What one update does (all inside ONE subshell, so the caller's options, traps and variables are never touched):
#   1. resolves the symlink (readlink -f) and works in the directory of the REAL file; takes flock on that directory (no lost updates);
#   2. refuses to start if the current file is empty, unreadable, does not source, or lacks a required key;
#   3. writes the new content to a temp file in the SAME directory (rename(2) is atomic only within one filesystem), copies mode and
#      owner, fsyncs it, sources it in a scratch subshell and checks the required keys AND that every changed key reads back as intended;
#   4. keeps the old file as a hard link (copy if the filesystem has no links), renames the temp file over the target, syncs, checks the
#      target again, and renames the old file back if that check fails (rollback);
#   5. on any failure leaves the original untouched (or restored), removes its temp files and prints a "gsconf: ..." line on stderr.
# A SIGKILL or power loss at any point leaves the target either old or new, never cut; leftovers (*.new.<pid>.*, *.bak.<pid>) of a dead
# writer are swept by the next update.
# Only keys that already exist in the file are replaced (nothing is appended), like the sed/awk it replaces; all lines with that key are replaced.
# Return: 0 done (also when nothing had to change), 1 refused/failed (file untouched or restored), 2 usage.
# Env: GSCONF_REQUIRED_KEYS (space separated, default "wifi_mode rec_dir gps_uart gps_uart_baudrate": spread over the whole file, so a cut anywhere is
# noticed), GSCONF_NO_SYNC=1 (skip sync, tests only), GSCONF_LOCK_WAIT (seconds, default 20).
# Not preserved: ACLs, xattrs, SELinux label (none are used on the image; INF), timestamps.

GSCONF_REQUIRED_KEYS_DEFAULT="wifi_mode rec_dir gps_uart gps_uart_baudrate"

# gsconf_valid_key KEY: a plain shell identifier
gsconf_valid_key() { [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; }

# gsconf_norm_value VALUE: print the right-hand side for a custom.conf style value: one layer of matching quotes is dropped, then the value
# is kept raw if it has shell-safe characters only, otherwise it goes inside single quotes. rc 1: the value contains a single quote
# or a newline (it cannot be stored inert).
gsconf_norm_value() {
	local val="${1%$'\r'}"
	if [[ "$val" =~ ^\'(.*)\'$ ]] || [[ "$val" =~ ^\"(.*)\"$ ]]; then
		val="${BASH_REMATCH[1]}"
	fi
	case "$val" in
	*\'* | *$'\n'*) return 1 ;;
	esac
	if [ -n "$val" ] && ! [[ "$val" =~ ^[A-Za-z0-9_.,:/@%+=-]+$ ]]; then
		val="'${val}'"
	fi
	printf '%s' "$val"
}

# gsconf_quote_value VALUE: print 'VALUE' (the form gsmenu.sh always wrote); rc 1 for a single quote or newline in VALUE
gsconf_quote_value() {
	case "$1" in
	*\'* | *$'\n'*) return 1 ;;
	esac
	printf "'%s'" "$1"
}

# _gsconf_check FILE [use_expect]: FILE must be non-empty, source cleanly in a scratch subshell and define every required key
# (and, with use_expect=1, every changed key must read back as the value in _gsconf_ek/_gsconf_ev of the calling _gsconf_txn).
_gsconf_check() {
	local f="$1" use="${2:-0}"
	local -a keys
	read -ra keys <<<"${GSCONF_REQUIRED_KEYS:-$GSCONF_REQUIRED_KEYS_DEFAULT}"
	[ -f "$f" ] && [ -r "$f" ] || { echo "gsconf: $f is missing or unreadable" >&2; return 1; }
	[ -s "$f" ] || { echo "gsconf: $f is empty" >&2; return 1; }
	(
		set +e +u
		# stdin is closed: the caller may be a `while read` loop fed from a pipe
		# shellcheck disable=SC1090  # the checked gs.conf is data chosen at run time
		. "$f" >/dev/null 2>&1 </dev/null || { echo "gsconf: $f does not source cleanly" >&2; exit 1; }
		for k in "${keys[@]}"; do
			[ -n "${!k:-}" ] || { echo "gsconf: '$k' is empty or missing in $f" >&2; exit 1; }
		done
		if [ "$use" = 1 ]; then
			for i in "${!_gsconf_ek[@]}"; do
				k="${_gsconf_ek[$i]}"
				[ "${!k-}" = "${_gsconf_ev[$i]}" ] || { echo "gsconf: '$k' does not read back as written in $f" >&2; exit 1; }
			done
		fi
	)
}

# gsconf_check FILE [KEY...]: public form; KEYs replace the default required list for this call
gsconf_check() {
	local f="$1"
	shift
	if [ $# -gt 0 ]; then
		GSCONF_REQUIRED_KEYS="$*" _gsconf_check "$f"
	else
		_gsconf_check "$f"
	fi
}

# _gsconf_sync PATH: flush data and the directory entry (syncfs of the filesystem of PATH). Absolute /bin/sync on purpose: not looked up in PATH.
_gsconf_sync() {
	local s
	[ "${GSCONF_NO_SYNC:-0}" = 1 ] && return 0
	for s in /bin/sync /usr/bin/sync; do
		if [ -x "$s" ]; then
			"$s" -f -- "$1" >/dev/null 2>&1 || "$s" >/dev/null 2>&1 || true
			return 0
		fi
	done
	return 0
}

# gsconf_can_write FILE: can a temp file be created next to the real target? (a plain `-w` test lies for root on a read-only mount)
gsconf_can_write() {
	local conf dir t err
	conf="$(readlink -f -- "$1" 2>/dev/null)"
	[ -n "$conf" ] && [ -f "$conf" ] || { echo "gsconf: $1 does not resolve to a regular file" >&2; return 1; }
	dir="${conf%/*}"
	[ -n "$dir" ] || dir=/
	if t="$(mktemp "${conf}.probe.XXXXXX" 2>&1)"; then
		rm -f -- "$t"
		return 0
	fi
	err="$t"
	echo "gsconf: cannot create a file next to $conf (read-only filesystem or no permission): $err" >&2
	return 1
}

# globals of the transaction (it runs in a subshell, so nothing leaks); the EXIT trap needs them after the function frame is gone
_gsconf_tmp="" _gsconf_bak=""
_gsconf_cleanup() {
	[ -z "$_gsconf_tmp" ] || rm -f -- "$_gsconf_tmp"
	[ -z "$_gsconf_bak" ] || rm -f -- "$_gsconf_bak"
	_gsconf_tmp="" _gsconf_bak=""
}

# _gsconf_txn FILE KEY RHS [KEY RHS]...  (run it only through gsconf_set_many: it must live in its own subshell)
_gsconf_txn() {
	set +e
	local file="$1" conf dir lockfd="" k v val i n f p err st0 st1 mode own
	local -a keys=() rhs=()
	local -a _gsconf_ek=() _gsconf_ev=()
	shift
	if [ $# -lt 2 ] || [ $(($# % 2)) -ne 0 ]; then
		echo "gsconf: usage: gsconf_set_many FILE KEY RHS [KEY RHS]..." >&2
		return 2
	fi
	while [ $# -gt 0 ]; do
		k="$1" v="$2"
		shift 2
		gsconf_valid_key "$k" || { echo "gsconf: rejected key '$k'" >&2; return 1; }
		case "$v" in
		\'*\')
			val="${v#\'}"
			val="${val%\'}"
			case "$val" in
			*\'* | *$'\n'*) echo "gsconf: rejected value of '$k' (single quote or newline inside quotes)" >&2; return 1 ;;
			esac
			;;
		*)
			[[ "$v" =~ ^[A-Za-z0-9_.,:/@%+=-]*$ ]] || { echo "gsconf: rejected value of '$k' (not a safe shell word)" >&2; return 1; }
			val="$v"
			;;
		esac
		keys+=("$k")
		rhs+=("$v")
		_gsconf_ek+=("$k")
		_gsconf_ev+=("$val")
	done

	conf="$(readlink -f -- "$file" 2>/dev/null)"
	[ -n "$conf" ] && [ -f "$conf" ] || { echo "gsconf: $file does not resolve to a regular file (got '${conf:-nothing}')" >&2; return 1; }
	dir="${conf%/*}"
	[ -n "$dir" ] || dir=/

	trap '_gsconf_cleanup' EXIT
	trap 'exit 143' TERM
	trap 'exit 130' INT HUP

	# one writer at a time (gsmenu, the custom.conf merge and the web UI may overlap); best effort when flock or the open is unavailable
	if command -v flock >/dev/null 2>&1 && { exec {lockfd}<"$dir"; } 2>/dev/null; then
		flock -w "${GSCONF_LOCK_WAIT:-20}" -E 75 "$lockfd"
		case $? in
		0) ;;
		75) echo "gsconf: timed out waiting for the lock on $dir" >&2; return 1 ;;
		*) echo "gsconf: warning: cannot lock $dir, continuing without a lock" >&2 ;;
		esac
	fi

	# leftovers of a writer that died (SIGKILL, power loss): the owner pid is in the name; a live pid is left alone
	for f in "$conf".new.* "$conf".bak.*; do
		[ -e "$f" ] || continue
		p="${f#"$conf".}"
		p="${p#*.}"
		p="${p%%.*}"
		[[ "$p" =~ ^[0-9]+$ ]] || continue
		kill -0 "$p" 2>/dev/null || rm -f -- "$f"
	done

	_gsconf_check "$conf" 0 || { echo "gsconf: $conf failed validation before the update: nothing written" >&2; return 1; }

	if ! err="$(mktemp "${conf}.new.${BASHPID}.XXXXXX" 2>&1)"; then
		echo "gsconf: cannot create a temp file next to $conf (read-only filesystem or no permission): $err" >&2
		return 1
	fi
	_gsconf_tmp="$err"

	n=${#keys[@]}
	export GSCONF_N="$n"
	for ((i = 0; i < n; i++)); do
		export "GSCONF_K$i=${keys[$i]}" "GSCONF_V$i=${rhs[$i]}"
	done
	awk 'BEGIN { n = ENVIRON["GSCONF_N"] + 0; for (i = 0; i < n; i++) m[ENVIRON["GSCONF_K" i] "="] = ENVIRON["GSCONF_V" i] }
		{ for (p in m) if (index($0, p) == 1) { print p m[p]; next } print }' "$conf" >"$_gsconf_tmp" ||
		{ echo "gsconf: awk failed while building the new content of $conf: nothing written" >&2; return 1; }

	if cmp -s -- "$conf" "$_gsconf_tmp"; then
		return 0   # nothing to change (no such key, or the same value)
	fi

	# mode and owner of the real file; tried, then verified (a filesystem that fixes them by mount options passes the verification)
	st0="$(stat -c '%a %u:%g' -- "$conf" 2>/dev/null)" || { echo "gsconf: cannot stat $conf: nothing written" >&2; return 1; }
	mode="${st0%% *}"
	own="${st0#* }"
	chmod "$mode" -- "$_gsconf_tmp" 2>/dev/null
	chown "$own" -- "$_gsconf_tmp" 2>/dev/null
	st1="$(stat -c '%a %u:%g' -- "$_gsconf_tmp" 2>/dev/null)"
	[ "$st0" = "$st1" ] || { echo "gsconf: cannot give the new file the mode/owner of $conf ($st0 wanted, $st1 got): nothing written" >&2; return 1; }

	# fsync the new data before the rename (otherwise a power cut after the rename can leave an empty file)
	if [ "${GSCONF_NO_SYNC:-0}" != 1 ]; then
		dd if=/dev/null of="$_gsconf_tmp" conv=notrunc,fsync >/dev/null 2>&1 || true
	fi
	_gsconf_check "$_gsconf_tmp" 1 || { echo "gsconf: the new content for $conf failed verification: nothing written" >&2; return 1; }

	_gsconf_bak="${conf}.bak.${BASHPID}"
	if ! ln -f -- "$conf" "$_gsconf_bak" 2>/dev/null; then
		cp -p -- "$conf" "$_gsconf_bak" 2>/dev/null && cmp -s -- "$conf" "$_gsconf_bak" ||
			{ echo "gsconf: cannot keep a backup of $conf: nothing written" >&2; return 1; }
	fi
	if ! err="$(mv -f -- "$_gsconf_tmp" "$conf" 2>&1)"; then
		echo "gsconf: cannot replace $conf: $err" >&2
		return 1
	fi
	_gsconf_tmp=""
	_gsconf_sync "$conf"
	if ! _gsconf_check "$conf" 1; then
		echo "gsconf: $conf failed verification after the update: restoring the previous content" >&2
		if mv -f -- "$_gsconf_bak" "$conf"; then
			_gsconf_bak=""
			_gsconf_sync "$conf"
			echo "gsconf: previous content of $conf restored" >&2
		else
			echo "gsconf: ERROR: could not restore $conf, the previous content is in $_gsconf_bak" >&2
			_gsconf_bak=""   # keep the backup file for the operator
		fi
		return 1
	fi
	return 0
}

# gsconf_set_many FILE KEY RHS [KEY RHS]...: replace existing KEY= lines atomically (see the header). RHS must be a safe shell word:
# characters A-Za-z0-9_.,:/@%+=- only, or '...' with no single quote inside (use gsconf_norm_value / gsconf_quote_value to build it).
gsconf_set_many() { ( _gsconf_txn "$@" ); }

# gsconf_set_quoted FILE KEY VALUE [KEY VALUE]...: like gsconf_set_many, each VALUE is stored as 'VALUE' (a value with a single quote or a
# newline is rejected, nothing is written)
gsconf_set_quoted() {
	local file="$1" k v q
	local -a args=()
	shift
	if [ $# -lt 2 ] || [ $(($# % 2)) -ne 0 ]; then
		echo "gsconf: usage: gsconf_set_quoted FILE KEY VALUE [KEY VALUE]..." >&2
		return 2
	fi
	while [ $# -gt 0 ]; do
		k="$1" v="$2"
		shift 2
		q="$(gsconf_quote_value "$v")" || { echo "gsconf: rejected value of '$k' (single quote or newline)" >&2; return 1; }
		args+=("$k" "$q")
	done
	gsconf_set_many "$file" "${args[@]}"
}
