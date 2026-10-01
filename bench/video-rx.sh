#!/usr/bin/env bash
# GS: UDP/RTP -> decode -> display. Mirrors the pipeline in gs/stream.sh with the
# Rockchip decoder swapped for a Pi decoder. Decoder/sink are auto-picked unless
# DECODER / SINK are set. Optional env: PROGRESS=1 (print flow), LISTEN_PORT.
. "$(dirname "$0")/lib.sh"

command -v gst-launch-1.0 >/dev/null || die "gst-launch-1.0 not found (run setup-common.sh)"

: "${LISTEN_PORT:=$GS_VIDEO_PORT}"
: "${PROGRESS:=0}"

case "$VIDEO_CODEC" in
	h264) depay="rtph264depay"; parse="h264parse"; enc_name="H264"
	      v4l2_dec="v4l2h264dec"; sw_dec="avdec_h264" ;;
	h265) depay="rtph265depay"; parse="h265parse"; enc_name="H265"
	      v4l2_dec="v4l2slh265dec"; sw_dec="avdec_h265" ;;
	*) die "VIDEO_CODEC must be h264 or h265" ;;
esac

if [ -z "$DECODER" ]; then
	if have_gst_element "$v4l2_dec"; then DECODER="$v4l2_dec"; else DECODER="$sw_dec"; fi
	# v4l2 decoders are present as GStreamer elements even when no matching
	# kernel device exists; if playback fails, retry with DECODER=$sw_dec.
fi

if [ -z "$SINK" ]; then
	if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then SINK="autovideosink sync=false"
	else SINK="kmssink sync=false"; fi
fi

prog=""
[ "$PROGRESS" = "1" ] && prog="! progressreport update-freq=1"

log "udp:${LISTEN_PORT} ${enc_name} -> ${DECODER} -> ${SINK}"
# shellcheck disable=SC2086
exec gst-launch-1.0 -e udpsrc port="$LISTEN_PORT" \
	caps="application/x-rtp,media=(string)video,clock-rate=(int)90000,encoding-name=(string)${enc_name}" \
	! "$depay" ! "$parse" ! $DECODER ! videoconvert $prog ! $SINK
