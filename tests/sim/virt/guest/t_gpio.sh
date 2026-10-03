#!/bin/bash
# Guest test: virtual GPIO (kernel gpio-sim) + real libgpiod v1 tools + real gs/lib/gpio.sh + real gs/button.sh daemon loop.
# Chips: "radxa" = lines PIN_1..PIN_40 (offset = pin-1), "pi" = lines ID_SDA, ID_SCL, GPIO2..GPIO27 (offset = BCM number; real Pi 4 DT names).
. /virt/lib.sh
CFG=/sys/kernel/config/gpio-sim

mod_load gpio-sim || { summary gpio; exit 1; }

# mkchip NAME NUMLINES  then  nameline NAME OFFSET LINENAME ; liveon NAME
mkchip() { mkdir -p "$CFG/$1/bank0" && echo "$2" > "$CFG/$1/bank0/num_lines"; }
nameline() { mkdir -p "$CFG/$1/bank0/line$2" && echo "$3" > "$CFG/$1/bank0/line$2/name"; }
liveon() { echo 1 > "$CFG/$1/live"; }
simdir() { echo "/sys/devices/platform/$(cat "$CFG/$1/dev_name")/$(cat "$CFG/$1/bank0/chip_name")"; }  # per-line dirs: <simdir>/sim_gpio<N>/{pull,value}

mkchip radxa 40
for p in $(seq 1 40); do nameline radxa $((p - 1)) "PIN_$p"; done
mkchip pi 28
nameline pi 0 ID_SDA; nameline pi 1 ID_SCL
for n in $(seq 2 27); do nameline pi "$n" "GPIO$n"; done
liveon radxa; liveon pi
RX="$(cat "$CFG/radxa/bank0/chip_name")"; PI="$(cat "$CFG/pi/bank0/chip_name")"
RXD="$(simdir radxa)"; PID="$(simdir pi)"
info "chips: radxa=$RX ($(cat $CFG/radxa/dev_name)) pi=$PI ($(cat $CFG/pi/dev_name)); libgpiod $(gpiodetect --version 2>&1 | head -1)"
gpiodetect | sed 's/^/VIRT-INFO gpiodetect: /'
eq gpio.sim.lines.radxa 40 "$(gpioinfo "$RX" | grep -c 'PIN_')"
eq gpio.sim.lines.pi 28 "$(gpioinfo "$PI" | grep -c '"')"

# ---- 1. gpiofind by NAME, as the scripts see the kernel ----
eq gpiofind.radxa.PIN_32 "$RX 31" "$(gpiofind PIN_32)"
eq gpiofind.pi.GPIO12 "$PI 12" "$(gpiofind GPIO12)"
if gpiofind PIN_12x >/dev/null 2>&1; then fail gpiofind.unknown "unknown name resolved"; else pass gpiofind.unknown "unknown name -> rc!=0"; fi

# ---- 2. the REAL gs/lib/gpio.sh gpio_find for both board contracts ----
gf() { ( export BOARD="$1"; . /gs/lib/gpio.sh; gpio_find "$2" ) 2>&1; }
eq gpio_find.radxa-zero3.32 "$RX 31" "$(gf radxa-zero3 32)"
eq gpio_find.radxa-zero3.40 "$RX 39" "$(gf radxa-zero3 40)"
eq gpio_find.rpi4.32 "$PI 12" "$(gf rpi4 32)"
eq gpio_find.rpi4.38 "$PI 20" "$(gf rpi4 38)"
eq gpio_find.rpi4.40 "$PI 21" "$(gf rpi4 40)"
eq gpio_find.rpi4.15 "$PI 22" "$(gf rpi4 15)"
eq gpio_find.rpi4.11 "$PI 17" "$(gf rpi4 11)"
eq gpio_find.rpi4.7 "$PI 4" "$(gf rpi4 7)"
out="$(gf rpi4 6)"; case "$out" in *"not a GPIO line"*) pass gpio_find.rpi4.GND "pin 6 rejected: $out" ;; *) fail gpio_find.rpi4.GND "got '$out'" ;; esac
# physical pin 27 -> BCM 0 -> name GPIO0, but the real Pi 4 DT names line 0 "ID_SDA": gpiofind cannot resolve it (finding, see docs)
out="$(gf rpi4 27)"; if [ -z "$out" ]; then pass gpio_find.rpi4.27-unresolvable "pin 27 -> GPIO0 is not a line name on a Pi 4 chip (line 0 = ID_SDA)"; else fail gpio_find.rpi4.27-unresolvable "unexpectedly resolved: '$out'"; fi

