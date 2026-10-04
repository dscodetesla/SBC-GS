"""UDP impairment hub driven by a real-time schedule (stdlib only). The unprivileged stand-in for tests/sim/air_relay.py.

Why a stand-in: air_relay.py relays raw 802.11 frames between veth interfaces (AF_PACKET, needs root/CAP_NET_RAW) and cannot carry the
MAVLink/UDP traffic of tx12_bridge.py <-> apm_fc.py. This hub keeps air_relay's DECISION RULE byte for byte (decide(): one
rnd.random() < loss draw per frame, then max(0, gauss(delay, jitter)) for survivors, a time-ordered heap for delivery) and adds what
air_relay has no schedule file for: loss/delay/jitter that change at given instants and a full-silence state (loss = 1).
test_twin.py cross-checks decide() against the real air_relay.py when root + veth are available (SKIP otherwise).

Flows (all go through the same schedule, i.e. the symmetric-link assumption is INF/UNVERIFIED):
  bridge <-> [gs socket] hub [fc socket] <-> apm_fc     (MAVLink over UDP, both directions, one RNG stream each)
  video source -> [vid_in] hub -> video sink            (udp_probe.py datagrams; its sequence number is parsed for the timeline)
Every packet is recorded (monotonic time, silent?, dropped?, delivery time) for the analysis.
"""
import heapq
import random
import select
import socket
import struct
import threading
import time

MAX_KEEP = 400   # bytes of payload kept in the tap for MAVLink flows


def decide(rnd, loss, delay_ms, jitter_ms):
    """air_relay.py semantics: None = dropped, else the delivery delay in seconds. Consumes the RNG exactly like air_relay.main()."""
    if rnd.random() < loss:
        return None
    return max(0.0, rnd.gauss(delay_ms, jitter_ms) if jitter_ms else delay_ms) / 1000.0


class Channel:
    def __init__(self, seed, clock=time.monotonic, seg_at=None):
        self.clock = clock
        self.seg_at = seg_at           # callable(real_t) -> segment dict or None
        self.t0 = None
        self.rnd = {"g2f": random.Random(seed * 10 + 1), "f2g": random.Random(seed * 10 + 2), "vid": random.Random(seed * 10 + 3)}
        self.socks = {}
        for name in ("gs", "fc", "vid_in", "vid_out"):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            s.setblocking(False)
            self.socks[name] = s
        self.ports = {k: v.getsockname()[1] for k, v in self.socks.items()}
        self.addr = {"gs": None, "fc": None, "vid_out": None}   # learned peers (gs/fc) / configured sink
        self.rec = []                  # packet records (dicts)
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._th = None
        self._heap = []
        self._n = 0

    def set_video_sink(self, addr):
        self.addr["vid_out"] = addr

    def start(self, t0=None):
        self.t0 = self.clock() if t0 is None else t0
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()
        if self._th:
            self._th.join(2.0)
        for s in self.socks.values():
            s.close()

    def now(self):
        return self.clock() - self.t0 if self.t0 is not None else None

    def _current(self, t):
        if self.seg_at is None or t is None:
            return None
        return self.seg_at(t)

    def _handle(self, flow, data, t):
        if t is None:
            return                      # plan not started yet: the link does not exist, nothing is recorded
        seg = self._current(t)
        silent = seg is None or seg["silent"]       # before start / past the end of the plan the link is down
        rnd = self.rnd[flow]
        if seg is None:
            d = decide(rnd, 1.0, 0.0, 0.0)
        else:
            d = decide(rnd, 1.0 if seg["silent"] else seg["loss"], seg["delay_ms"], seg["jitter_ms"])
        rec = {"flow": flow, "t_in": t, "silent": bool(silent), "dropped": d is None, "t_out": None, "p": 1.0 if silent else seg["loss"]}
        if flow == "vid":
            rec["seq"] = struct.unpack_from("<I", data)[0] if len(data) >= 4 else -1
        else:
            rec["data"] = bytes(data[:MAX_KEEP])
        with self.lock:
            self.rec.append(rec)
        if d is not None:
            self._n += 1
            heapq.heappush(self._heap, (self.clock() + d, self._n, flow, rec, data))

    def _send(self, flow, data):
        try:
            if flow == "g2f" and self.addr["fc"]:
                self.socks["fc"].sendto(data, self.addr["fc"])
            elif flow == "f2g" and self.addr["gs"]:
                self.socks["gs"].sendto(data, self.addr["gs"])
            elif flow == "vid" and self.addr["vid_out"]:
                self.socks["vid_out"].sendto(data, self.addr["vid_out"])
        except OSError:
            pass

    def _run(self):
        rd = [self.socks["gs"], self.socks["fc"], self.socks["vid_in"]]
        while not self._stop.is_set():
            timeout = 0.05
            if self._heap:
                timeout = max(0.0, min(timeout, self._heap[0][0] - self.clock()))
            try:
                ready = select.select(rd, [], [], timeout)[0]
            except (OSError, ValueError):
                break
            for s in ready:
                try:
                    data, src = s.recvfrom(65535)
                except OSError:
                    continue
                t = self.now()
                if s is self.socks["gs"]:
                    self.addr["gs"] = src
                    self._handle("g2f", data, t)
                elif s is self.socks["fc"]:
                    self.addr["fc"] = src
                    self._handle("f2g", data, t)
                else:
                    self._handle("vid", data, t)
            now = self.clock()
            while self._heap and self._heap[0][0] <= now:
                _due, _n, flow, rec, data = heapq.heappop(self._heap)
                self._send(flow, data)
                rec["t_out"] = self.clock() - self.t0
