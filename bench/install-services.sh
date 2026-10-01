#!/usr/bin/env bash
# Install systemd units so the bench starts on boot.
#   air: bench-video-src.service + bench-fc.service   (After=wifibroadcast@drone)
#   gs : bench-video-rx.service                        (After=wifibroadcast@gs)
# The GS MAVLink client (gs_mav.py) is interactive and is started by hand.
# Usage: sudo ./install-services.sh <air|gs>
. "$(dirname "$0")/lib.sh"
role_check "${1:-}"; need_root "$@"

unit_dir="$(cfgpath /etc/systemd/system)"; mkdir -p "$unit_dir"

mk_unit() {  # name description after exec
	cat > "$unit_dir/$1.service" <<EOT
[Unit]
Description=$2
After=network.target $3
Wants=$3

[Service]
Type=simple
WorkingDirectory=$BENCH_DIR
ExecStart=$4
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOT
	log "wrote $unit_dir/$1.service"
}

if [ "$1" = air ]; then
	mk_unit bench-video-src "Bench: synthetic video source" wifibroadcast@drone.service "$BENCH_DIR/video-src.sh"
	units="bench-video-src"
	if [ -z "$FC_SERIAL" ]; then
		mk_unit bench-fc "Bench: MAVLink flight-controller emulator" wifibroadcast@drone.service "$VENV/bin/python $BENCH_DIR/fake_fc.py --sysid $FC_SYSID --conn udpout:127.0.0.1:$MAV_PORT"
		units="$units bench-fc"
	else
		log "FC_SERIAL=$FC_SERIAL: real FC is wired through wfb-ng; fake_fc service not installed"
	fi
else
	if [ -n "$GS_FORWARD_IP" ]; then
		log "GS_FORWARD_IP=$GS_FORWARD_IP: the host decodes; no local video service"
		units=""
	else
		mk_unit bench-video-rx "Bench: GS video receiver" wifibroadcast@gs.service "$BENCH_DIR/video-rx.sh"
		units="bench-video-rx"
	fi
fi
run systemctl daemon-reload
for u in $units; do run systemctl enable "$u"; done
[ -n "$units" ] && log "enabled: $units  (start now: sudo systemctl start $units)"
true
