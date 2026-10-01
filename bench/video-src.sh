#!/usr/bin/env bash
# Air node: synthetic video -> RTP -> UDP (default 127.0.0.1:5602 = wfb-ng drone_video).
# Overlays a running time stamp so latency can be read off the GS screen.
# Optional env: SINK_HOST, SINK_PORT, NUM_BUFFERS (0 = endless).
. "$(dirname "$0")/lib.sh"

command -v gst-launch-1.0 >/dev/null || die "gst-launch-1.0 not found (run setup-common.sh)"

: "${SINK_HOST:=127.0.0.1}"
: "${SINK_PORT:=$AIR_VIDEO_PORT}"
: "${NUM_BUFFERS:=0}"
# gst: num-buffers=-1 is endless, 0 would mean "no frames"
[ "$NUM_BUFFERS" -le 0 ] && NUM_BUFFERS=-1

raw="videotestsrc is-live=true pattern=${VIDEO_PATTERN} num-buffers=${NUM_BUFFERS} \
 ! video/x-raw,width=${VIDEO_W},height=${VIDEO_H},framerate=${VIDEO_FPS}/1 \
 ! videoconvert"
if have_gst_element timeoverlay; then
	raw="${raw} ! timeoverlay halignment=left valignment=top ! videoconvert"
else
	warn "timeoverlay missing (install gstreamer1.0-x): no time stamp on video"
fi

case "$VIDEO_CODEC" in
	h264)
		enc="x264enc tune=zerolatency speed-preset=ultrafast bitrate=${VIDEO_BITRATE_KBPS} key-int-max=${VIDEO_FPS} \
 ! video/x-h264,profile=baseline ! h264parse config-interval=-1 \
 ! rtph264pay config-interval=1 pt=96 mtu=1400" ;;
	h265)
		warn "x265 software encoding is heavy on a Pi 4; lower resolution/fps if frames drop"
		enc="x265enc tune=zerolatency speed-preset=ultrafast bitrate=${VIDEO_BITRATE_KBPS} key-int-max=${VIDEO_FPS} \
 ! h265parse config-interval=-1 \
 ! rtph265pay config-interval=1 pt=96 mtu=1400" ;;
	*) die "VIDEO_CODEC must be h264 or h265" ;;
esac

log "video ${VIDEO_CODEC} ${VIDEO_W}x${VIDEO_H}@${VIDEO_FPS} ${VIDEO_BITRATE_KBPS}kbps -> udp://${SINK_HOST}:${SINK_PORT}"
# shellcheck disable=SC2086
exec gst-launch-1.0 -e $raw ! $enc ! udpsink host="$SINK_HOST" port="$SINK_PORT" sync=false