# ---- 3. gpioget / gpioset / gpiomon against the simulated pull and drive ----
echo pull-down > "$PID/sim_gpio12/pull"; eq gpioget.pull-down 0 "$(gpioget "$PI" 12)"
echo pull-up > "$PID/sim_gpio12/pull";   eq gpioget.pull-up 1 "$(gpioget "$PI" 12)"
( gpioset -m time -s 2 "$PI" 22=1 & ) ; sleep 0.5
eq gpioset.output-high 1 "$(cat "$PID/sim_gpio22/value")"
sleep 2; eq gpioset.released "0" "$(cat "$PID/sim_gpio22/value")"

# ---- helpers for the button tests ----
# press PULLFILE EXTRA_MS: closing the switch = hold the line high by re-asserting the external pull every 20 ms (the consumer's -B pull-down is
# applied to the SAME simulated pull, so a switch that overpowers it has to be re-asserted); release (pull-down => falling edge) EXTRA_MS after
# button.sh has armed its falling-edge wait (`gpiomon -f`). button.sh measures with /proc/uptime from the end of its first gpiomon to the end of
# the second one, so the process-spawn overhead of the (slow, TCG) guest is part of the measured time; EXTRA_MS is therefore the lower bound.
press() {
	local f="$1" end n=0
	while ! pgrep -f 'gpiomon -f -s' >/dev/null && [ "$n" -lt 400 ]; do echo pull-up > "$f"; sleep 0.02; n=$((n + 1)); done
	end=$(( $(date +%s%3N) + $2 ))
	while [ "$(date +%s%3N)" -lt "$end" ]; do echo pull-up > "$f"; sleep 0.02; done
	echo pull-down > "$f"
}
line_used() { gpioinfo "$1" 2>/dev/null | grep -E "line +$2:" | grep -q '\[used'; }  # $1 chip $2 offset
reset_lines() { for f in "$@"; do echo pull-down > "$f"; done; }

# ---- 4. REAL button_action (function text extracted verbatim from gs/button.sh) with both boards ----
sed -n '/^function button_action()/,/^}/p' /gs/button.sh > /tmp/button_action.sh
run_action() { # BOARD PIN CHIPNAME CHIPDIR OFFSET HOLD_MS -> prints output of button_action
	local board="$1" pin="$2" chip="$3" dir="$4" off="$5" hold="$6"  # hold in ms
	reset_lines "$dir/sim_gpio$off/pull"
	( export BOARD="$board"; . /gs/lib/gpio.sh; . /tmp/button_action.sh; timeout 30 bash -c '. /gs/lib/gpio.sh; . /tmp/button_action.sh; button_action '"$pin" ) > /tmp/ba.out 2>&1 &
	local bp=$!
	wait_for 10 line_used "$chip" "$off" || { echo "NOT-ARMED(gpiomon never requested the line)"; kill $bp 2>/dev/null; return 1; }
	press "$dir/sim_gpio$off/pull" "$hold"
	wait $bp
	cat /tmp/ba.out
}
unset BOARD
eq button_action.radxa-zero3.pin32.short-0.3s-after-arm single "$(BOARD=radxa-zero3 run_action radxa-zero3 32 "$RX" "$RXD" 31 300)"
eq button_action.radxa-zero3.pin32.long-2.5s-after-arm  long   "$(BOARD=radxa-zero3 run_action radxa-zero3 32 "$RX" "$RXD" 31 2500)"
eq button_action.rpi4.pin32.short-0.3s-after-arm single "$(BOARD=rpi4 run_action rpi4 32 "$PI" "$PID" 12 300)"
eq button_action.rpi4.pin32.long-2.5s-after-arm  long   "$(BOARD=rpi4 run_action rpi4 32 "$PI" "$PID" 12 2500)"
# near the 200-centisecond threshold: 1.0 s is still short, 2.3 s is already long
eq button_action.rpi4.pin38.hold-1.0s-after-arm single "$(BOARD=rpi4 run_action rpi4 38 "$PI" "$PID" 20 1000)"
eq button_action.rpi4.pin38.hold-2.3s-after-arm long   "$(BOARD=rpi4 run_action rpi4 38 "$PI" "$PID" 20 2300)"

