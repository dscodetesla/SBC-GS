#!/usr/bin/env bash
# Offline tests for build/lib/fetch.sh (M5 step 1): sha256 verification, git SHA pinning, safety.
# No network: the download primitive is replaced through GS_FETCH_CMD by a `cp` shim (file:// is
# rejected by curl --proto '=https'); git_pin uses a local temp repository.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB="$REPO/build/lib/fetch.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
ok()   { echo "ok   $*"; }
fail() { echo "FAIL $*"; bad=1; }
norm() { sed "s#$T#<T>#g"; }

printf 'payload-one\n' > "$T/src.bin"
GOOD="$(sha256sum "$T/src.bin" | awk '{print $1}')"
BADH="$(printf '0%.0s' $(seq 64))"
cat > "$T/cpshim" <<'SH'
#!/bin/sh
# usage: cpshim <url> <dest>; url is a local path (stands in for the https download)
cp -- "$1" "$2"
SH
cat > "$T/failshim" <<'SH'
#!/bin/sh
exit 22
SH
chmod +x "$T/cpshim" "$T/failshim"
export GS_FETCH_CMD="$T/cpshim"
# shellcheck source=../../build/lib/fetch.sh
. "$LIB"

echo "== fetch_file"
rc=0; out="$(fetch_file "$T/src.bin" "$T/d1" "$GOOD" 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 0 ] && cmp -s "$T/src.bin" "$T/d1"; } && ok "correct hash accepted, file in place" || fail "correct hash"
UP="$(printf '%s' "$GOOD" | tr 'a-f' 'A-F')"
rc=0; fetch_file "$T/src.bin" "$T/d1u" "$UP" >/dev/null 2>&1 || rc=$?
[ "$rc" = 0 ] && ok "uppercase hash accepted" || fail "uppercase hash rc=$rc"

