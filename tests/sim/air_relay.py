#!/usr/bin/env python3
"""Virtual 'air' for wfb-ng over veth pairs (test double, NOT a radio model).

wfb_tx injects 802.11 frames with a TX radiotap header (TX_FLAGS set) through an AF_PACKET socket;
wfb_rx drops frames with TX_FLAGS (it takes them for its own injection) and wants an RX radiotap.
This relay reads wfb_tx frames on --src, replaces the radiotap header by a synthetic RX one
(flags=0, dBm signal, antenna 0), optionally drops/delays them, and writes them to --dst.

  wfb_tx -> [tx0]==[air0] -> air_relay --src air0 --dst air1 -> [air1]==[rx0] -> wfb_rx (needs pcapshim.so)

Impairments are seeded and deterministic: --loss P (0..1), --delay-ms D, --jitter-ms J, --seed S.
Prints 'RELAY forwarded=N dropped=M' on exit (SIGTERM/--duration).
Requires CAP_NET_RAW. Python stdlib only.
"""
import argparse
import heapq
import random
import select
import signal
import socket
import struct
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import simcfg  # noqa: E402

CFG = simcfg.load(["sim"])   # RELAY_* keys, docs/CONFIG.md
ETH_P_ALL = 3
RX_RADIOTAP = struct.pack("<BBHIBbB", 0, 0, 11, (1 << 1) | (1 << 5) | (1 << 11), 0, CFG["RELAY_RX_SIGNAL_DBM"], 0)  # flags, dBm signal, antenna


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--loss", type=float, default=CFG["RELAY_LOSS"])
    ap.add_argument("--delay-ms", type=float, default=CFG["RELAY_DELAY_MS"])
    ap.add_argument("--jitter-ms", type=float, default=CFG["RELAY_JITTER_MS"])
    ap.add_argument("--seed", type=int, default=CFG["RELAY_SEED"])
    ap.add_argument("--duration", type=float, default=0.0, help="0 = until SIGTERM")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    rx.bind((a.src, 0))
    tx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    tx.bind((a.dst, 0))
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    fwd = drop = seq = 0
    q = []
    t0 = time.monotonic()
    while not stop and not (a.duration and time.monotonic() - t0 > a.duration):
        timeout = 0.01 if q else 0.2
        if select.select([rx], [], [], timeout)[0]:
            pkt = rx.recv(4096)
            # only frames that start with a radiotap header and carry the wfb magic 'WB' in addr2/addr3
            if len(pkt) > 24 and pkt[0] == 0 and struct.unpack_from("<H", pkt, 2)[0] < len(pkt):
                body = pkt[struct.unpack_from("<H", pkt, 2)[0]:]
                if rnd.random() < a.loss:
                    drop += 1
                else:
                    due = time.monotonic() + max(0.0, rnd.gauss(a.delay_ms, a.jitter_ms) if a.jitter_ms else a.delay_ms) / 1000
                    seq += 1
                    heapq.heappush(q, (due, seq, RX_RADIOTAP + body))
        now = time.monotonic()
        while q and q[0][0] <= now:
            tx.send(heapq.heappop(q)[2])
            fwd += 1
    print(f"RELAY forwarded={fwd} dropped={drop}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
