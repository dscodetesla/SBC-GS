#!/usr/bin/env python3
"""Paced UDP sender + receiver in one process: delivery ratio and one-way latency of any UDP path.

  udp_probe.py --send-to 127.0.0.1:15602 --listen 127.0.0.1:15600 --count 200 --rate 100
Each datagram carries a sequence number and a monotonic send time (same host, so latency is valid).
Last line:  PROBE sent=N recv=M lost=L dup=D ooo=O min=.. p50=.. p95=.. max=.. ms
Exit 0 if recv/sent >= --min-delivery (default 0.99). Python stdlib only.
"""
import argparse
import socket
import struct
import sys
import threading
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simcfg  # noqa: E402

CFG = simcfg.load(["sim"])   # SIM_UDP_* keys, docs/CONFIG.md


def hp(s):
    h, p = s.rsplit(":", 1)
    return h, int(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--send-to", required=True)
    ap.add_argument("--listen", required=True)
    ap.add_argument("--count", type=int, default=CFG["SIM_UDP_COUNT"])
    ap.add_argument("--rate", type=float, default=CFG["SIM_UDP_RATE_PPS"], help="packets per second")
    ap.add_argument("--size", type=int, default=CFG["SIM_UDP_SIZE"])
    ap.add_argument("--settle", type=float, default=CFG["SIM_UDP_SETTLE_S"], help="seconds to wait for stragglers after the last packet")
    ap.add_argument("--min-delivery", type=float, default=CFG["SIM_UDP_MIN_DELIVERY"])
    a = ap.parse_args()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(hp(a.listen))
    rx.settimeout(0.1)
    got = {}
    dup = ooo = 0
    stop = threading.Event()

    def reader():
        nonlocal dup, ooo
        last = -1
        while not stop.is_set():
            try:
                d = rx.recv(65535)
            except socket.timeout:
                continue
            now = time.monotonic()
            if len(d) < 12:
                continue
            seq, ts = struct.unpack_from("<Id", d)
            if seq in got:
                dup += 1
                continue
            if seq < last:
                ooo += 1
            last = max(last, seq)
            got[seq] = (now - ts) * 1000

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = hp(a.send_to)
    pad = b"\0" * max(0, a.size - 12)
    t0 = time.monotonic()
    for i in range(a.count):
        tx.sendto(struct.pack("<Id", i, time.monotonic()) + pad, dst)
        delay = t0 + (i + 1) / a.rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    time.sleep(a.settle)
    stop.set()
    th.join()
    lat = sorted(got.values())
    n = len(lat)
    if n:
        print(f"PROBE sent={a.count} recv={n} lost={a.count - n} dup={dup} ooo={ooo} min={lat[0]:.1f} "
              f"p50={lat[n // 2]:.1f} p95={lat[min(n - 1, int(.95 * n))]:.1f} max={lat[-1]:.1f} ms")
    else:
        print(f"PROBE sent={a.count} recv=0 lost={a.count}")
    return 0 if n >= a.min_delivery * a.count else 1


if __name__ == "__main__":
    sys.exit(main())
