#!/usr/bin/env bash
# Write /etc/wifibroadcast.cfg for the role and enable the wfb-ng service.
# Requires WFB_REGION in ./env. Usage: sudo ./configure-wfb.sh <air|gs>
. "$(dirname "$0")/lib.sh"
role_check "${1:-}"; need_root "$@"
[ -n "$WFB_REGION" ] || die "set WFB_REGION in $BENCH_DIR/env (regulatory domain you may legally use)"

cfg="$(cfgpath /etc/wifibroadcast.cfg)"
mkdir -p "$(dirname "$cfg")"
[ -f "$cfg" ] && cp -a "$cfg" "$cfg.bak.$(date +%s)"

{
	cat <<EOT
[common]
wifi_channel = ${WFB_CHANNEL}
wifi_region = '${WFB_REGION}'

EOT
	if [ "$1" = gs ]; then
		dest="${GS_FORWARD_IP:-127.0.0.1}"
		cat <<EOT
[gs_mavlink]
peer = 'connect://${dest}:${MAV_PORT}'

[gs_video]
peer = 'connect://${dest}:${GS_VIDEO_PORT}'
EOT
	else
		if [ -n "$FC_SERIAL" ]; then
			mav_peer="serial:${FC_SERIAL}:${FC_BAUD}"   # form from wfb-ng Setup-HOWTO; wfb-ng will not start if the device is absent
		else
			mav_peer="listen://0.0.0.0:${MAV_PORT}"
		fi
		cat <<EOT
[drone_mavlink]
peer = '${mav_peer}'

[drone_video]
peer = 'listen://0.0.0.0:${AIR_VIDEO_PORT}'
EOT
	fi
} > "$cfg"
log "wrote $cfg"

if [ -n "$WFB_NICS" ]; then
	mkdir -p "$(dirname "$(cfgpath /etc/default/wifibroadcast)")"
	echo "WFB_NICS=\"$WFB_NICS\"" > "$(cfgpath /etc/default/wifibroadcast)"
	log "WFB_NICS=\"$WFB_NICS\""
fi

svc="wifibroadcast@$([ "$1" = gs ] && echo gs || echo drone)"
run systemctl enable "$svc"
run systemctl restart "$svc"
log "service $svc enabled. monitor: $([ "$1" = gs ] && echo 'wfb-cli gs' || echo "journalctl -u $svc -f")"
