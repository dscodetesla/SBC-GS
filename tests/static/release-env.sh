#!/usr/bin/env bash
# Static+functional check of the chroot-env block in build/release.sh (audit S2): the root-login switches must reach
# build.sh inside the chroot, only when set, via the environment, and a password must never appear in xtrace or argv.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REL="$REPO/build/release.sh"
bad=0
ok()   { echo "ok $*"; }
fail() { echo "FAIL $*"; bad=1; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
sed -n '/^# >>> chroot-env/,/^# <<< chroot-env/p' "$REL" > "$T/block.sh"
[ -s "$T/block.sh" ] && ok "chroot-env block present" || { fail "chroot-env block missing"; echo "bad=1"; exit 1; }
# fake chroot: records what the child process sees (names and values through its environment, argv separately)
mkdir -p "$T/bin"
cat > "$T/bin/chroot" <<'SH'
#!/bin/bash
{ echo "argv: $*"; echo "legacy=${GS_LEGACY_ROOT_LOGIN-<unset>}"; echo "pw=${GS_ROOT_PASSWORD-<unset>}"; } > "$CHROOT_OUT"
SH
chmod +x "$T/bin/chroot"
run() { # run <legacy|-> <pw|-> ; prints nothing, fills $T/out and $T/err
	local l="$1" p="$2"
	( unset GS_LEGACY_ROOT_LOGIN GS_ROOT_PASSWORD
	  [ "$l" = - ] || export GS_LEGACY_ROOT_LOGIN="$l"
	  [ "$p" = - ] || export GS_ROOT_PASSWORD="$p"
	  export CHROOT_OUT="$T/out" PATH="$T/bin:$PATH"
	  ROOTFS=/fake bash -c 'set -e; set -x; . "$1"; echo done' _ "$T/block.sh" ) > "$T/stdout" 2> "$T/err"
}
run - -;            grep -q '^legacy=<unset>' "$T/out" && grep -q '^pw=<unset>' "$T/out" && ok "nothing set -> nothing exported" || fail "unset variables leaked into the chroot"
run 1 -;            grep -q '^legacy=1' "$T/out" && grep -q '^pw=<unset>' "$T/out" && ok "GS_LEGACY_ROOT_LOGIN reaches the chroot" || fail "legacy flag did not reach the chroot"
SECRET='s3cr3t-Pa$$ w0rd'
run - "$SECRET";    grep -q "^pw=$SECRET" "$T/out" && ok "GS_ROOT_PASSWORD reaches the chroot unchanged" || fail "password did not reach the chroot"
grep -q 's3cr3t' "$T/err" && fail "password leaked into xtrace (stderr)" || ok "password absent from xtrace"
grep -q 's3cr3t' "$T/stdout" && fail "password leaked into stdout" || ok "password absent from stdout"
grep -qx 'argv: /fake /root/build.sh' "$T/out" && ok "argv is only: rootfs and /root/build.sh (no secrets)" || fail "unexpected chroot argv: $(head -1 "$T/out")"
grep -q '^done' "$T/stdout" && ok "script continues after the block (set -e/xtrace state restored)" || fail "block aborted the script"
grep -q '^+ echo done' "$T/err" && ok "xtrace is back on after the block" || fail "xtrace not restored"
echo "bad=$bad"
exit $bad
