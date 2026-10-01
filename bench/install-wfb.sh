#!/usr/bin/env bash
# Install wfb-ng from the project's apt repository (same method as upstream
# scripts/install_gs.sh) and handle keys.
#   gs : generates gs.key + drone.key in /etc (only if /etc/gs.key is missing)
#   air: requires /etc/drone.key copied from the GS (never generates)
# Usage: sudo ./install-wfb.sh <air|gs>
. "$(dirname "$0")/lib.sh"
role_check "${1:-}"; need_root "$@"

# shellcheck disable=SC1091
codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
[ -n "$codename" ] || die "cannot determine distribution codename"

log "adding apt.wfb-ng.org repository for $codename"
run bash -c 'curl -fsS https://apt.wfb-ng.org/public.asc | gpg --dearmor --yes -o /usr/share/keyrings/wfb-ng.gpg'
if [ "$DRY_RUN" = 1 ]; then
	echo "[dry-run] write /etc/apt/sources.list.d/wfb-ng.list (suite $codename, component master)"
else
	echo "deb [signed-by=/usr/share/keyrings/wfb-ng.gpg] https://apt.wfb-ng.org/ $codename master" > /etc/apt/sources.list.d/wfb-ng.list
fi
if ! run apt-get update || ! run apt-get install -y wfb-ng; then
	rm -f /etc/apt/sources.list.d/wfb-ng.list /usr/share/keyrings/wfb-ng.gpg
	die "wfb-ng package install failed (no prebuilt package for '$codename'/arch?). Build from source: https://github.com/svpcom/wfb-ng (make deb)"
fi

# packet-filter JIT, as recommended by the wfb-ng Setup-HOWTO
if ! grep -qs '^net.core.bpf_jit_enable' /etc/sysctl.conf; then
	run bash -c 'echo "net.core.bpf_jit_enable = 1" >> /etc/sysctl.conf'
	run sysctl -p
fi

case "$1" in
	gs)
		if [ -f /etc/gs.key ]; then
			log "/etc/gs.key exists: keeping it (re-running wfb_keygen would break pairing)"
		else
			log "generating keys (wfb_keygen) in /etc"
			run bash -c 'cd /etc && wfb_keygen'
		fi
		log "copy the drone key to the AIR node:  scp /etc/drone.key root@<air-ip>:/etc/drone.key"
		;;
	air)
		[ -f /etc/drone.key ] || die "/etc/drone.key missing. On the GS run: scp /etc/drone.key root@<this-node>:/etc/drone.key"
		;;
esac
log "wfb-ng installed. next: sudo ./configure-wfb.sh $1"
