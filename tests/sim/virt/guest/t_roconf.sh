#!/bin/bash
# Guest test: the image layout for gs.conf, with the project's REAL scripts. Modelled on the OpenIPC/Radxa image (docs/KNOWLEDGE.md 4a, gs/install.sh):
#   rootfs ext4 mounted READ-ONLY at /media/root-ro (the overlay lower layer, /dev/vda), /etc = overlayfs (lower root-ro/etc, upper tmpfs),
#   /etc/gs.conf is a SYMLINK (in the lower layer) to /config/gs.conf, /config = a separate WRITABLE partition (/dev/vdb ext4; /dev/vdc FAT when mkfs.fat
#   was available on the host: a Pi may keep /config on a FAT partition, docs/PI-PORT.md).
# Real: mount/overlayfs/ext4/vfat, gs/lib/gsconf.sh, gs/gs-applyconf.sh (sourced, like gs-init.sh and gs.sh do), gs/gsmenu.sh set-paths, flock, kill -9.
# Shims (only these): reboot, systemctl, nmcli, killall (logged to /run/shim.log). gs-init.sh is NOT run (it repartitions disks; it writes gs.conf only through
# the sourced gs-applyconf.sh, covered here).
. /virt/lib.sh
echo 7 > /proc/sys/kernel/printk 2>/dev/null   # roconf mounts real filesystems: keep kernel errors visible in the serial log (panic, EXT4/FAT-fs errors)
for t in flock setpriv stat chown md5sum comm setsid; do command -v "$t" >/dev/null || { fail "roconf.tools" "$t is not in the guest"; summary roconf; exit 1; }; done
mod_load overlay || { summary roconf; exit 1; }
mkdir -p /media/root-ro /config /upper /work /etc.base /usr/local/bin /run /tmp/shim-awk
[ -e /dev/fd ] || ln -s /proc/self/fd /dev/fd   # bash process substitution (comm <(...)) in gs-applyconf.sh needs it; devtmpfs has no /dev/fd
for n in reboot systemctl nmcli killall; do printf '#!/bin/sh\necho "%s $*" >> /run/shim.log\nexit 0\n' "$n" > "/usr/local/bin/$n"; chmod +x "/usr/local/bin/$n"; done
# an awk that hangs after the first 5 output lines: the writer is "in the middle of the write" until it is killed
printf '#!/bin/bash\n/usr/bin/awk "$@" | head -n 5; sleep 60\n' > /tmp/shim-awk/awk; chmod +x /tmp/shim-awk/awk

# ---------------------------------------------------------------- the image layout
mount -t ext4 -o ro /dev/vda /media/root-ro || { fail roconf.layout.rootfs "mount -o ro /dev/vda failed"; summary roconf; exit 1; }
mount -t ext4 /dev/vdb /config || { fail roconf.layout.config "mount /dev/vdb failed"; summary roconf; exit 1; }
mount -t tmpfs tmpfs /upper; mkdir -p /upper/u /upper/w
mount --bind /etc /etc.base
mount -t overlay overlay -o "lowerdir=/media/root-ro/etc:/etc.base,upperdir=/upper/u,workdir=/upper/w" /etc || { fail roconf.layout.overlay "overlay mount failed"; summary roconf; exit 1; }
ROOT_MD5="$(md5sum < /dev/vda)"
check roconf.layout.rootfs-is-read-only "touch on /media/root-ro succeeded" bash -c '! touch /media/root-ro/x 2>/tmp/ro.err && grep -q "Read-only file system" /tmp/ro.err'
eq roconf.layout.etc-gs.conf-is-a-symlink "/config/gs.conf" "$(readlink /etc/gs.conf)"
info "mounts: $(findmnt -no SOURCE,FSTYPE,OPTIONS /media/root-ro | cut -c1-60) | $(findmnt -no SOURCE,FSTYPE /config) | $(findmnt -no FSTYPE /etc)"
info "findmnt of the real gs.conf: $(findmnt -no SOURCE -T "$(readlink -f /etc/gs.conf)" 2>&1) (symlink target); /etc/gs.conf itself lives on: $(findmnt -no FSTYPE /etc)"

