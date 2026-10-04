#!/bin/bash
# Hardware path/sensor helpers (milestone M3d). Values come from the board profile; fallbacks are the Radxa Zero 3W
# literals that were hard-coded in fan.sh / gs.sh / gsmenu.sh, so behaviour is unchanged if board.sh or a key is missing.
# Usage: . /gs/lib/hw.sh; hw_cpu_temp_file
# Every helper always succeeds (prints a value), so callers' `set -e` semantics are not affected.

_HW_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# _hw_get KEY FALLBACK: print board_get KEY, or FALLBACK when the board lib/profile/key is unavailable.
# Runs in a subshell so board.conf variables never leak into the calling script.
_hw_get() {
	local v
	v="$( . "$_HW_LIB_DIR/board.sh" 2>/dev/null && board_get "$1" 2>/dev/null )" || v="$2"
	printf '%s' "$v"
}

# sysfs file with the CPU temperature in millidegrees (Radxa: /sys/class/thermal/thermal_zone0/temp)
hw_cpu_temp_file() { _hw_get THERMAL_CPU_TEMP_FILE '/sys/class/thermal/thermal_zone0/temp'; }
# PWM chip sysfs path prefix; append the chip number (Radxa: /sys/class/pwm/pwmchip)
hw_pwm_base() { _hw_get PWM_SYSFS_BASE '/sys/class/pwm/pwmchip'; }
# home directory of the default user, where the ruby runtime lives (Radxa: /home/radxa)
hw_home_dir() { _hw_get HOME_DIR '/home/radxa'; }
# onboard wifi interface name (Radxa: wifi0)
hw_wifi_iface() { _hw_get WIFI_ONBOARD_IFACE 'wifi0'; }
# I2C bus number of the external RTC (Radxa: 4, i.e. /dev/i2c-4)
hw_rtc_i2c_bus() { _hw_get RTC_I2C_BUS '4'; }
# fan_kernel_managed: status 0 when the board's fan is driven by the kernel/firmware (Pi 5 pwm-fan on RP1), so gs/fan.sh must NOT run.
# A predicate (status, no output): call inside `if`/`&&`; false (the Radxa behaviour) when the board lib/profile/key is unavailable.
hw_fan_kernel_managed() { [ "$(_hw_get FAN_KERNEL_MANAGED 'no')" = 'yes' ]; }
# overlay enabling mechanism: rename (Radxa: *.dtbo.disabled <-> *.dtbo) or config-txt (Pi: dtoverlay= line in CONFIG_TXT)
hw_dtbo_mode() { _hw_get DTBO_MODE 'rename'; }
# partition table type: gpt (Radxa) or mbr (Pi OS images)
hw_part_table() { _hw_get PART_TABLE 'gpt'; }
# console device for the boot banner (Radxa: /dev/ttyFIQ0; Pi: the serial console of the profile)
hw_console_tty() { _hw_get CONSOLE_TTY '/dev/ttyFIQ0'; }
# network owner: networkd (systemd-networkd files, Radxa) or networkmanager (nmcli, Raspberry Pi OS)
hw_net_backend() { _hw_get NET_BACKEND 'networkd'; }
# label of the recording partition (Radxa: the partition made by gs-init.sh is labelled videos)
hw_videos_label() { _hw_get PART_VIDEOS_LABEL 'videos'; }
# partition number of the recording partition on the system disk (Radxa: 5 after gs-init.sh, but gs.sh used p4 as a legacy fallback)
