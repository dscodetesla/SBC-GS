#!/usr/bin/env bash
# Static check of the decoder/sink choice of bench/video-rx.sh (Pi V4L2 -> host VA-API -> software), with shimmed gst tools
# and a fake DRM render-node directory. DRY_RUN=1 makes the script print its pipeline instead of running it.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
want() { if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi; }
mkdir -p "$T/bin" "$T/dri-yes" "$T/dri-no"; : > "$T/dri-yes/renderD128"
cat > "$T/bin/gst-inspect-1.0" <<'SH'
#!/bin/sh
case " $GST_ELEMENTS " in *" $1 "*) exit 0 ;; esac
exit 1
SH
printf '#!/bin/sh\nexit 0\n' > "$T/bin/gst-launch-1.0"; chmod +x "$T/bin/"*
pipe() {  # pipe <codec> <elements> <dri dir> [extra env...] -> decoder element of the printed pipeline, or RC<n>
	local codec="$1" els="$2" dri="$3"; shift 3
	local out rc
	out="$(env -i PATH="$T/bin:/usr/bin:/bin" HOME="$T" DRY_RUN=1 VIDEO_CODEC="$codec" GST_ELEMENTS="$els" DRI_DIR="$dri" "$@" bash "$REPO/bench/video-rx.sh" 2>/dev/null)"; rc=$?
	[ "$rc" = 0 ] || { echo "RC$rc"; return; }
	printf '%s\n' "$out" | sed -n 's/^pipeline: .*parse ! \([^ ]*\) !.*/\1/p'
}
sinkof() { env -i PATH="$T/bin:/usr/bin:/bin" HOME="$T" DRY_RUN=1 VIDEO_CODEC=h265 GST_ELEMENTS="" DRI_DIR="$T/dri-no" "$@" bash "$REPO/bench/video-rx.sh" 2>/dev/null | sed -n 's/^pipeline: .*videoconvert ! \(.*\)$/\1/p'; }
want "Pi: V4L2 stateless HEVC is preferred" v4l2slh265dec "$(pipe h265 'v4l2slh265dec vah265dec avdec_h265' "$T/dri-yes")"
want "Pi: V4L2 H.264" v4l2h264dec "$(pipe h264 'v4l2h264dec vah264dec avdec_h264' "$T/dri-yes")"
want "host with GPU: VA-API HEVC" vah265dec "$(pipe h265 'vah265dec avdec_h265' "$T/dri-yes")"
want "host with GPU: VA-API H.264" vah264dec "$(pipe h264 'vah264dec avdec_h264' "$T/dri-yes")"
want "va element but no render node: software" avdec_h265 "$(pipe h265 'vah265dec avdec_h265' "$T/dri-no")"
want "render node but no va element: software" avdec_h265 "$(pipe h265 'avdec_h265' "$T/dri-yes")"
want "HOST_VAAPI=no: software" avdec_h265 "$(pipe h265 'vah265dec avdec_h265' "$T/dri-yes" HOST_VAAPI=no)"
want "HOST_VAAPI=yes without the element fails" RC1 "$(pipe h265 'avdec_h265' "$T/dri-yes" HOST_VAAPI=yes)"
want "HOST_VAAPI=yes with element and node" vah265dec "$(pipe h265 'vah265dec avdec_h265' "$T/dri-yes" HOST_VAAPI=yes)"
want "explicit DECODER wins" avdec_h265 "$(pipe h265 'vah265dec avdec_h265' "$T/dri-yes" DECODER=avdec_h265)"
want "bad HOST_VAAPI value is rejected by the registry (rc 2)" RC2 "$(pipe h265 'avdec_h265' "$T/dri-yes" HOST_VAAPI=maybe)"
want "Wayland session: autovideosink" "autovideosink sync=false" "$(sinkof WAYLAND_DISPLAY=wayland-0)"
want "no display (console): kmssink" "kmssink sync=false" "$(sinkof)"
echo "bad=$bad"
exit "$bad"
