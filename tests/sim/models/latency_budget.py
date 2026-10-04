#!/usr/bin/env python3
"""Glass-to-glass video latency budget for OpenIPC air -> wfb-ng -> Raspberry Pi GS (min / typ / max per term).

A MODEL, not a proof: most hardware terms are UNMEASURED placeholders with an INF range (see params.json,
sections.latency). Its job is to say which term dominates and which configuration is worth measuring
first. It cannot certify a latency; the bench protocol (`protocol` subcommand, docs/SIM-MODELS.md) does.

  glass-to-glass = capture + encode + radio airtime + FEC block wait + USB/rx + network + jitter buffer
                   + decode + display (vblank wait + scan-out + panel + queued frames)

Terms:
  capture, encode   frames x frame period                              (UNMEASURED)
  radio             packets/frame x (airtime + access), FEC parity interleaved: min = data only,
                    typ = half the parity ahead of the frame, max = I-frame x n/k      (formulas: rf_model.py)
  fec_wait          0 without loss (wfb-ng sends data fragments at once, tx.cpp send_block_fragment before
                    the parity); --lossy: recovery waits for k fragments + parity (typ half, max full block)
  decode            HW: proc_ms x pixels/1080p + (reorder + queue) frames; SW (Pi 5 H.264): MEASURED(sim)
                    x264/avdec time scaled by decode share, pixel ratio and CPU ratio
  display           vblank wait + scan-out + panel + queued frames

  latency_budget.py budget --board pi4 --codec h265 --fps 30 [--res 1920x1080 --bitrate 8000 --mcs 1 --fec 8/12 --lossy]
  latency_budget.py matrix            # every supported board/codec x fps 30,60
  latency_budget.py protocol          # bench steps for the real glass-to-glass measurement
  Add --measured-g2g-ms X to compare with a bench number; --set key=val / MODEL_MEASURED override parameters.
"""
import argparse
import math
import sys

import common
import rf_model

DECODERS = {("pi4", "h264"): "pi4_h264_stateful", ("pi4", "h265"): "pi4_h265_stateless",
            ("pi5", "h265"): "pi5_h265_stateless", ("pi5", "h264"): "pi5_h264_sw"}
TERMS = ("capture", "encode", "radio", "fec_wait", "usb_rx", "network", "jitter_buffer", "decode", "display")

PROTOCOL = """Glass-to-glass measurement protocol (bench, Monday) - HW, nothing here was run
Fix first: codec, resolution, fps, bitrate, MCS, FEC k/n, decoder element, sink (kmssink|waylandsink), display mode (modetest -M vc4 -c).
A. LED + photodiode (preferred, resolution ~1 ms)
 1. Put an LED (driven by a GPIO of any board or a function generator, 1-2 Hz square wave) in front of the AIR camera lens, bright enough to saturate the image.
 2. Show the GS output full screen; stick a photodiode (or a phototransistor + 10 kOhm) on the screen in the area where the LED image appears.
 3. Scope / logic analyser (>= 10 kS/s): channel 1 = LED drive, channel 2 = photodiode threshold output. Delay = edge(ch2) - edge(ch1).
 4. Record >= 100 edges per configuration; report p50, p95, max (not the mean). Repeat 3 times, power-cycle between runs.
 5. Display scan-out: repeat with the photodiode at the top and at the bottom of the screen; the difference is the scan-out time.
B. Phone slow-motion with an on-screen counter (no extra hardware, resolution = 1 frame of the phone: 4 ms at 240 fps)
 1. On the host show a millisecond counter (any stopwatch page) and point the AIR camera at it.
 2. Film with the phone, at 240 fps, the counter screen AND the GS screen in one shot.
 3. Per video frame of the phone read both counters; latency = counter(source) - counter(GS). Take >= 50 samples; report p50/p95/max.
 4. Subtract half of the counter refresh period as the reading error; note it in RESULTS.
C. Software-only baseline (separates the Pi decoder from the rest)
 1. bench/video-src.sh with SOURCE=test (timeoverlay) sent straight to bench/video-rx.sh on the same network, no radio: the glass-to-glass of the software path.
 2. python3 tests/sim/video_latency.py --codec h264|h265 on the same Pi (system python3 with python3-gi) for the in-process encode+decode time.
D. Per term (fits the model parameters)
 1. wfb-cli gs: loss/recovered counters during the run; the run only counts if residual loss is ~0 (otherwise FEC wait pollutes the number).
 2. GST_DEBUG=GST_TRACER:7 GST_TRACERS='latency(flags=element)' on the GS pipeline for the decoder element time (UNVERIFIED that this tracer reports v4l2 decoders per element).
 3. python3 tests/sim/models/latency_budget.py budget ... --measured-g2g-ms <p50> prints the residual against the model; write the fitted terms to measured.json and run with MODEL_MEASURED=measured.json.
Record: date, kernel (uname -r), GStreamer version, pipeline string, config, p50/p95/max, number of samples."""