rc=0; out="$(fetch_file "$T/src.bin" "$T/d2" "$BADH" 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 1 ] && [ ! -e "$T/d2" ]; } && ok "wrong hash rejected, file removed" || fail "wrong hash rc=$rc"
printf 'stale\n' > "$T/d2b"
fetch_file "$T/src.bin" "$T/d2b" "$BADH" >/dev/null 2>&1 || true
[ ! -e "$T/d2b" ] && ok "stale dest removed on mismatch" || fail "stale dest kept"
parts=("$T"/*.part.*); [ -e "${parts[0]}" ] && fail "temp .part file left behind" || ok "no .part leftovers"

rc=0; out="$(fetch_file "$T/src.bin" "$T/d3" "" 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 2 ] && [ ! -e "$T/d3" ]; } && ok "empty hash refused" || fail "empty hash rc=$rc"
rc=0; out="$(fetch_file "$T/src.bin" "$T/d3b" "abc123" 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 2 ] && [ ! -e "$T/d3b" ]; } && ok "malformed hash refused" || fail "malformed hash rc=$rc"

rc=0; out="$(GS_ALLOW_UNPINNED=1 fetch_file "$T/src.bin" "$T/d4" "" 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 0 ] && [ -f "$T/d4" ] && printf '%s' "$out" | grep -q "WARNING" && printf '%s' "$out" | grep -q "$GOOD"; } \
	&& ok "GS_ALLOW_UNPINNED=1 warns and prints the computed sha256" || fail "unpinned mode rc=$rc"
rc=0; out="$(GS_ALLOW_UNPINNED=0 fetch_file "$T/src.bin" "$T/d4b" "" 2>&1)" || rc=$?
[ "$rc" = 2 ] && ok "GS_ALLOW_UNPINNED=0 still refuses" || fail "GS_ALLOW_UNPINNED=0 rc=$rc"

rc=0; out="$(GS_FETCH_CMD="$T/failshim" fetch_file "$T/src.bin" "$T/d5" "$GOOD" 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 1 ] && [ ! -e "$T/d5" ]; } && ok "download failure returns non-zero, no file" || fail "download failure rc=$rc"

echo "== git_pin"
G="$T/up"; mkdir "$G"
# isolate from the host git config (e.g. commit.gpgsign would make commit ids non-deterministic)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
git -C "$G" init -q -b main
printf 'one\n' > "$G/f"; git -C "$G" add f
GIT_AUTHOR_DATE='2020-01-01T00:00:00Z' GIT_COMMITTER_DATE='2020-01-01T00:00:00Z' git -C "$G" commit -q -m c1
C1="$(git -C "$G" rev-parse HEAD)"
printf 'two\n' > "$G/f"; git -C "$G" add f
GIT_AUTHOR_DATE='2020-01-02T00:00:00Z' GIT_COMMITTER_DATE='2020-01-02T00:00:00Z' git -C "$G" commit -q -m c2
C2="$(git -C "$G" rev-parse HEAD)"
echo "commits: $C1 $C2"

for src in "$G" "file://$G"; do
	d="$T/g1"; rm -rf "$d"
	rc=0; out="$(git_pin "$src" "$d" "$C1" 2>&1)" || rc=$?
	echo "$out" | norm; echo "rc=$rc"
	{ [ "$rc" = 0 ] && [ "$(git -C "$d" rev-parse HEAD)" = "$C1" ] && [ "$(cat "$d/f")" = one ]; } \
		&& ok "pin to older commit ($(printf '%s' "$src" | sed 's#^\(file://\).*#\1#;s#^/.*#path#')): HEAD and content match" || fail "pin $src rc=$rc"
done
d="$T/g2"
rc=0; out="$(git_pin "$G" "$d" "$C2" 2>&1)" || rc=$?
{ [ "$rc" = 0 ] && [ "$(cat "$d/f")" = two ]; } && ok "pin to newest commit" || fail "pin C2 rc=$rc"
rc=0; out="$(git_pin "$G" "$d" "$C2" 2>&1)" || rc=$?
[ "$rc" = 0 ] && ok "re-run on existing checkout is idempotent" || fail "idempotent rc=$rc"

d="$T/g3"
rc=0; out="$(git_pin "$G" "$d" "$(printf 'f%.0s' $(seq 40))" 2>&1)" || rc=$?
echo "$out" | grep '^fetch:' | norm; echo "rc=$rc"   # git's own fatal: lines are version-specific, not part of the golden
[ "$rc" = 1 ] && ok "non-existent sha rejected" || fail "wrong sha rc=$rc"
d="$T/g4"
rc=0; out="$(git_pin "$G" "$d" main 2>&1)" || rc=$?
echo "$out" | norm; echo "rc=$rc"
{ [ "$rc" = 2 ] && [ ! -e "$d/.git" ]; } && ok "branch name refused" || fail "branch name rc=$rc"
rc=0; out="$(git_pin "$G" "$d" "" 2>&1)" || rc=$?
[ "$rc" = 2 ] && ok "empty ref refused" || fail "empty ref rc=$rc"
rc=0; out="$(git_pin "$G" "$d" "${C1:0:12}" 2>&1)" || rc=$?
[ "$rc" = 2 ] && ok "abbreviated sha refused" || fail "short sha rc=$rc"
d="$T/g5"
rc=0; out="$(GS_ALLOW_UNPINNED=1 git_pin "$G" "$d" main 2>&1)" || rc=$?
echo "$out" | norm | sed "s/$C2/<C2>/"; echo "rc=$rc"
{ [ "$rc" = 0 ] && printf '%s' "$out" | grep -q WARNING && printf '%s' "$out" | grep -q "$C2"; } \
	&& ok "GS_ALLOW_UNPINNED=1 accepts a branch, warns, prints resolved sha" || fail "unpinned git rc=$rc"
mkdir "$T/g6"; echo junk > "$T/g6/x"
rc=0; out="$(git_pin "$G" "$T/g6" "$C1" 2>&1)" || rc=$?
[ "$rc" = 1 ] && ok "non-empty non-git dest refused" || fail "non-empty dest rc=$rc"
rc=0; out="$(git_pin "git://example.invalid/x.git" "$T/g7" "$C1" 2>&1)" || rc=$?
[ "$rc" = 1 ] && ok "git:// transport refused" || fail "git:// rc=$rc"

echo "== pin_from_manifest"
export T_GIT_REPO="$G" T_GIT_PIN="$C1" T_FILE_URL="$T/src.bin" T_FILE_PIN="$GOOD" T_NOPIN_URL="$T/src.bin" T_NOPIN_PIN=""
export T_BOTH_REPO="$G" T_BOTH_URL="$T/src.bin"
rc=0; pin_from_manifest T_GIT "$T/m1" >/dev/null 2>&1 || rc=$?
{ [ "$rc" = 0 ] && [ "$(git -C "$T/m1" rev-parse HEAD)" = "$C1" ]; } && ok "manifest NAME_REPO dispatches to git_pin" || fail "manifest git rc=$rc"
rc=0; pin_from_manifest T_FILE "$T/m2" >/dev/null 2>&1 || rc=$?
{ [ "$rc" = 0 ] && cmp -s "$T/src.bin" "$T/m2"; } && ok "manifest NAME_URL dispatches to fetch_file" || fail "manifest file rc=$rc"
rc=0; pin_from_manifest T_NOPIN "$T/m3" >/dev/null 2>&1 || rc=$?
{ [ "$rc" = 2 ] && [ ! -e "$T/m3" ]; } && ok "manifest with empty PIN refused" || fail "manifest nopin rc=$rc"
rc=0; pin_from_manifest T_BOTH "$T/m4" >/dev/null 2>&1 || rc=$?
[ "$rc" = 2 ] && ok "manifest with both REPO and URL refused" || fail "manifest both rc=$rc"
rc=0; pin_from_manifest T_NONE "$T/m5" >/dev/null 2>&1 || rc=$?
[ "$rc" = 2 ] && ok "manifest with unknown NAME refused" || fail "manifest none rc=$rc"
rc=0; pin_from_manifest 'bad;name' "$T/m6" >/dev/null 2>&1 || rc=$?
[ "$rc" = 2 ] && ok "manifest invalid NAME refused" || fail "manifest badname rc=$rc"
# real manifest: every component currently has an empty pin, so the default must refuse (no behaviour change)
( . "$REPO/build/versions.env"; pin_from_manifest WFB_NG "$T/m7" ) >/dev/null 2>&1; rc=$?
{ [ "$rc" = 2 ] && [ ! -e "$T/m7" ]; } && ok "real versions.env: empty WFB_NG_PIN refused" || fail "versions.env rc=$rc"

echo "== set -e / set -x compatibility"
for mode in "-e" "-ex" "-euo pipefail"; do
	rc=0; out="$(bash -c "set $mode"'; . "$1"; fetch_file "$2" "$3" "$4"; git_pin "$5" "$6" "$7"; echo reached-end' _ "$LIB" "$T/src.bin" "$T/x1_$$" "$GOOD" "$G" "$T/x2_$$" "$C1" 2>&1)" || rc=$?
	printf '%s' "$out" | tail -n 1 | grep -q reached-end && [ "$rc" = 0 ] && ok "bash $mode: success path runs to the end" || fail "bash $mode rc=$rc"
	rm -rf "$T/x1_$$" "$T/x2_$$"
done
rc=0; out="$(bash -e -c '. "$1"; fetch_file "$2" "$3" "$4"; echo NOT-REACHED' _ "$LIB" "$T/src.bin" "$T/x3" "$BADH" 2>&1)" || rc=$?
{ [ "$rc" != 0 ] && ! printf '%s' "$out" | grep -q NOT-REACHED; } && ok "set -e aborts the caller on a mismatch" || fail "set -e abort rc=$rc"
rc=0; out="$(bash -e -c '. "$1"; fetch_file "$2" "$3" "$4" || echo handled; echo after' _ "$LIB" "$T/src.bin" "$T/x4" "$BADH" 2>&1)" || rc=$?
{ [ "$rc" = 0 ] && printf '%s' "$out" | grep -q handled && printf '%s' "$out" | grep -q after; } && ok "'|| handler' works under set -e" || fail "handler rc=$rc"
bash -n "$LIB" && ok "bash -n fetch.sh" || fail "bash -n"

echo "== static: no TLS-bypass flags in build/lib/*.sh"
hits="$(grep -nE '(^|[[:space:]])-k([[:space:]]|$)|--insecure|curl[^#]*-[a-zA-Z]*k[a-zA-Z]*[[:space:]]|--no-check-certificate|--proto[[:space:]]+.?=http([[:space:]]|$)|sslverify[[:space:]]*=[[:space:]]*false|GIT_SSL_NO_VERIFY' "$REPO"/build/lib/*.sh | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' || true)"
[ -z "$hits" ] && ok "no -k / --insecure / -sk / --no-check-certificate / http-only proto" || { echo "$hits"; fail "TLS bypass flag found"; }
grep -q -- "--proto '=https'" "$LIB" && grep -q -- '--tlsv1.2' "$LIB" && grep -q -- '--fail' "$LIB" \
	&& ok "default fetch uses --fail --proto '=https' --tlsv1.2" || fail "curl safety flags missing"

echo "bad=$bad"
exit $bad
