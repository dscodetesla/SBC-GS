#!/usr/bin/env bash
# Static/behavioural check of gs/lib/gsconf.sh (atomic, validated writes of /etc/gs.conf; D16 second half). No hardware, no root needed;
# the output is identical for root and nobody (temp paths and owners are normalised). Registered in tests/run.sh as static/gsconf.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB="$REPO/gs/lib/gsconf.sh"
T="$(mktemp -d)"
trap 'kill -9 $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
bad=0
want() { # want <label> <expected> <actual>
	if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi
}
yes_() { if [ "$2" = 0 ]; then echo "ok $1"; else echo "FAIL $1"; bad=1; fi; }
st() { stat -c '%a %u:%g' -- "$1"; }
leftovers() { find "$1" -maxdepth 1 \( -name '*.new.*' -o -name '*.bak.*' -o -name '*.probe.*' \) | wc -l; }
# run a gsconf function in a fresh bash (as the product scripts do: sourced, then called)
g() { bash -c '. "$1"; shift; "$@"' _ "$LIB" "$@"; }
fresh() { rm -rf "$T/w"; mkdir -p "$T/w/config" "$T/w/etc"; cp "$REPO/gs/gs.conf" "$T/w/config/gs.conf"; ln -s "$T/w/config/gs.conf" "$T/w/etc/gs.conf"; chmod 640 "$T/w/config/gs.conf"; C="$T/w/etc/gs.conf"; R="$T/w/config/gs.conf"; }

echo "== files"
[ -f "$LIB" ] && echo "ok gs/lib/gsconf.sh exists" || { echo "FAIL gs/lib/gsconf.sh missing"; exit 1; }
bash -n "$LIB"; yes_ "bash -n gsconf.sh" $?
grep -q '^cp -r lib boards' "$REPO/gs/install.sh"; yes_ "install.sh copies the whole lib/ directory (gsconf.sh included)" $?
for f in gs-applyconf.sh gsmenu.sh channel-scan.sh; do
	n="$(grep -cE 'sed -i.*gs\.conf|>[[:space:]]*/etc/gs\.conf|>>[[:space:]]*/etc/gs\.conf' "$REPO/gs/$f")"; want "$f: no direct sed -i/redirect into gs.conf" 0 "$n"
	grep -q 'lib/gsconf.sh' "$REPO/gs/$f"; yes_ "$f sources lib/gsconf.sh" $?
done

echo "== update through the symlink"
fresh; orig="$T/orig"; cp "$R" "$orig"; ino0="$(stat -c %i "$R")"
g gsconf_set_quoted "$C" wfb_channel 36 rec_fps 90 >/dev/null 2>&1; want "rc" 0 $?
[ "$(readlink "$C")" = "$R" ] && [ -L "$C" ]; yes_ "symlink intact and still points at the real file" $?
want "target is still a regular file" "regular file" "$(stat -c %F "$R")"
want "changed keys" "rec_fps wfb_channel" "$(diff "$orig" "$R" | sed -n 's/^> \([a-z_]*\)=.*/\1/p' | sort | tr '\n' ' ' | sed 's/ $//')"
want "diff line count" 4 "$(diff "$orig" "$R" | grep -c '^[<>]')"
want "wfb_channel" "36" "$(bash -c ". '$R'; echo \$wfb_channel")"
want "mode preserved" "640" "$(st "$R" | cut -d' ' -f1)"
want "no leftovers" 0 "$(leftovers "$T/w/config")"
[ "$(stat -c %i "$R")" != "$ino0" ]; yes_ "replaced by rename (new inode)" $?

echo "== owner and mode are carried over"
fresh
if [ "$(id -u)" = 0 ]; then chown 65534:65534 "$R"; fi
chmod 600 "$R"; before="$(st "$R")"
g gsconf_set_quoted "$C" rec_fps 75 >/dev/null 2>&1; want "rc" 0 $?
want "mode and owner unchanged" "$before" "$(st "$R")"

echo "== nothing to change: no rewrite"
fresh; ino0="$(stat -c %i "$R")"
g gsconf_set_quoted "$C" rec_fps 60 >/dev/null 2>&1; want "same value rc" 0 $?
[ "$ino0" = "$(stat -c %i "$R")" ]; yes_ "same value keeps the inode" $?
g gsconf_set_quoted "$C" no_such_key 1 >/dev/null 2>&1; want "unknown key rc" 0 $?
[ "$ino0" = "$(stat -c %i "$R")" ]; yes_ "unknown key keeps the inode" $?
cmp -s "$R" "$REPO/gs/gs.conf"; yes_ "content unchanged" $?

