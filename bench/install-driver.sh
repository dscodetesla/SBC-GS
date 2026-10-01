#!/usr/bin/env bash
# Build/install the patched Realtek monitor-mode driver via DKMS and set bench-safe
# (minimal) TX power. DRIVER=8812au (default, pinned) or 8812eu (unpinned HEAD).
# Needs matching kernel headers for the RUNNING kernel; reboot after kernel updates.
# Usage: sudo ./install-driver.sh
. "$(dirname "$0")/lib.sh"
need_root "$@"

log "kernel $(uname -r), arch $(uname -m)"
run apt-get install -y dkms git build-essential bc

src=/opt/gs-bench/src; run mkdir -p "$src"
case "$DRIVER" in
	8812au)
		url=https://github.com/svpcom/rtl8812au.git
		opts='options 88XXau_wfb rtw_tx_pwr_idx_override='"$TX_PWR_IDX" ;;
	8812eu)
		url=https://github.com/svpcom/rtl8812eu.git
		warn "8812eu is not pinned to a commit; record 'git rev-parse HEAD' of $src/rtl8812eu for reproducibility"
		opts='options 8812eu rtw_tx_pwr_by_rate=0 rtw_tx_pwr_lmt_enable=0' ;;
	8814au)
		# In-kernel rtw88_8814au: no out-of-tree code. File exists in mainline from v6.15
		# (404 at v6.14); monitor/injection behaviour is NOT documented: test it yourself.
		warn "8814au: wfb-ng officially supports only 8812au/8812eu ('8814au ... not supported by author', wfb-ng wiki WiFi-hardware)"
		modinfo rtw88_8814au >/dev/null 2>&1 || die "kernel $(uname -r) has no rtw88_8814au (needs >= 6.15 and the module enabled)"
		log "using in-kernel rtw88_8814au; set WFB_NICS in env: wfb-ng does not auto-detect this adapter"
		log "validate injection with the A/B loss test in docs/BENCH-HARDWARE.md before trusting it"
		exit 0 ;;
	8814au-morrownr)
		[ "${ALLOW_UNPINNED:-0}" = 1 ] || die "8814au-morrownr clones UNPINNED third-party code and builds it as root. The README at morrownr/8814au/main belongs to fork joseguzman1337/8814au (provenance unclear). Re-run with ALLOW_UNPINNED=1 only after you reviewed the source."
		url=https://github.com/morrownr/8814au.git
		warn "unpinned build of morrownr/8814au: record 'git rev-parse HEAD' of $src/rtl8814au-morrownr"
		opts='# 8814au: no module options set by this script' ;;
	*) die "DRIVER must be 8812au, 8812eu, 8814au (in-kernel) or 8814au-morrownr" ;;
esac
if [ ! -e "/lib/modules/$(uname -r)/build" ]; then
	warn "no headers for $(uname -r); trying Raspberry Pi header packages (names are NOT verified for every release)"
	for pkg in linux-headers-rpi-v8 raspberrypi-kernel-headers; do
		if apt-cache show "$pkg" >/dev/null 2>&1; then run apt-get install -y "$pkg" && break; fi
	done
	[ -e "/lib/modules/$(uname -r)/build" ] || die "still no /lib/modules/$(uname -r)/build: update+reboot so the running kernel matches the installed headers"
fi

dir="$src/rtl$DRIVER"
[ -d "$dir/.git" ] || run git clone "$url" "$dir"
if [ "$DRIVER" = 8812au ]; then
	run git -C "$dir" fetch --all --tags
	run git -C "$dir" checkout "$RTL8812AU_REF" || die "commit $RTL8812AU_REF not found; set RTL8812AU_REF in env"
fi
( cd "$dir" && run ./dkms-install.sh )

# blacklist stock drivers (see wfb-ng Setup-HOWTO) and set bench-safe TX power
if [ "$DRY_RUN" = 1 ]; then
	echo "[dry-run] write $(cfgpath /etc/modprobe.d/wfb.conf): blacklist 88XXau 8812au rtl8812au + '$opts'"
else
	mkdir -p "$(dirname "$(cfgpath /etc/modprobe.d/wfb.conf)")"
	cat > "$(cfgpath /etc/modprobe.d/wfb.conf)" <<EOT
# blacklist stock modules
blacklist 88XXau
blacklist 8812au
blacklist rtl8812au
blacklist rtl88x2bs
$([ "$DRIVER" = 8814au-morrownr ] && echo blacklist rtw88_8814au)
$opts
EOT
fi
if command -v update-initramfs >/dev/null; then run update-initramfs -k all -u || true; fi
log "driver installed. REBOOT, then verify: ethtool -i wlanX  (driver version must be empty for the patched module)"
