#!/usr/bin/python3
"""Software end-to-end latency of the video chain on ONE machine (no radio, no GPU).

  videotestsrc -> x264enc|x265enc -> rtp pay -> udpsink 127.0.0.1:PORT
  udpsrc PORT -> rtp depay -> parse -> avdec -> appsink

Frame N is stamped (monotonic clock) when it enters the encoder and again when the
N-th decoded frame leaves the decoder; latency = difference. Needs the system
python3 with python3-gi + gir1.2-gstreamer-1.0 (the pymavlink venv has no `gi`).
Output (last line, machine readable):  LATENCY codec=h264 frames=N lost=L min=.. p50=.. p95=.. max=.. ms
Exit 0 = frames flowed and p95 <= --max-p95-ms (default 500: a regression guard, NOT a Pi figure).

What this proves: pipeline wiring, caps, RTP packetisation, zerolatency encoder settings.
What it cannot prove: Pi hardware decoders (v4l2*), kmssink/vsync, radio/FEC/jitter delay, real camera.
"""
import argparse
import statistics
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simcfg  # noqa: E402

CFG = simcfg.load(["sim"])   # SIM_LAT_* keys, docs/CONFIG.md

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

ENC = {
    "h264": ("x264enc tune=zerolatency speed-preset=ultrafast bitrate={kbps} key-int-max={fps} ! h264parse"
             " ! rtph264pay config-interval=1 pt=96 mtu=1400",
             "rtph264depay ! h264parse ! avdec_h264", "H264"),
    "h265": ("x265enc tune=zerolatency speed-preset=ultrafast bitrate={kbps} key-int-max={fps} ! h265parse"
             " ! rtph265pay config-interval=1 pt=96 mtu=1400",
             "rtph265depay ! h265parse ! avdec_h265", "H265"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--codec", choices=sorted(ENC), default="h264")
    ap.add_argument("--port", type=int, default=CFG["SIM_LAT_PORT"])
    ap.add_argument("--frames", type=int, default=CFG["SIM_LAT_FRAMES"])
    ap.add_argument("--fps", type=int, default=CFG["SIM_LAT_FPS"])
    ap.add_argument("--size", default=CFG["SIM_LAT_SIZE"])
    ap.add_argument("--max-p95-ms", type=float, default=CFG["SIM_LAT_MAX_P95_MS"])
    ap.add_argument("--max-lost", type=int, default=CFG["SIM_LAT_MAX_LOST"], help="tolerated missing frames at the tail (encoder flush)")
    a = ap.parse_args()
    w, h = a.size.split("x")
    Gst.init(None)
    enc, dec, name = ENC[a.codec]
    enc = enc.format(kbps=CFG["SIM_LAT_BITRATE_KBPS"], fps=a.fps)
    tx = Gst.parse_launch(
        f"videotestsrc is-live=true num-buffers={a.frames} pattern=ball ! video/x-raw,width={w},height={h},"
        f"framerate={a.fps}/1 ! videoconvert name=stamp_in ! {enc} ! udpsink host=127.0.0.1 port={a.port} sync=false")
    rx = Gst.parse_launch(
        f"udpsrc port={a.port} caps=\"application/x-rtp,media=(string)video,clock-rate=(int)90000,"
        f"encoding-name=(string){name},payload=(int)96\" ! {dec} ! videoconvert "
        f"! appsink name=out emit-signals=true sync=false max-buffers=0 drop=false")
    t_in, t_out = [], []

    def probe(_pad, _info):
        t_in.append(time.monotonic())
        return Gst.PadProbeReturn.OK

    tx.get_by_name("stamp_in").get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, probe)

    def on_sample(sink):
        sink.emit("pull-sample")
        t_out.append(time.monotonic())
        return Gst.FlowReturn.OK

    rx.get_by_name("out").connect("new-sample", on_sample)
    rx.set_state(Gst.State.PLAYING)
    time.sleep(0.5)  # receiver must listen before the first packet leaves
    tx.set_state(Gst.State.PLAYING)
    bus = tx.get_bus()
    bus.timed_pop_filtered(int((a.frames / a.fps + 15) * Gst.SECOND), Gst.MessageType.EOS | Gst.MessageType.ERROR)
    deadline = time.monotonic() + 3.0
    while len(t_out) < len(t_in) - a.max_lost and time.monotonic() < deadline:
        time.sleep(0.05)
    tx.set_state(Gst.State.NULL)
    rx.set_state(Gst.State.NULL)

    n = min(len(t_in), len(t_out))
    if n < a.frames // 2:
        print(f"FAIL: only {len(t_out)} of {len(t_in)} frames decoded", file=sys.stderr)
        return 1
    lat = sorted((t_out[i] - t_in[i]) * 1000 for i in range(n))
    lost = len(t_in) - len(t_out)
    p = lambda q: lat[min(n - 1, int(q * n))]  # noqa: E731
    print(f"LATENCY codec={a.codec} frames={len(t_out)}/{len(t_in)} lost={lost} "
          f"min={lat[0]:.0f} p50={statistics.median(lat):.0f} p95={p(0.95):.0f} max={lat[-1]:.0f} ms")
    if lost > a.max_lost:
        print(f"FAIL: lost {lost} > {a.max_lost}", file=sys.stderr)
        return 1
    if p(0.95) > a.max_p95_ms:
        print(f"FAIL: p95 {p(0.95):.0f} ms > {a.max_p95_ms:.0f} ms", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
