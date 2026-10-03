#!/usr/bin/env bash
# Runs ONE gs/*.sh script in the project sandbox (tests/lib/sandbox.sh: paths rewritten into a temp root, system commands replaced
# by logging shims) with extra fault injection, and leaves the outcome in <outdir>. Never touches the real /etc, /sys, /run.
#   sandbox_driver.sh <repo> <script (relative to repo)> <outdir> [standalone|sourced]
# Env: OVERLAY=<dir>   copied over the sandbox root first (e.g. config/custom.conf, sys/..., etc/gs.conf)
#      GSCONF_APPEND   text appended to the sandbox etc/gs.conf (the baseline is the real gs/gs.conf)
#      HIDE="jq iw"    command names removed from PATH (a "missing utility" fault)
#      HANG="nmcli"    command names replaced by a shim that sleeps for 60 s (a "hanging command" fault)
#      TMO=10          seconds after which the script is killed (exit 124 = it hung)
#      SLEEP_LIMIT     Nth call of the sleep shim terminates the script (ends daemon loops)
#      ARGS            script arguments (space separated)
#      REWRITE_EXTRA   extra absolute prefixes to move into the sandbox (e.g. "/dev/shm")
# Output: <outdir>/{exit,stdout,stderr,shim.log,root/} ; exit holds the script's exit code (124 = timeout).
set -u
repo="$1"; script="$2"; out="$3"; inv="${4:-standalone}"
# shellcheck source=/dev/null
. "$repo/tests/lib/sandbox.sh"
# shellcheck disable=SC2034  # read by sb_rewrite
rewrite_extra=()
sb_new
[ -z "${OVERLAY:-}" ] || cp -a "$OVERLAY"/. "$ROOT"/
[ -z "${GSCONF_APPEND:-}" ] || printf '%s\n' "$GSCONF_APPEND" >> "$ROOT/etc/gs.conf"
dirs="$ROOT/shims:/usr/bin:/bin"
if [ -n "${HIDE:-}" ]; then
	mkdir "$ROOT/hidebin"
	for f in /usr/bin/*; do
		b="${f##*/}"
		case " $HIDE " in *" $b "*) continue ;; esac
		ln -s "$f" "$ROOT/hidebin/$b"
	done
	for h in $HIDE; do rm -f "$ROOT/shims/$h"; done
	dirs="$ROOT/shims:$ROOT/hidebin"
fi
for h in ${HANG:-}; do printf '#!/bin/sh\nexec /usr/bin/sleep 60\n' > "$ROOT/shims/$h"; chmod +x "$ROOT/shims/$h"; done
if [ -n "${REWRITE_EXTRA:-}" ]; then
	# shellcheck disable=SC2034  # read by sb_rewrite
	read -ra rewrite_extra <<< "$REWRITE_EXTRA"
fi
sb_rewrite "$repo/$script" "$ROOT/script.sh"
(
	cd "$ROOT" || exit 99
	export PATH="$dirs" SLEEP_LIMIT="${SLEEP_LIMIT:-0}" GPIOSET_LIMIT=0
	# shellcheck disable=SC2086
	if [ "$inv" = sourced ]; then
		timeout -k 1 "${TMO:-10}" bash -c 'source "$1"; source "$2"' _ "$ROOT/etc/gs.conf" "$ROOT/script.sh" > "$ROOT/stdout" 2> "$ROOT/stderr" < /dev/null
	else
		timeout -k 1 "${TMO:-10}" bash "$ROOT/script.sh" ${ARGS:-} > "$ROOT/stdout" 2> "$ROOT/stderr" < /dev/null
	fi
	echo "$?" > "$ROOT/exit"
) 2>/dev/null
pkill -f "$ROOT/script.sh" 2>/dev/null
mkdir -p "$out"
cp "$ROOT/exit" "$ROOT/stdout" "$ROOT/stderr" "$ROOT/shim.log" "$out"/ 2>/dev/null
cp -a "$ROOT" "$out/root" 2>/dev/null
sb_clean
exit 0