# ---- 5. the REAL gs/button.sh daemon (unmodified): bounded run, driven presses, effects observed ----
mkdir -p /sys/kernel/debug; mount -t tmpfs none /sys/kernel/debug; mkdir -p /sys/kernel/debug/usb/fcc00000.dwc3
setup_etc
touch /etc/systemd/system/multi-user.target.wants/gs.service
mkdir -p /Videos
cat >> /etc/gs.conf <<'CONF'
# --- virt overrides (appended: last assignment wins) ---
gs_enable='yes'
video_player='pixelpilot'
rec_dir='/Videos'
btn_q2_pin='38'
btn_q2_single_press='toggle_record'
btn_q2_long_press='cleanup_record_files'
btn_q3_pin='40'
btn_q3_single_press=''
btn_q3_long_press='change_otg_mode'
CONF
# daemon_run BOARD CHIP CHIPDIR OFF_Q2 OFF_Q3 OFF_LED
daemon_run() {
	local board="$1" chip="$2" dir="$3" oq2="$4" oq3="$5" oled="$6"
	rm -f /run/pixelpilot.msg /tmp/fifo.out /tmp/led.log /run/record_button.fifo; echo device > /sys/kernel/debug/usb/fcc00000.dwc3/mode
	reset_lines "$dir/sim_gpio$oq2/pull" "$dir/sim_gpio$oq3/pull"
	( export BOARD="$board"; exec setsid timeout -s TERM 60 /gs/button.sh ) > /tmp/button.out 2>&1 &
	local dp=$!
	wait_for 5 test -p /run/record_button.fifo || { fail "daemon.$board.fifo" "button.sh did not create the FIFO"; return 1; }
	( while :; do cat /run/record_button.fifo; done >> /tmp/fifo.out ) & local fp=$!
	( last=x; while :; do v="$(cat "$dir/sim_gpio$oled/value" 2>/dev/null)"; [ "$v" != "$last" ] && { echo "$v" >> /tmp/led.log; last="$v"; }; sleep 0.05; done ) & local lp=$!
	wait_for 10 line_used "$chip" "$oq2"; wait_for 10 line_used "$chip" "$oq3"
	info "daemon($board): armed lines: $(gpioinfo "$chip" | grep -E "line +($oq2|$oq3|$oled):" | tr -s ' ' | tr '\n' ';')"
	press "$dir/sim_gpio$oq2/pull" 300; sleep 1.5                 # q2 short -> toggle_record -> "single" into the FIFO
	press "$dir/sim_gpio$oq2/pull" 2500; sleep 1.5                 # q2 long  -> cleanup_record_files -> pixelpilot.msg
	MSG_Q2="$(cat /run/pixelpilot.msg 2>/dev/null)"
	press "$dir/sim_gpio$oq3/pull" 2500; sleep 4.5                 # q3 long  -> change_otg_mode (device->host, LED on 1 s on Radxa)
	info "daemon($board) button.sh output: $(tr '\n' '|' < /tmp/button.out | cut -c1-300); led samples: $(tr '\n' ',' < /tmp/led.log); msg: $(tr '\n' '|' < /run/pixelpilot.msg 2>/dev/null)"
	DAEMON_PID=$dp; FIFO_PID=$fp; LED_PID=$lp
	return 0
}
daemon_stop() {
	kill -TERM "-$DAEMON_PID" 2>/dev/null; kill "$DAEMON_PID" "$FIFO_PID" "$LED_PID" 2>/dev/null
	pkill -f /gs/button.sh 2>/dev/null; pkill gpiomon 2>/dev/null; sleep 0.5
}

daemon_run radxa-zero3 "$RX" "$RXD" 37 39 14
eq daemon.radxa-zero3.q2-short.fifo single "$(tr -d '\n' < /tmp/fifo.out 2>/dev/null)"
eq daemon.radxa-zero3.q2-long.msg "no record file found!" "$MSG_Q2"
eq daemon.radxa-zero3.q3-long.msg "change otg mode to host!" "$(grep -m1 'otg mode' /run/pixelpilot.msg 2>/dev/null)"
eq daemon.radxa-zero3.q3-long.modefile host "$(cat /sys/kernel/debug/usb/fcc00000.dwc3/mode)"
eq daemon.radxa-zero3.led-pin15-toggle "0,1,0" "$(tr '\n' ',' < /tmp/led.log | sed 's/,$//')"
daemon_stop
check daemon.radxa-zero3.stopped "button.sh / gpiomon still running" test -z "$(pgrep -f /gs/button.sh; pgrep gpiomon)"