echo "== hostile and malformed input: rejected, file untouched"
fresh
for spec in "rec_fps|it's" "rec_fps|a\$(touch $T/PWNED)'x" "9bad|1" "wifi_mode;touch $T/PWNED|1" "rec_fps|$(printf 'a\nb')"; do
	k="${spec%%|*}"; v="${spec#*|}"
	g gsconf_set_quoted "$C" "$k" "$v" >/dev/null 2>"$T/err"; rc=$?
	want "reject [$(printf '%s' "$k=$v" | tr '\n' '~' | sed "s#$T#T#g")] rc" 1 "$rc"
	cmp -s "$R" "$REPO/gs/gs.conf"; yes_ "  file untouched" $?
done
want "no PWNED" no "$([ -e "$T/PWNED" ] && echo yes || echo no)"
g gsconf_set_many "$C" rec_fps '$(touch '"$T"'/PWNED)' >/dev/null 2>&1; want "set_many refuses an unquoted unsafe rhs rc" 1 $?
g gsconf_set_many "$C" rec_fps "'a'b'" >/dev/null 2>&1; want "set_many refuses a quote inside quotes rc" 1 $?
want "no PWNED after set_many" no "$([ -e "$T/PWNED" ] && echo yes || echo no)"

echo "== hostile characters inside single quotes are stored inert and read back"
fresh
val='My Net & co $(touch X) `touch Y` \x /a/b ;|<>"'
g gsconf_set_quoted "$C" wifi_ssid "$val" >/dev/null 2>&1; want "rc" 0 $?
want "read back" "$val" "$(bash -c ". '$R'; printf '%s' \"\$wifi_ssid\"")"
want "diff line count" 2 "$(diff "$REPO/gs/gs.conf" "$R" | grep -c '^[<>]')"

echo "== norm_value (custom.conf semantics)"
for v in "zz" "a/b" "My Net" "'quoted pass'" "\"dq\"" "" "x;y" "it's" "a b"$'\r' '$(x)'; do
	o="$(g gsconf_norm_value "$v" 2>/dev/null)"; rc=$?
	echo "norm [$(printf '%s' "$v" | tr '\r' '~')] -> rc=$rc out=[$o]"
done

echo "== empty, truncated or unsourceable gs.conf is refused"
fresh; : > "$R"; g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>"$T/err"; want "empty rc" 1 $?
want "empty stays empty" 0 "$(stat -c %s "$R")"; grep -q 'is empty' "$T/err"; yes_ "clear message (empty)" $?
fresh; printf "wifi_mode='hotspot'\nrec_dir='/Videos'\nrec_fps='60'\n" > "$R"; cp "$R" "$T/trunc"
g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>"$T/err"; want "truncated rc" 1 $?
cmp -s "$R" "$T/trunc"; yes_ "truncated file untouched" $?; grep -q "'gps_uart' is empty or missing" "$T/err"; yes_ "message names the missing key" $?
fresh; printf '%s\n' "$(cat "$REPO/gs/gs.conf")" 'if then fi' > "$R"; cp "$R" "$T/syn"
g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>"$T/err"; want "syntax error rc" 1 $?
cmp -s "$R" "$T/syn"; yes_ "syntax-error file untouched" $?
fresh; rm -f "$R"; g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>"$T/err"; want "dangling symlink rc" 1 $?
grep -q 'does not resolve to a regular file' "$T/err"; yes_ "clear message (dangling)" $?
g gsconf_check "$T/none" >/dev/null 2>&1; want "gsconf_check on a missing file rc" 1 $?
fresh; g gsconf_check "$C" >/dev/null 2>&1; want "gsconf_check on the default gs.conf rc" 0 $?
g gsconf_check "$C" nope_key >/dev/null 2>&1; want "gsconf_check with an extra required key rc" 1 $?

echo "== the directory cannot take a temp file: clear error, no damage"
fresh; mkdir "$T/shim1"; printf '#!/bin/sh\necho "mktemp: failed to create file via template: Read-only file system" >&2\nexit 1\n' > "$T/shim1/mktemp"; chmod +x "$T/shim1/mktemp"
PATH="$T/shim1:$PATH" g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>"$T/err"; want "rc" 1 $?
grep -q 'cannot create a temp file next to' "$T/err" && grep -q 'Read-only file system' "$T/err"; yes_ "message names the cause" $?
cmp -s "$R" "$REPO/gs/gs.conf"; yes_ "file untouched" $?; want "no leftovers" 0 "$(leftovers "$T/w/config")"
PATH="$T/shim1:$PATH" g gsconf_can_write "$C" >/dev/null 2>"$T/err"; want "gsconf_can_write rc" 1 $?
g gsconf_can_write "$C" >/dev/null 2>&1; want "gsconf_can_write on a writable dir rc" 0 $?
want "gsconf_can_write leaves nothing" 0 "$(leftovers "$T/w/config")"

