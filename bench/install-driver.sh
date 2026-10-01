#!/usr/bin/env bash
# Build/install the patched Realtek monitor-mode driver via DKMS and set bench-safe
# (minimal) TX power. DRIVER=8812au (default, pinned) or 8812eu (unpinned HEAD).
# Needs matching kernel headers for the RUNNING kernel; reboot after kernel updates.
# Usage: sudo ./install-driver.sh
. "$(dirname "$0")/lib.sh"
need_root "$@"

log "kernel $(uname -r), arch $(uname -m)"
run apt-get install -y dkms git build-essential bc

if [ ! -e "/lib/modules/$(uname -r)/build" ]; then
	warn "no headers for $(uname -r); trying Raspberry Pi header packages (names are NOT verified for every release)"
	for pkg in linux-headers-rpi-v8 raspberrypi-kernel-headers; do
		if apt-cache show "$pkg" >/dev/null 2>&1; then run apt-get install -y "$pkg" && break; fi
	done
	[ -e "/lib/modules/$(uname -r)/build" ] || die "still no /lib/modules/$(uname -r)/build: update+reboot so the running kernel matches the installed headers"
fi

src=/opt/gs-bench/src; run mkdir -p "$src"
case "$DRIVER" in
	8812au)
		url=https://github.com/svpcom/rtl8812au.git
		opts='options 88XXau_wfb rtw_tx_pwr_idx_override='"$TX_PWR_IDX" ;;
	8812eu)
		url=https://github.com/svpcom/rtl8812eu.git
		warn "8812eu is not pinned to a commit; record 'git rev-parse HEAD' of $src/rtl8812eu for reproducibility"
		opts='options 8812eu rtw_tx_pwr_by_rate=0 rtw_tx_pwr_lmt_enable=0' ;;
	*) die "DRIVER must be 8812au or 8812eu" ;;
esac
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
$opts
EOT
fi
if command -v update-initramfs >/dev/null; then run update-initramfs -k all -u || true; fi
log "driver installed. REBOOT, then verify: ethtool -i wlanX  (driver version must be empty for the patched module)"