daemon_run rpi4 "$PI" "$PID" 20 21 22
eq daemon.rpi4.q2-short.fifo single "$(tr -d '\n' < /tmp/fifo.out 2>/dev/null)"
eq daemon.rpi4.q2-long.msg "no record file found!" "$MSG_Q2"
# on rpi4 OTG_CONTROLLER='none' (no runtime role switch): the long press on q3 reports it (the guard in button.sh change_otg_mode; before it
# the script ran `cat none` and printed "otg mode is unkonw") and never drives the LED
eq daemon.rpi4.q3-long.msg "otg mode switch is not supported on this board" "$(grep -ho "otg mode switch is not supported on this board" /tmp/button.out | head -1)"
eq daemon.rpi4.led-untouched "0" "$(tr '\n' ',' < /tmp/led.log | sed 's/,$//')"
daemon_stop
check daemon.rpi4.stopped "button.sh / gpiomon still running" test -z "$(pgrep -f /gs/button.sh; pgrep gpiomon)"

# ---- 6. LED write by physical pin on Pi, same call shape as button.sh/gs.sh (gpioset -D <drive> -m time -s N <line>=1) ----
LEDINFO="$(gf rpi4 15)"
( gpioset -D push-pull -m time -s 2 $LEDINFO=1 & ); sleep 0.6
eq led.rpi4.pin15.on 1 "$(cat "$PID/sim_gpio22/value")"
sleep 2.2; eq led.rpi4.pin15.off 0 "$(cat "$PID/sim_gpio22/value")"

# ---- 7. Pi 5: the RP1 chip is found by its LABEL (pinctrl-rp1), never by a chip number ----
# The Pi 4 / Radxa chips are removed first: gpiofind matches line NAMES over all chips, and the Pi 5 names (GPIO<n>) are the Pi 4 ones.
rmchip() { echo 0 > "$CFG/$1/live"; rmdir "$CFG/$1"/bank0/line* 2>/dev/null; rmdir "$CFG/$1/bank0" "$CFG/$1" 2>/dev/null; }
rmchip radxa; rmchip pi
# two other chips first, so that the RP1 chip is neither gpiochip0 nor a fixed number; labels as set by the kernel drivers are not known here
mkchip dummy_a 8; echo dummy-a > "$CFG/dummy_a/bank0/label"; for n in $(seq 0 7); do nameline dummy_a "$n" "DUMMY_A_$n"; done
mkchip dummy_b 4; echo other-b > "$CFG/dummy_b/bank0/label"; for n in 0 1 2 3; do nameline dummy_b "$n" "DUMMY_B_$n"; done
mkchip rp1 28; echo pinctrl-rp1 > "$CFG/rp1/bank0/label"
nameline rp1 0 ID_SDA; nameline rp1 1 ID_SCL; for n in $(seq 2 27); do nameline rp1 "$n" "GPIO$n"; done
liveon dummy_a; liveon dummy_b; liveon rp1
RP1="$(cat "$CFG/rp1/bank0/chip_name")"; RP1D="$(simdir rp1)"
gpiodetect | sed 's/^/VIRT-INFO pi5 gpiodetect: /'
check gpio.pi5.label-visible "gpiodetect does not list the label pinctrl-rp1" bash -c 'gpiodetect | grep -q "\[pinctrl-rp1\]"'
BYLABEL="$(gpiodetect | awk '/\[pinctrl-rp1\]/ { print $1 }')"
eq gpio.pi5.chip-found-by-label "$RP1" "$BYLABEL"
check gpio.pi5.chip-number-is-not-fixed "RP1 chip is gpiochip0: the test cannot tell a label lookup from a number" test "$RP1" != gpiochip0
eq gpio.pi5.board-label-key "pinctrl-rp1" "$(BOARD=rpi5 bash -c '. /gs/lib/board.sh; board_get GPIO_CHIP_LABEL' 2>&1)"
eq gpio.pi5.lines "28" "$(gpioinfo "$RP1" | grep -c '"')"
eq gpio_find.rpi5.7  "$RP1 4"  "$(gf rpi5 7)"
eq gpio_find.rpi5.32 "$RP1 12" "$(gf rpi5 32)"
eq gpio_find.rpi5.40 "$RP1 21" "$(gf rpi5 40)"
eq gpio_find.rpi5.15 "$RP1 22" "$(gf rpi5 15)"
eq gpio_find.rpi5.11 "$RP1 17" "$(gf rpi5 11)"
for p in 6 27 28; do out="$(gf rpi5 "$p")"; case "$out" in *"not a GPIO line"*) pass gpio_find.rpi5.pin$p-refused "$out" ;; *) fail gpio_find.rpi5.pin$p-refused "got '$out'" ;; esac; done
eq button_action.rpi5.pin32.short-0.3s-after-arm single "$(BOARD=rpi5 run_action rpi5 32 "$RP1" "$RP1D" 12 300)"
eq button_action.rpi5.pin32.long-2.5s-after-arm  long   "$(BOARD=rpi5 run_action rpi5 32 "$RP1" "$RP1D" 12 2500)"
summary gpio
