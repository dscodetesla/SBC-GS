#!/usr/bin/env bash
# Read-only hardware facts for the bench day: writes one JSON (HW-tagged facts) and prints a short summary.
# Never transmits, never loads/unloads modules, never writes outside the output file.
#   bench/doctor.sh [OUT.json]       default: bench/out/doctor-<host>-<utc>.json
# Missing tools are recorded as null (not an error): the same script runs on a laptop (then most fields are null).
# DOCTOR_ROOT=<dir> prefixes /proc and /sys reads (tests/sim/powerlab replays a fake tree; empty = the real system).
# New fields are only added to sbc-gs-doctor/1; bench/ingest.py consumes them (docs/SIM-POWERLAB.md).
set -u
root="${DOCTOR_ROOT:-}"
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

dmesg_txt=""; dmesg_rc=""
if have dmesg; then dmesg_txt="$(dmesg 2>/dev/null)"; dmesg_rc=$?; fi
dmesg_re='under-?voltage|voltage normali|over-?current|USB disconnect|new .* USB device|New USB device found|device descriptor read|unable to enumerate|Cannot enable|connect-debounce|disabled by hub'

{
    echo '{'
    echo '  "schema": "sbc-gs-doctor/1", "tag": "HW",'
    printf '  "utc": "%s",\n' "$(date -u +%FT%TZ)"
    { uname -r; } | field kernel; echo ','
    { getconf PAGESIZE; } | field pagesize; echo ','
    { tr -d '\0' <"$root/proc/device-tree/model" 2>/dev/null; } | field board_model; echo ','
    { run vcgencmd get_throttled; } | field get_throttled; echo ','
    { run vcgencmd measure_temp; } | field soc_temp; echo ','
    { run vcgencmd pmic_read_adc | grep -E 'EXT5V_V|VDD_CORE_A' ; } | field pi5_pmic_5v_rail; echo ','
    { run vcgencmd get_config usb_max_current_enable; } | field usb_max_current_enable; echo ','
    { cat "$root/sys/class/thermal/thermal_zone0/temp"; } | field thermal_zone0_mC; echo ','
    { run lsusb -t; } | field lsusb_tree; echo ','
    { run lsusb | grep -iE '0bda|rtl|realtek|1a86|0483|ttyACM'; } | field usb_radio_fc_candidates; echo ',' # cfg-ok: USB IDs
    { run iw list | grep -E '^Wiphy|\* monitor|Supported interface modes' | head -20; } | field iw_monitor_capable; echo ','
    { run iw dev; } | field iw_dev; echo ','
    { lsmod | grep -E '88x2|8812|8814|8821|rtw88|rtl8xxxu|mac80211_hwsim'; } | field wifi_modules; echo ',' # cfg-ok: driver names
    { run gpioinfo | head -60; } | field gpioinfo_head; echo ','
    { printf '%s\n' "$dmesg_txt" | grep -ciE 'usb .*disconnect|over-?current|under-?voltage'; } | field dmesg_usb_power_events; echo ','
    printf '%s' "$dmesg_rc" | field dmesg_rc; echo ','
    { printf '%s\n' "$dmesg_txt" | grep -iE "$dmesg_re" | tail -200; } | field dmesg_power_lines; echo ','
    { run vcgencmd pmic_read_adc; } | field pi5_pmic_adc_full; echo ','
    { run vcgencmd measure_clock arm; } | field arm_clock; echo ','
    { for f in "$root"/proc/device-tree/chosen/power/*; do [ -f "$f" ] && printf '%s=%s\n' "$(basename "$f")" "$(od -An -v -tx1 "$f" | tr -d ' \n')"; done; } | field dt_chosen_power; echo ','
    { cut -d' ' -f1 "$root/proc/uptime"; } | field uptime_s; echo ','
    { cat "$root/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"; } | field cpu0_cur_freq_khz; echo ','
    { for z in "$root"/sys/class/thermal/thermal_zone*; do [ -r "$z/temp" ] && printf '%s type=%s temp=%s\n' "$(basename "$z")" "$(cat "$z/type" 2>/dev/null)" "$(cat "$z/temp")"; done; } | field thermal_zones; echo ','
    { for d in "$root"/sys/class/hwmon/hwmon*; do
          [ -d "$d" ] || continue
          for f in "$d"/name "$d"/in*_input "$d"/in*_label "$d"/in*_lcrit_alarm "$d"/curr*_input "$d"/curr*_label "$d"/power*_input "$d"/temp*_input "$d"/fan*_input; do
              [ -r "$f" ] && printf '%s/%s=%s\n' "$(basename "$d")" "$(basename "$f")" "$(cat "$f" 2>/dev/null)"
          done
      done; } | field hwmon; echo ','
    { for d in "$root"/sys/bus/usb/devices/*; do
          [ -r "$d/idVendor" ] || continue
          printf '%s %s:%s bMaxPower=%s speed=%s product=%s\n' "$(basename "$d")" "$(cat "$d/idVendor")" "$(cat "$d/idProduct" 2>/dev/null)" \
              "$(cat "$d/bMaxPower" 2>/dev/null)" "$(cat "$d/speed" 2>/dev/null)" "$(cat "$d/product" 2>/dev/null)"
      done; } | field usb_sysfs; echo ','
    { for f in "$root"/sys/bus/usb/devices/*/*/*port*/over_current_count; do [ -r "$f" ] && printf '%s=%s\n' "${f#"$root"/sys/bus/usb/devices/}" "$(cat "$f")"; done; } | field usb_port_over_current_count; echo ','
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
