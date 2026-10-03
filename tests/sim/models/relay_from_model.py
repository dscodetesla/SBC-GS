#!/usr/bin/env python3
"""Adapter: RF model scenario -> loss/delay/jitter for tests/sim/air_relay.py (wfb-ng over veth).

air_relay.py takes constant impairments on its command line (--loss P --delay-ms D --jitter-ms J --seed S
[--duration T]); it has no schedule file and no burst model. This adapter therefore emits
  --format args  one line of relay arguments for a fixed scenario   (use as: air_relay.py --src A --dst B $(this))
  --format cmds  one full air_relay.py command per segment with --duration (run them one after another)
  --format json  the same schedule as JSON (segments with the model numbers and the expected residual)
A scenario is "distance D, MCS m, FEC k/n" (optionally a distance ramp D0:D1:SECONDS:STEPS).

The relay drops individual 802.11 frames (data AND parity) independently, so the model's per-frame loss
is replayed as --loss. With --loss-model ge the burst structure cannot be replayed: the adapter emits the
iid loss that gives the SAME residual loss after FEC k/n (rf_model.iid_equivalent), so the veth run
reproduces the model's video-packet loss, not its burst statistics. Start wfb_tx with the printed -k/-n.
Informational lines go to stderr; stdout carries only the requested format.

  relay_from_model.py --distance 1000 --mcs 1 --fec 8/12 --format args
  relay_from_model.py --ramp 500:1500:60:6 --mcs 1 --fec 8/12 --format cmds
"""
import argparse
import json
import os
import sys

import common
import rf_model

HERE = os.path.dirname(os.path.abspath(__file__))
RELAY = os.path.normpath(os.path.join(HERE, "..", "air_relay.py"))


def segment(P, d, mcs, k, n):
    snr = rf_model.snr_db(P, d)
    per = rf_model.frame_per(P, mcs, snr)
    res = rf_model.residual(P, per, k, n)
    loss = rf_model.iid_equivalent(P, per, k, n) if P.get("rf.loss_model") == "ge" else per
    air_ms = (rf_model.frame_airtime_us(P, mcs) + P.get("rf.mac_access_us")) / 1000.0
    return {
        "distance_m": round(d, 1), "snr_db": round(snr, 2), "frame_per": per, "residual_model": res,
        "loss": round(min(max(loss, 0.0), 1.0), 6),
        "delay_ms": round(air_ms + P.get("rf.relay_extra_delay_ms"), 4),
        "jitter_ms": round(P.get("rf.relay_jitter_ms"), 4),
    }


def schedule(P, a, mcs, k, n):
    if a.ramp:
        try:
            d0, d1, secs, steps = a.ramp.split(":")
            d0, d1, secs, steps = float(d0), float(d1), float(secs), int(steps)
        except ValueError:
            raise common.ParamError("--ramp wants D0:D1:SECONDS:STEPS")
        if steps < 1 or secs <= 0:
            raise common.ParamError("--ramp needs steps >= 1 and seconds > 0")
        dists = [d0 + (d1 - d0) * (i + 0.5) / steps for i in range(steps)]
        dur = secs / steps
    else:
        if a.distance is None:
            raise common.ParamError("give --distance or --ramp")
        dists, dur = [a.distance], a.duration
    segs, t = [], 0.0
    for i, d in enumerate(dists):
        s = segment(P, d, mcs, k, n)
        s.update({"t_start_s": round(t, 3), "duration_s": round(dur, 3), "seed": a.seed + i})
        segs.append(s)
        t += dur
    return segs


def args_of(s, with_duration):
    out = "--loss %.6f --delay-ms %.4f --jitter-ms %.4f --seed %d" % (s["loss"], s["delay_ms"], s["jitter_ms"], s["seed"])
    if with_duration and s["duration_s"] > 0:
        out += " --duration %.3f" % s["duration_s"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_cli(ap)
    ap.add_argument("--distance", type=float, help="metres")
    ap.add_argument("--ramp", help="D0:D1:SECONDS:STEPS (distance schedule)")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds for a fixed scenario (0 = until SIGTERM)")
    ap.add_argument("--mcs", type=int)
    ap.add_argument("--bw", type=int, choices=(20, 40))
    ap.add_argument("--fec", help="k/n (default from params)")
    ap.add_argument("--fading", choices=("none", "rayleigh", "rician"))
    ap.add_argument("--loss-model", choices=("iid", "ge"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--format", choices=("args", "cmds", "json"), default="args")
    ap.add_argument("--src", default="air0")
    ap.add_argument("--dst", default="air1")
    a = ap.parse_args(argv)
    try:
        P = common.from_args(a)
        k, n = rf_model.apply_common(P, a)
        mcs = int(P.get("rf.mcs_index"))
        segs = schedule(P, a, mcs, k, n)
    except common.ParamError as e:
        print("relay_from_model: error: %s" % e, file=sys.stderr)
        return 2
    print("# scenario: mcs=%d fec=%d/%d loss_model=%s fading=%s -> run wfb_tx with -k %d -n %d"
          % (mcs, k, n, P.get("rf.loss_model"), P.get("rf.fading_model"), k, n), file=sys.stderr)
    for s in segs:
        print("# d=%.0fm snr=%.1fdB frame_per=%.3e residual(model)=%.3e relay --loss %.6f"
              % (s["distance_m"], s["snr_db"], s["frame_per"], s["residual_model"], s["loss"]), file=sys.stderr)
    print(P.footer(), file=sys.stderr)
    if a.format == "json":
        print(json.dumps({"schema": "sbc-gs-relay-schedule/1", "relay": "tests/sim/air_relay.py",
                          "wfb_tx": {"k": k, "n": n, "mcs": mcs}, "segments": segs}, indent=1))
    elif a.format == "cmds":
        for s in segs:
            print("python3 %s --src %s --dst %s %s" % (RELAY, a.src, a.dst, args_of(s, True)))
    else:
        if len(segs) != 1:
            print("relay_from_model: error: --format args needs a single distance (use --format cmds for a ramp)",
                  file=sys.stderr)
            return 2
        print(args_of(segs[0], a.duration > 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