# gs.conf of the guest: the repository default, with the bonnet button layout so that gs-applyconf.sh has a layout to write
PRISTINE=/work/gs.conf.pristine
sed "s/^btn_pin_layout=.*/btn_pin_layout='bonnet'/" /gs-etc/gs.conf > "$PRISTINE"
. /gs/lib/gsconf.sh

val() { bash -c '. /config/gs.conf; eval "printf %s \"\${$1}\""' _ "$1"; }   # value of a gs.conf variable as the scripts see it
conf_valid() { bash -c '. /config/gs.conf; [ -n "$wifi_mode" ] && [ -n "$rec_dir" ] && [ -n "$gps_uart" ] && [ -n "$gps_uart_baudrate" ]' 2>/dev/null; }
leftovers() { find /config -maxdepth 1 \( -name 'gs.conf.new.*' -o -name 'gs.conf.bak.*' -o -name 'gs.conf.probe.*' \) | wc -l; }
st() { stat -c '%a %u:%g' /config/gs.conf; }
has_partial() { [ -n "$(find /config -maxdepth 1 -name 'gs.conf.new.*' -size +0 2>/dev/null)" ]; }
reset_conf() { # reset_conf: pristine content, same inode/mode/owner kept (cp onto the existing file)
	rm -f /config/custom.conf /config/custom-merged.conf /config/gs.conf.new.* /config/gs.conf.bak.*
	cp "$PRISTINE" /config/gs.conf
}
run_applyconf() { # sourced, like gs-init.sh:186 and gs.sh:11 (gs.conf variables are already loaded)
	: > /run/shim.log
	bash -c 'source /etc/gs.conf; source /gs/gs-applyconf.sh' >/tmp/ac.out 2>/tmp/ac.err </dev/null
	AC_RC=$?
}
gsmenu_set() { : > /run/shim.log; /gs/gsmenu.sh "$@" >/tmp/gm.out 2>/tmp/gm.err </dev/null; GM_RC=$?; }
no_copyup() { [ ! -e /upper/u/gs.conf ]; }