echo "== a bad result after the rename is rolled back"
fresh; mkdir "$T/shim2"
cat > "$T/shim2/mv" <<'SH'
#!/bin/bash
# first call: do the move, then cut the target (a filesystem that lost the data); later calls (the rollback) are real
if [ ! -e "$SHIMSTATE" ]; then : > "$SHIMSTATE"; /bin/mv "$@" && : > "${@: -1}"; exit $?; fi
exec /bin/mv "$@"
SH
chmod +x "$T/shim2/mv"
SHIMSTATE="$T/state2" PATH="$T/shim2:$PATH" g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>"$T/err"; want "rc" 1 $?
cmp -s "$R" "$REPO/gs/gs.conf"; yes_ "previous content restored byte for byte" $?
grep -q 'restoring the previous content' "$T/err"; yes_ "message says it rolled back" $?
want "no leftovers" 0 "$(leftovers "$T/w/config")"; want "mode kept" "640" "$(st "$R" | cut -d' ' -f1)"

echo "== killed in the middle of the write: the target is never cut"
fresh; mkdir "$T/shim3"
cat > "$T/shim3/awk" <<'SH'
#!/bin/bash
# real awk, but only the first 5 lines of its output arrive, then the process hangs (power cut / kill in the middle of the write)
/usr/bin/awk "$@" | head -n 5; sleep 30
SH
chmod +x "$T/shim3/awk"
for sig in KILL TERM; do
	fresh
	PATH="$T/shim3:$PATH" setsid bash -c '. "$1"; gsconf_set_quoted "$2" rec_fps 90' _ "$LIB" "$C" >/dev/null 2>&1 &
	pid=$!
	n=50; while [ "$n" -gt 0 ] && [ "$(find "$T/w/config" -name 'gs.conf.new.*' -size +0 | wc -l)" = 0 ]; do sleep 0.1; n=$((n - 1)); done
	want "[$sig] partial temp file exists while writing" 1 "$(find "$T/w/config" -name 'gs.conf.new.*' -size +0 | wc -l)"
	kill -"$sig" -- -"$pid" 2>/dev/null; wait "$pid" 2>/dev/null
	cmp -s "$R" "$REPO/gs/gs.conf"; yes_ "[$sig] target is still the complete old file" $?
	g gsconf_check "$C" >/dev/null 2>&1; yes_ "[$sig] target passes the check" $?
	case "$sig" in
	KILL) want "[KILL] a stale temp file of the dead writer remains" 1 "$(leftovers "$T/w/config")" ;;
	TERM) want "[TERM] the handler removed the temp file" 0 "$(leftovers "$T/w/config")" ;;
	esac
	g gsconf_set_quoted "$C" rec_fps 90 >/dev/null 2>&1; want "[$sig] next update rc" 0 $?
	want "[$sig] next update sweeps the leftovers" 0 "$(leftovers "$T/w/config")"
	want "[$sig] next update applied" "90" "$(bash -c ". '$R'; echo \$rec_fps")"
done
pkill -f "$T/shim3" 2>/dev/null; true

echo "== concurrent writers do not lose updates"
fresh
g gsconf_set_quoted "$C" rec_fps 91 >/dev/null 2>&1 &
g gsconf_set_quoted "$C" wfb_channel 40 >/dev/null 2>&1 &
g gsconf_set_quoted "$C" wfb_bandwidth 40 >/dev/null 2>&1 &
g gsconf_set_quoted "$C" osd_type msposd_gs >/dev/null 2>&1 &
g gsconf_set_quoted "$C" alink_enable yes >/dev/null 2>&1 &
wait
want "all five updates present" "91 40 40 msposd_gs yes" "$(bash -c ". '$R'; echo \$rec_fps \$wfb_channel \$wfb_bandwidth \$osd_type \$alink_enable")"
want "no leftovers" 0 "$(leftovers "$T/w/config")"

echo "== caller state: set -e, traps, PATH sync"
fresh; mkdir "$T/shim4"; printf '#!/bin/sh\necho called >> "%s/sync.log"\n' "$T" > "$T/shim4/sync"; chmod +x "$T/shim4/sync"
out="$(PATH="$T/shim4:$PATH" bash -c "set -e; trap 'echo CALLER_EXIT' EXIT; . '$LIB'; gsconf_set_quoted '$C' rec_fps 80; gsconf_set_quoted '$C' rec_fps 81; echo survived" 2>/dev/null | tr '\n' ' ')"
want "set -e caller survives, its EXIT trap is intact" "survived CALLER_EXIT " "$out"
want "the PATH sync shim is not used" no "$([ -e "$T/sync.log" ] && echo yes || echo no)"
out="$(bash -c "set -e; . '$LIB'; gsconf_set_quoted '$T/none' rec_fps 1 2>/dev/null; echo not-reached" 2>/dev/null | tr '\n' ' ')"
want "set -e caller stops on a failed update (like the sed it replaced)" "" "$out"

echo "== usage"
g gsconf_set_many "$C" rec_fps >/dev/null 2>&1; want "odd argument count rc" 2 $?
g gsconf_set_quoted "$C" >/dev/null 2>&1; want "missing arguments rc" 2 $?
[ "$bad" = 0 ] && echo "gsconf: ALL OK" || echo "gsconf: FAILED"
exit "$bad"
