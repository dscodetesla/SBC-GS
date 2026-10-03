#!/usr/bin/env bash
# Read-only hardware facts for the bench day: writes one JSON (HW-tagged facts) and prints a short summary.
# Never transmits, never loads/unloads modules, never writes outside the output file.
#   bench/doctor.sh [OUT.json]       default: bench/out/doctor-<host>-<utc>.json
# Missing tools are recorded as null (not an error): the same script runs on a laptop (then most fields are null).
set -u
host="$(hostname 2>/dev/null || echo unknown)"
out="${1:-$(dirname "${BASH_SOURCE[0]}")/out/doctor-${host}-$(date -u +%Y%m%dT%H%M%SZ).json}"
mkdir -p "$(dirname "$out")"

have() { command -v "$1" >/dev/null 2>&1; }
run() { if have "$1"; then "$@" 2>/dev/null; fi; }
J() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'; }
field() { # name, value-producing command output on stdin -> "name": json-string or null
    local v
    v="$(cat)"
    if [ -n "$v" ]; then printf '  "%s": %s' "$1" "$(printf '%s' "$v" | J)"; else printf '  "%s": null' "$1"; fi
}

{
    echo '{'
    echo '  "schema": "sbc-gs-doctor/1", "tag": "HW",'
    printf '  "utc": "%s",\n' "$(date -u +%FT%TZ)"
    { uname -r; } | field kernel; echo ','
    { getconf PAGESIZE; } | field pagesize; echo ','
    { tr -d '\0' </proc/device-tree/model 2>/dev/null; } | field board_model; echo ','
    { run vcgencmd get_throttled; } | field get_throttled; echo ','
    { run vcgencmd measure_temp; } | field soc_temp; echo ','
    { run vcgencmd pmic_read_adc | grep -E 'EXT5V_V|VDD_CORE_A' ; } | field pi5_pmic_5v_rail; echo ','
    { run vcgencmd get_config usb_max_current_enable; } | field usb_max_current_enable; echo ','
    { cat /sys/class/thermal/thermal_zone0/temp; } | field thermal_zone0_mC; echo ','
    { run lsusb -t; } | field lsusb_tree; echo ','
    { run lsusb | grep -iE '0bda|rtl|realtek|1a86|0483|ttyACM'; } | field usb_radio_fc_candidates; echo ',' # cfg-ok: USB IDs
    { run iw list | grep -E '^Wiphy|\* monitor|Supported interface modes' | head -20; } | field iw_monitor_capable; echo ','
    { run iw dev; } | field iw_dev; echo ','
    { lsmod | grep -E '88x2|8812|8814|8821|rtw88|rtl8xxxu|mac80211_hwsim'; } | field wifi_modules; echo ',' # cfg-ok: driver names
    { run gpioinfo | head -60; } | field gpioinfo_head; echo ','
    { dmesg 2>/dev/null | grep -ciE 'usb .*disconnect|over-?current|under-?voltage'; } | field dmesg_usb_power_events; echo ','
    { ls /dev/ttyACM* /dev/ttyUSB* /dev/input/js* 2>/dev/null; } | field serial_and_joystick; echo ','
    { run systemctl is-system-running; } | field systemd_state
    echo
    echo '}'
} >"$out" 2>/dev/null

if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out" 2>/dev/null; then
    echo "doctor: wrote $out"
    python3 - "$out" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
missing = [k for k, v in d.items() if v is None]
print("  kernel=%s pagesize=%s board=%s" % (d.get("kernel"), d.get("pagesize"), d.get("board_model")))
print("  get_throttled=%s" % d.get("get_throttled"))
print("  not available here (null): %d of %d fields: %s" % (len(missing), len(d), ", ".join(missing)))
PY
else
    echo "doctor: FAILED to produce valid JSON in $out" >&2
    exit 1
fi