def frame_period_ms(fps):
    return 1000.0 / fps


def decode_ms(P, board, codec, w, h, fps):
    """(min, typ, max) decode term in ms."""
    did = DECODERS[(board, codec)]
    pix = w * h / P.get("latency.pixel_ref")
    T = frame_period_ms(fps)
    rmin, rtyp, rmax = P.rng("latency.decoders.%s.reorder_frames" % did)
    qmin, qtyp, qmax = P.rng("latency.decoders.%s.queue_frames" % did)
    if board == "pi5" and codec == "h264":
        ref = "latency.sw_ref_h264_ms"
        rmn, rt, rmx = P.rng(ref)
        scale_px = w * h / P.get("latency.sw_ref_pixels")
        fmn, ft, fmx = P.rng("latency.sw_decode_fraction")
        cmn, ct, cmx = P.rng("latency.cpu_scale_vs_x86")
        proc = (rmn * fmn * scale_px * cmn, rt * ft * scale_px * ct, rmx * fmx * scale_px * cmx)
    else:
        pmin, ptyp, pmax = P.rng("latency.decoders.%s.proc_ms_1080p" % did)
        proc = (pmin * pix, ptyp * pix, pmax * pix)
    return (proc[0] + (rmin + qmin) * T, proc[1] + (rtyp + qtyp) * T, proc[2] + (rmax + qmax) * T)


def radio_terms(P, mcs, k, n, fps, bitrate_kbps, lossy):
    air_us = rf_model.frame_airtime_us(P, mcs) + P.get("rf.mac_access_us")
    payload = P.get("rf.payload_bytes")
    avg_bytes = bitrate_kbps * 1000 / 8 / fps
    ppf = max(1, math.ceil(avg_bytes / payload))
    ppf_i = max(1, math.ceil(avg_bytes * P.get("video.iframe_ratio") / payload))
    t = air_us / 1000.0
    parity_per_pkt = (n - k) / k
    radio = (ppf * t, ppf * t * (1 + parity_per_pkt / 2), ppf_i * t * n / k)
    pps = bitrate_kbps * 1000 / 8 / payload
    interval_ms = 1000.0 / pps
    full = (k - 1) * interval_ms + (n - k) * t
    fec = (0.0, full / 2, full) if lossy else (0.0, 0.0, 0.0)
    util = pps * (n / k) * air_us / 1e6
    return radio, fec, util, ppf


def display_ms(P, fps_unused=None):
    T = 1000.0 / P.get("latency.display_hz")
    vmn, vt, vmx = P.rng("latency.vsync_wait_frac")
    smn, st, smx = P.rng("latency.scanout_frac")
    pmn, pt, pmx = P.rng("latency.panel_ms")
    qmn, qt, qmx = P.rng("latency.display_queue_frames")
    return ((vmn + smn + qmn) * T + pmn, (vt + st + qt) * T + pt, (vmx + smx + qmx) * T + pmx)


def budget(P, board, codec, fps, w, h, bitrate_kbps, mcs, k, n, lossy=False):
    if (board, codec) not in DECODERS:
        raise common.ParamError("no decoder entry for %s/%s" % (board, codec))
    T = frame_period_ms(fps)
    terms = {}
    cm = P.rng("latency.capture_frames")
    em = P.rng("latency.encode_frames")
    terms["capture"] = tuple(x * T for x in cm)
    terms["encode"] = tuple(x * T for x in em)
    radio, fec, util, ppf = radio_terms(P, mcs, k, n, fps, bitrate_kbps, lossy)
    terms["radio"], terms["fec_wait"] = radio, fec
    terms["usb_rx"] = P.rng("latency.usb_rx_ms")
    terms["network"] = P.rng("latency.network_ms")
    terms["jitter_buffer"] = P.rng("latency.jitterbuffer_ms")
    terms["decode"] = decode_ms(P, board, codec, w, h, fps)
    terms["display"] = display_ms(P)
    tot = tuple(sum(terms[t][i] for t in TERMS) for i in range(3))
    dom_typ = max(TERMS, key=lambda t: terms[t][1])
    dom_max = max(TERMS, key=lambda t: terms[t][2])
    return {"terms": terms, "total": tot, "dominant_typ": dom_typ, "dominant_max": dom_max, "util": util,
            "ppf": ppf, "feasible": util <= 1.0, "decoder": DECODERS[(board, codec)]}


