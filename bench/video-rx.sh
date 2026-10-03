#!/usr/bin/env bash
# GS: UDP/RTP -> decode -> display. Mirrors the pipeline in gs/stream.sh with the
# Rockchip decoder swapped for a Pi decoder. Decoder/sink are auto-picked unless
# DECODER / SINK are set. Optional env: PROGRESS=1 (print flow), LISTEN_PORT.
. "$(dirname "$0")/lib.sh"

command -v gst-launch-1.0 >/dev/null || die "gst-launch-1.0 not found (run setup-common.sh)"

: "${LISTEN_PORT:=$GS_VIDEO_PORT}"

case "$VIDEO_CODEC" in
	h264) depay="rtph264depay"; parse="h264parse"; enc_name="H264"
	      v4l2_dec="v4l2h264dec"; va_dec="vah264dec"; sw_dec="avdec_h264" ;;
	h265) depay="rtph265depay"; parse="h265parse"; enc_name="H265"
	      v4l2_dec="v4l2slh265dec"; va_dec="vah265dec"; sw_dec="avdec_h265" ;;
	*) die "VIDEO_CODEC must be h264 or h265" ;;
esac

# VA-API (gst-plugins-bad `va` plugin, GStreamer >= 1.22; Ubuntu 26.04 has 1.28.2) decodes on the host GPU when a DRM render node exists.
# HOST_VAAPI: auto (use it if the element and a render node exist) | yes (require it) | no (never).
have_render_node() { compgen -G "${DRI_DIR}/renderD*" >/dev/null; }

if [ -z "$DECODER" ]; then
	# order: V4L2 stateless (Pi) -> VA-API (host GPU) -> software. v4l2 decoders are present as GStreamer elements even when no
	# matching kernel device exists; if playback fails, retry with DECODER=$va_dec or DECODER=$sw_dec.
	if have_gst_element "$v4l2_dec"; then DECODER="$v4l2_dec"
	elif [ "$HOST_VAAPI" != no ] && have_gst_element "$va_dec" && have_render_node; then DECODER="$va_dec"
	elif [ "$HOST_VAAPI" = yes ]; then die "HOST_VAAPI=yes but $va_dec or a render node in $DRI_DIR is missing (gstreamer1.0-plugins-bad + the GPU's VA driver, e.g. intel-media-va-driver or mesa-va-drivers)"
	else DECODER="$sw_dec"; fi
fi

if [ -z "$SINK" ]; then
	if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then SINK="autovideosink sync=false"
	else SINK="kmssink sync=false"; fi
fi

prog=""
[ "$PROGRESS" = "1" ] && prog="! progressreport update-freq=1"

log "udp:${LISTEN_PORT} ${enc_name} -> ${DECODER} -> ${SINK}"
# DRY_RUN=1: print the pipeline and stop (tests/static/host-decode.sh)
[ "$DRY_RUN" = 1 ] && { echo "pipeline: udpsrc port=$LISTEN_PORT ! $depay ! $parse ! $DECODER ! videoconvert ! $SINK"; exit 0; }
# shellcheck disable=SC2086
exec gst-launch-1.0 -e udpsrc port="$LISTEN_PORT" \
	caps="application/x-rtp,media=(string)video,clock-rate=(int)90000,encoding-name=(string)${enc_name}" \
	! "$depay" ! "$parse" ! $DECODER ! videoconvert $prog ! $SINK