# ================================================================= scenarios on one /config
scenario() { # scenario LABEL
	local L="$1" st0 ino0 pid n

	reset_conf; chown 1000:1000 /config/gs.conf 2>/dev/null; chmod 640 /config/gs.conf 2>/dev/null; sync
	cp "$PRISTINE" /work/before; st0="$(st)"; ino0="$(stat -c %i /config/gs.conf)"
	info "$L: /config/gs.conf starts as mode/owner '$st0' on $(findmnt -no FSTYPE /config)"

	# ---- 1. the library through the symlink ----
	gsconf_set_quoted /etc/gs.conf rec_fps 90 2>/tmp/e; eq "roconf.$L.lib.update.rc" 0 $?
	eq "roconf.$L.lib.update.target-file-changed" 90 "$(val rec_fps)"
	eq "roconf.$L.lib.update.symlink-intact" "/config/gs.conf" "$(readlink /etc/gs.conf)"
	check "roconf.$L.lib.update.no-copy-up-into-the-overlay-upper" "gs.conf appeared in the upper layer" no_copyup
	eq "roconf.$L.lib.update.mode-owner-preserved" "$st0" "$(st)"
	eq "roconf.$L.lib.update.no-leftovers" 0 "$(leftovers)"
	eq "roconf.$L.lib.update.only-one-line-differs" 2 "$(diff /work/before /config/gs.conf | grep -c '^[<>]')"
	if [ "$L" = ext4 ]; then check "roconf.$L.lib.update.replaced-by-rename" "same inode" test "$(stat -c %i /config/gs.conf)" != "$ino0"; fi

	# ---- 2. killed in the middle of the write (SIGKILL: no handler runs) ----
	reset_conf; cp /config/gs.conf /work/before
	PATH=/tmp/shim-awk:$PATH setsid bash -c '. /gs/lib/gsconf.sh; gsconf_set_quoted /etc/gs.conf rec_fps 91' </dev/null >/dev/null 2>&1 &
	pid=$!
	check "roconf.$L.kill.partial-temp-file-seen" "writer never reached the half-written state" wait_for 15 has_partial
	kill -9 -- -"$pid" 2>/dev/null; wait "$pid" 2>/dev/null
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.kill.target-is-still-the-complete-old-file" 0 $?
	check "roconf.$L.kill.target-passes-the-check" "gs.conf not usable after the kill" conf_valid
	eq "roconf.$L.kill.stale-temp-file-of-the-dead-writer" 1 "$(leftovers)"
	gsconf_set_quoted /etc/gs.conf rec_fps 92 2>/tmp/e; eq "roconf.$L.kill.next-update.rc" 0 $?
	eq "roconf.$L.kill.next-update.sweeps-leftovers" 0 "$(leftovers)"
	eq "roconf.$L.kill.next-update.applied" 92 "$(val rec_fps)"

	# ---- 3. the directory is read-only (remount,ro: a real EROFS from the kernel) ----
	reset_conf; cp /config/gs.conf /work/before
	mount -o remount,ro /config; eq "roconf.$L.erofs.remount-ro" 0 $?
	gsconf_set_quoted /etc/gs.conf rec_fps 93 2>/tmp/e; eq "roconf.$L.erofs.update.rc" 1 $?
	check "roconf.$L.erofs.update.message-names-the-cause" "stderr: $(cat /tmp/e)" grep -q 'Read-only file system' /tmp/e
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.erofs.target-untouched" 0 $?
	gsconf_can_write /etc/gs.conf 2>/dev/null; eq "roconf.$L.erofs.can_write.rc" 1 $?
	mount -o remount,rw /config; gsconf_set_quoted /etc/gs.conf rec_fps 94 2>/tmp/e; eq "roconf.$L.erofs.after-remount-rw.rc" 0 $?
	eq "roconf.$L.erofs.after-remount-rw.applied" 94 "$(val rec_fps)"
	eq "roconf.$L.erofs.no-leftovers" 0 "$(leftovers)"

	# ---- 4. an unprivileged writer cannot create a file in the directory ----
	reset_conf; cp /config/gs.conf /work/before
	setpriv --reuid=1000 --regid=1000 --clear-groups bash -c '. /gs/lib/gsconf.sh; gsconf_set_quoted /etc/gs.conf rec_fps 95' >/dev/null 2>/tmp/e; eq "roconf.$L.eacces.update.rc" 1 $?
	check "roconf.$L.eacces.message-names-the-cause" "stderr: $(cat /tmp/e)" grep -q 'Permission denied' /tmp/e
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.eacces.target-untouched" 0 $?

	# ---- 5. an empty or cut file is refused, nothing is written ----
	: > /config/gs.conf
	gsconf_set_quoted /etc/gs.conf rec_fps 96 2>/tmp/e; eq "roconf.$L.cut.empty.rc" 1 $?
	eq "roconf.$L.cut.empty.stays-empty" 0 "$(stat -c %s /config/gs.conf)"
	head -c 5000 "$PRISTINE" > /config/gs.conf
	cp /config/gs.conf /work/before; gsconf_set_quoted /etc/gs.conf rec_fps 96 2>/tmp/e; eq "roconf.$L.cut.truncated.rc" 1 $?
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.cut.truncated.untouched" 0 $?
	reset_conf

	# ---- 6. the REAL gs-applyconf.sh: custom.conf merge + button layout, through the symlink ----
	cat > /config/custom.conf <<-'EOF'
	wifi_ssid=MyNet
	gps_uart_baudrate=9600
	wifi_password=it's
	hotspot_ssid=$(touch /work/PWNED)
	not a key line
	EOF
	st0="$(st)"; rm -f /work/PWNED
	run_applyconf
	info "$L applyconf rc=$AC_RC (0 or 1: the known last-line status, docs/KNOWLEDGE.md 4a)"
	info "$L applyconf stderr tail: $(grep -v '^+' /tmp/ac.err | tail -4 | tr '\n' '|') | last trace: $(tail -2 /tmp/ac.err | tr '\n' '|' | cut -c1-200)"
	check "roconf.$L.applyconf.merge.rc-is-0-or-1" "rc=$AC_RC; stderr tail: $(tail -3 /tmp/ac.err | tr '\n' '|')" test "$AC_RC" -le 1
	eq "roconf.$L.applyconf.merge.wifi_ssid" MyNet "$(val wifi_ssid)"
	eq "roconf.$L.applyconf.merge.gps_uart_baudrate" 9600 "$(val gps_uart_baudrate)"
	eq "roconf.$L.applyconf.merge.hostile-value-stored-inert" '$(touch /work/PWNED)' "$(val hotspot_ssid)"
	check "roconf.$L.applyconf.merge.hostile-value-not-executed" "/work/PWNED exists" test ! -e /work/PWNED
	eq "roconf.$L.applyconf.merge.quote-value-rejected-old-kept" "" "$(val wifi_password)"
	check "roconf.$L.applyconf.merge.custom.conf-consumed" "custom.conf=$(ls /config | tr '\n' ' ')" test -f /config/custom-merged.conf -a ! -f /config/custom.conf
	eq "roconf.$L.applyconf.layout.pins" "16 18 13 12 11 22 | |" "$(val btn_cu_pin) $(val btn_cd_pin) $(val btn_cl_pin) $(val btn_cr_pin) $(val btn_cm_pin) $(val btn_q1_pin) | $(val btn_q2_pin)|$(val btn_q3_pin)"
	eq "roconf.$L.applyconf.symlink-intact" "/config/gs.conf" "$(readlink /etc/gs.conf)"
	check "roconf.$L.applyconf.no-copy-up" "gs.conf in the upper layer" no_copyup
	eq "roconf.$L.applyconf.mode-owner-preserved" "$st0" "$(st)"
	eq "roconf.$L.applyconf.no-leftovers" 0 "$(leftovers)"
	check "roconf.$L.applyconf.rest-of-the-script-ran" "stdout: $(tail -3 /tmp/ac.out | tr '\n' '|')" grep -q 'radxa0 usb gadget network configure done' /tmp/ac.out
	check "roconf.$L.applyconf.no-reboot-needed" "shim.log: $(cat /run/shim.log | tr '\n' '|')" bash -c '! grep -q "^reboot" /run/shim.log'
	check "roconf.$L.applyconf.etc-writes-land-in-the-overlay-upper" "gpsd in upper? $(ls /upper/u/default 2>&1)" bash -c '[ -f /upper/u/default/gpsd ] && [ ! -e /media/root-ro/etc/default/gpsd ] && grep -q ttyS3 /etc/default/gpsd'

	# ---- 7. applyconf killed in the middle of the merge ----
	reset_conf; cp /config/gs.conf /work/before
	printf 'wifi_ssid=Net2\ngps_uart_baudrate=19200\n' > /config/custom.conf
	PATH=/tmp/shim-awk:$PATH setsid bash -c 'source /etc/gs.conf; source /gs/gs-applyconf.sh' </dev/null >/dev/null 2>&1 &
	pid=$!
	check "roconf.$L.applyconf-kill.partial-temp-file-seen" "applyconf never reached the half-written state" wait_for 20 has_partial
	kill -9 -- -"$pid" 2>/dev/null; wait "$pid" 2>/dev/null
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.applyconf-kill.gs.conf-complete-and-unchanged" 0 $?
	check "roconf.$L.applyconf-kill.gs.conf-usable" "gs.conf cut" conf_valid
	check "roconf.$L.applyconf-kill.custom.conf-kept-for-the-next-run" "$(ls /config | tr '\n' ' ')" test -f /config/custom.conf -a ! -f /config/custom-merged.conf
	run_applyconf
	check "roconf.$L.applyconf-rerun.merged" "ssid=$(val wifi_ssid) baud=$(val gps_uart_baudrate)" test "$(val wifi_ssid)/$(val gps_uart_baudrate)" = Net2/19200
	eq "roconf.$L.applyconf-rerun.sweeps-leftovers" 0 "$(leftovers)"

	# ---- 8. applyconf with a read-only /config: a clear error, nothing consumed, nothing applied ----
	reset_conf; printf 'wifi_ssid=Net3\n' > /config/custom.conf; cp /config/gs.conf /work/before; sync
	mount -o remount,ro /config
	run_applyconf
	eq "roconf.$L.applyconf-erofs.with-custom.conf.rc" 1 "$AC_RC"
	check "roconf.$L.applyconf-erofs.with-custom.conf.message" "stderr: $(grep -E 'gsconf:|\[error\]' /tmp/ac.err | tr '\n' '|')" grep -q 'custom.conf is kept' /tmp/ac.err
	check "roconf.$L.applyconf-erofs.with-custom.conf.names-the-cause" "stderr: $(grep gsconf: /tmp/ac.err | tr '\n' '|')" grep -q 'Read-only file system' /tmp/ac.err
	check "roconf.$L.applyconf-erofs.with-custom.conf.kept" "$(ls /config | tr '\n' ' ')" test -f /config/custom.conf -a ! -f /config/custom-merged.conf
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.applyconf-erofs.with-custom.conf.gs.conf-untouched" 0 $?
	check "roconf.$L.applyconf-erofs.with-custom.conf.nothing-applied" "reboot/mount in shim.log or the rest ran" bash -c '! grep -q "br0 configure done" /tmp/ac.out && ! grep -q "^reboot" /run/shim.log'
	mount -o remount,rw /config; rm -f /config/custom.conf; mount -o remount,ro /config   # no custom.conf: the button-layout write is what fails now
	run_applyconf
	check "roconf.$L.applyconf-erofs.layout-write.rc-nonzero" "rc=$AC_RC" test "$AC_RC" -ne 0
	check "roconf.$L.applyconf-erofs.layout-write.not-silent" "stderr: $(grep gsconf: /tmp/ac.err | tr '\n' '|')" grep -q 'gsconf: cannot create a temp file next to /config/gs.conf' /tmp/ac.err
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.applyconf-erofs.layout-write.gs.conf-untouched" 0 $?
	mount -o remount,rw /config; run_applyconf
	check "roconf.$L.applyconf-erofs.after-remount-rw.layout-written" "pins: $(val btn_cu_pin) $(val btn_cm_pin)" test "$(val btn_cu_pin)/$(val btn_cm_pin)" = 16/11

	# ---- 9. gsmenu write path (real gs/gsmenu.sh), through the symlink ----
	reset_conf; chmod 640 /config/gs.conf; st0="$(st)"
	gsmenu_set set gs system rec_fps 90; eq "roconf.$L.gsmenu.rec_fps.rc" 0 "$GM_RC"; eq "roconf.$L.gsmenu.rec_fps.value" 90 "$(val rec_fps)"
	gsmenu_set set gs system resolution 1280x720@60; eq "roconf.$L.gsmenu.resolution.rc" 0 "$GM_RC"; eq "roconf.$L.gsmenu.resolution.value" 1280x720@60 "$(val screen_mode)"
	gsmenu_set set gs system gs_rendering off; eq "roconf.$L.gsmenu.gs_rendering.rc" 0 "$GM_RC"; eq "roconf.$L.gsmenu.gs_rendering.value" msposd_air "$(val osd_type)"
	check "roconf.$L.gsmenu.gs_rendering.rest-ran" "shim.log: $(cat /run/shim.log)" grep -q '^killall -q msposd' /run/shim.log
	gsmenu_set set gs wfbng adaptivelink off; eq "roconf.$L.gsmenu.adaptivelink.rc" 0 "$GM_RC"; eq "roconf.$L.gsmenu.adaptivelink.value" no "$(val alink_enable)"
	eq "roconf.$L.gsmenu.symlink-intact" "/config/gs.conf" "$(readlink /etc/gs.conf)"
	check "roconf.$L.gsmenu.no-copy-up" "gs.conf in the upper layer" no_copyup
	eq "roconf.$L.gsmenu.mode-owner-preserved" "$st0" "$(st)"
	eq "roconf.$L.gsmenu.no-leftovers" 0 "$(leftovers)"
	gsmenu_set set gs system rec_fps "it's"; eq "roconf.$L.gsmenu.hostile-value.rc" 1 "$GM_RC"
	check "roconf.$L.gsmenu.hostile-value.message" "stderr: $(cat /tmp/gm.err)" grep -q 'gsconf: rejected' /tmp/gm.err
	eq "roconf.$L.gsmenu.hostile-value.old-value-kept" 90 "$(val rec_fps)"
	check "roconf.$L.gsmenu.hostile-value.gs.conf-usable" "gs.conf unusable" conf_valid
	cp /config/gs.conf /work/before; mount -o remount,ro /config
	gsmenu_set set gs system rec_fps 75; eq "roconf.$L.gsmenu.erofs.rc" 1 "$GM_RC"
	check "roconf.$L.gsmenu.erofs.not-silent" "stderr: $(cat /tmp/gm.err)" grep -q 'Read-only file system' /tmp/gm.err
	cmp -s /config/gs.conf /work/before; eq "roconf.$L.gsmenu.erofs.untouched" 0 $?
	mount -o remount,rw /config
	gsmenu_set set gs system rec_fps 75; eq "roconf.$L.gsmenu.after-remount-rw.rc" 0 "$GM_RC"; eq "roconf.$L.gsmenu.after-remount-rw.value" 75 "$(val rec_fps)"
	n="$(cat /config/gs.conf | wc -l)"; eq "roconf.$L.gsmenu.line-count-unchanged" "$(wc -l < "$PRISTINE")" "$n"
}