def fmt_budget(P, b, board, codec, fps, w, h, bitrate, mcs, k, n, lossy, measured=None):
    out = ["# latency_budget: board=%s codec=%s decoder=%s %dx%d@%dfps %dkbps mcs=%d fec=%d/%d lossy=%d airtime_util=%.2f%s"
           % (board, codec, b["decoder"], w, h, fps, bitrate, mcs, k, n, int(lossy), b["util"],
              "" if b["feasible"] else " INFEASIBLE(link capacity exceeded: radio term unbounded)"),
           "term,min_ms,typ_ms,max_ms"]
    for t in TERMS:
        mn, ty, mx = b["terms"][t]
        out.append("%s,%.1f,%.1f,%.1f" % (t, mn, ty, mx))
    mn, ty, mx = b["total"]
    out.append("TOTAL,%.1f,%.1f,%.1f" % (mn, ty, mx))
    out.append("dominant_typ=%s dominant_max=%s (max = sum of per-term maxima, a bound, not a percentile)"
               % (b["dominant_typ"], b["dominant_max"]))
    if measured is not None:
        out.append("measured_g2g_ms=%.1f model_typ=%.1f residual=%+.1f%s" % (
            measured, ty, measured - ty, "  OUTSIDE model [min,max]: a term is mis-modelled" if not mn <= measured <= mx else ""))
    out.append(P.footer())
    return out


def cmd_budget(P, a):
    w, h = common.split_wh(a.res) if a.res else (int(P.get("video.width")), int(P.get("video.height")))
    for flag, key in (("mcs", "rf.mcs_index"), ("bw", "rf.bandwidth_mhz")):
        if getattr(a, flag) is not None:
            P._override(key, getattr(a, flag), "OVERRIDE")
    k, n = rf_model.parse_fec(a.fec) if a.fec else (int(P.get("rf.fec_k")), int(P.get("rf.fec_n")))
    fps = a.fps or int(P.get("video.fps"))
    bitrate = a.bitrate or P.get("video.bitrate_kbps")
    P.get("video.iframe_ratio")
    mcs = int(P.get("rf.mcs_index"))
    b = budget(P, a.board, a.codec, fps, w, h, bitrate, mcs, k, n, a.lossy)
    return fmt_budget(P, b, a.board, a.codec, fps, w, h, bitrate, mcs, k, n, a.lossy, a.measured_g2g_ms)


def cmd_matrix(P, a):
    w, h = common.split_wh(a.res) if a.res else (int(P.get("video.width")), int(P.get("video.height")))
    k, n = rf_model.parse_fec(a.fec) if a.fec else (int(P.get("rf.fec_k")), int(P.get("rf.fec_n")))
    mcs = a.mcs if a.mcs is not None else int(P.get("rf.mcs_index"))
    bitrate = a.bitrate or P.get("video.bitrate_kbps")
    out = ["# latency_budget matrix: %dx%d %dkbps mcs=%d fec=%d/%d lossy=%d" % (w, h, bitrate, mcs, k, n, int(a.lossy)),
           "board,codec,fps,util,min_ms,typ_ms,max_ms,dominant_typ,dominant_max"]
    for (board, codec) in sorted(DECODERS):
        for fps in (30, 60):
            b = budget(P, board, codec, fps, w, h, bitrate, mcs, k, n, a.lossy)
            mn, ty, mx = b["total"]
            out.append("%s,%s,%d,%.2f%s,%.1f,%.1f,%.1f,%s,%s" % (board, codec, fps, b["util"], "" if b["feasible"] else "!",
                                                                mn, ty, mx, b["dominant_typ"], b["dominant_max"]))
    out.append("# '!' = airtime utilisation > 1 (stream does not fit the air at this MCS/FEC)")
    out.append(P.footer())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("budget", "matrix", "protocol"):
        sp = sub.add_parser(name)
        common.add_cli(sp)
        if name == "protocol":
            continue
        sp.add_argument("--res", help="WxH (default: video.width x video.height)")
        sp.add_argument("--bitrate", type=float, help="kbit/s")
        sp.add_argument("--mcs", type=int)
        sp.add_argument("--fec", help="k/n")
        sp.add_argument("--lossy", action="store_true", help="include FEC recovery wait")
        if name == "budget":
            sp.add_argument("--board", required=True, choices=("pi4", "pi5"))
            sp.add_argument("--codec", required=True, choices=("h264", "h265"))
            sp.add_argument("--fps", type=int)
            sp.add_argument("--bw", type=int, choices=(20, 40))
            sp.add_argument("--measured-g2g-ms", type=float)
    a = ap.parse_args(argv)
    try:
        P = common.from_args(a)
        if a.cmd == "protocol":
            print(PROTOCOL)
            return 0
        out = {"budget": cmd_budget, "matrix": cmd_matrix}[a.cmd](P, a)
    except common.ParamError as e:
        print("latency_budget: error: %s" % e, file=sys.stderr)
        return 2
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