scenario ext4
eq roconf.rootfs.image-unchanged-by-all-of-the-above "$ROOT_MD5" "$(md5sum < /dev/vda)"

# ================================================================= FAT /config (a Pi may keep /config on a FAT partition): hard links and chown do not exist there
if [ -b /dev/vdc ]; then
	mod_load nls_iso8859-1
	if umount /config 2>/tmp/vfat.err && mount -t vfat /dev/vdc /config 2>>/tmp/vfat.err; then
		cp "$PRISTINE" /config/gs.conf; scenario vfat
	else fail roconf.vfat.mount "cannot switch /config to /dev/vdc (vfat): $(cat /tmp/vfat.err) | fs: $(grep -c fat /proc/filesystems) | nls: $(grep -c nls /proc/modules) | dmesg: $(dmesg 2>&1 | tail -3 | tr '\n' '|')"; fi
	umount /config 2>/dev/null; mount -t ext4 /dev/vdb /config
else
	echo "VIRT-SKIP roconf.vfat :: no mkfs.fat on the host and dosfstools could not be downloaded: the FAT /config variant was not run"
fi

# ================================================================= the real script remounts the RO lower layer (fstab / smb.conf / cmdline paths)
reset_conf; ROOT_RO_BEFORE="$(md5sum < /media/root-ro/etc/samba/smb.conf)"
printf 'rec_dir=/Videos2\n' > /config/custom.conf
run_applyconf
eq roconf.rootfs-remount.merged-rec_dir /Videos2 "$(val rec_dir)"
check roconf.rootfs-remount.real-mount-remount-rw-worked "mount opts: $(findmnt -no OPTIONS /media/root-ro)" bash -c 'findmnt -no OPTIONS /media/root-ro | grep -qw rw'
check roconf.rootfs-remount.reboot-requested "shim.log: $(tr '\n' '|' < /run/shim.log)" grep -q '^reboot' /run/shim.log
check roconf.rootfs-remount.smb.conf-edit-copied-up-not-written-to-the-lower-layer "smb upper? $(ls /upper/u/samba 2>&1)" bash -c '[ -f /upper/u/samba/smb.conf ] && grep -q /Videos2 /etc/samba/smb.conf && [ "$(md5sum < /media/root-ro/etc/samba/smb.conf)" = "$1" ]' _ "$ROOT_RO_BEFORE"
eq roconf.rootfs-remount.symlink-intact "/config/gs.conf" "$(readlink /etc/gs.conf)"
mount -o remount,ro /media/root-ro 2>/dev/null

# ================================================================= control: the naive `sed -i /etc/gs.conf` (no readlink -f) on this layout
mkdir -p /upper/c/u /upper/c/w /upper/c/m
mount -t overlay overlay -o "lowerdir=/media/root-ro/etc,upperdir=/upper/c/u,workdir=/upper/c/w" /upper/c/m
reset_conf; cp /config/gs.conf /work/before
sed -i "s/^rec_fps=.*/rec_fps='1'/" /upper/c/m/gs.conf
check roconf.control.naive-sed-i-replaces-the-symlink-in-the-overlay "still a symlink: the harness cannot see the failure mode" bash -c '[ ! -L /upper/c/m/gs.conf ] && [ -f /upper/c/u/gs.conf ]'
cmp -s /config/gs.conf /work/before; eq roconf.control.naive-sed-i-never-touches-the-real-file 0 $?
umount /upper/c/m 2>/dev/null
summary roconf
