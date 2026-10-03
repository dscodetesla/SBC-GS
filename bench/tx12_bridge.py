#!/usr/bin/env python3
"""Safe single-writer RC bridge: joystick-like source -> MAVLink RC_CHANNELS_OVERRIDE.

Inputs: --input evdev:/dev/input/eventN (EdgeTX USB joystick; python-evdev is
imported lazily), --input stdin ('ch1 ch2 ... chN' in microseconds per line),
--input sweep (synthetic, bench only).

Safety model (all of it is exercised by bench/tx12-bridge-test.sh):
  * refuses to start without --confirm-props-off or --real-fc-armed-ok;
  * single writer: an exclusive flock() lock file, a second instance refuses;
  * dead-man: no fresh valid input for --deadman-ms -> sticks stop, throttle is
    forced to its failsafe value for a few frames, then all channels are
    released (0) for 1 s, then NOTHING is sent (ArduPilot RC_OVERRIDE_TIME
    expires and the RC receiver takes over again);
  * values clamped to 1000-2000; NaN / absurd samples are dropped (and so do
    not refresh the dead-man timer); sending rate is capped by --max-rate;
  * SIGTERM / SIGINT / normal exit: throttle failsafe frames, then release x5.

The sysid (--sysid, default 255) MUST match the FC's MAV_GCS_SYSID (see
docs/MAVLINK-ROUTER.md, section 6), otherwise ArduPilot silently ignores the
overrides.
Do NOT connect this to a real aircraft with propellers fitted. RC over wfb-ng
must never be the only control channel.

Every tunable (dead-man, rates, clamp range, failsafe throttle, sysid, ...) is a registry key TX12_* in
config/registry.tsv (docs/CONFIG.md): CLI flag > environment SBC_GS_TX12_* > /config/sbc-gs.env > profile > default.
Keys flagged SAFETY have hard bounds; going beyond them needs --i-know (or SBC_GS_I_KNOW=1) and is logged loudly.
"""
import argparse
import fcntl
import importlib.util
import json
import math
import os
import select
import signal
import sys
import tempfile
import threading
import time



def _load_cfg_module():
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(here, "..", "config"), os.path.join(here, "config")):
        p = os.path.join(d, "load.py")
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("sbc_gs_load", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("config/load.py not found (expected ../config next to bench/)")


CFGLIB = _load_cfg_module()

NCH = 8            # cfg-ok: protocol (RC_CHANNELS_OVERRIDE has 8 channels here)
IGNORE = 65535     # cfg-ok: ArduPilot: field is ignored
RELEASE = 0        # ArduPilot: channel released to the RC receiver
# Tunables, filled from the registry (built-in defaults at import, the full layered config in parse_args()).
LO = HI = SANE_MIN = SANE_MAX = RELEASE_HOLD_S = THROTTLE_FS_FRAMES = EXIT_RELEASE_FRAMES = EVDEV_POLL_S = None


def configure(cfg):
    global LO, HI, SANE_MIN, SANE_MAX, RELEASE_HOLD_S, THROTTLE_FS_FRAMES, EXIT_RELEASE_FRAMES, EVDEV_POLL_S
    LO, HI = cfg["TX12_CLAMP_LO_US"], cfg["TX12_CLAMP_HI_US"]
    SANE_MIN, SANE_MAX = cfg["TX12_SANE_MIN_US"], cfg["TX12_SANE_MAX_US"]   # outside of this a sample is "absurd" and dropped
    RELEASE_HOLD_S = cfg["TX12_RELEASE_HOLD_S"]
    THROTTLE_FS_FRAMES = cfg["TX12_THROTTLE_FS_FRAMES"]
    EXIT_RELEASE_FRAMES = cfg["TX12_EXIT_RELEASE_FRAMES"]
    EVDEV_POLL_S = cfg["TX12_EVDEV_POLL_S"]


configure(CFGLIB.resolve(["tx12"], defaults_only=True))


def log(msg):
    print(f"[tx12_bridge] {msg}", flush=True)


# ---------------------------------------------------------------- mapping

def clamp_us(v):
    return max(LO, min(HI, int(round(v))))


def sanitize(values):
    """values: list of up to 8 numbers (None = unmapped). Returns list of 8 ints
    (clamped us, or IGNORE) or None if the sample is NaN/absurd."""
    if values is None or len(values) == 0 or len(values) > NCH:
        return None
    out = []
    for v in values:
        if v is None:
            out.append(IGNORE)
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f):
            return None
        if f == IGNORE:
            out.append(IGNORE)
        elif SANE_MIN <= f <= SANE_MAX:
            out.append(clamp_us(f))
        else:
            return None
    return out + [IGNORE] * (NCH - len(out))


def map_axis(raw, cfg):
    """Raw evdev axis value -> microseconds, using one entry of the mapping file.

    cfg keys: min, max (required); center (optional: bipolar axis, else linear
    min..max -> 1000..2000); deadband (fraction 0..1 of half-travel around
    center); reverse (bool).
    """
    lo, hi = float(cfg["min"]), float(cfg["max"])
    if not hi > lo:
        raise ValueError("axis max must be > min")
    raw = max(lo, min(hi, float(raw)))
    if "center" in cfg:
        c = float(cfg["center"])
        n = (raw - c) / (hi - c) if raw >= c else (raw - c) / (c - lo)
        db = float(cfg.get("deadband", 0.0))
        if abs(n) <= db:
            n = 0.0
        elif db > 0:
            n = math.copysign((abs(n) - db) / (1.0 - db), n)
        if cfg.get("reverse"):
            n = -n
        return clamp_us(1500 + 500 * n)
    n = (raw - lo) / (hi - lo)
    if cfg.get("reverse"):
        n = 1.0 - n
    return clamp_us(LO + (HI - LO) * n)


def load_map(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    axes = d.get("axes")
    if not isinstance(axes, dict) or not axes:
        raise ValueError("mapping file needs a non-empty 'axes' object")
    for name, cfg in axes.items():
        ch = cfg.get("channel")
        if not isinstance(ch, int) or not 1 <= ch <= NCH:
            raise ValueError(f"axis {name}: channel must be an integer 1-{NCH}")
        if "min" not in cfg or "max" not in cfg:
            raise ValueError(f"axis {name}: min and max are required")
    return axes


# ---------------------------------------------------------------- input sources

class Source:
    """poll() -> (values or None, monotonic timestamp of last valid sample or None)."""

    def poll(self):
        raise NotImplementedError

    def close(self):
        pass


class SweepSource(Source):
    def __init__(self, throttle_ch):
        self.t0 = time.monotonic()
        self.thr = throttle_ch

    def poll(self):
        now = time.monotonic()
        ph = now - self.t0
        v = [1500 + 400 * math.sin(ph + i) for i in range(4)] + [1500] * 4
        v[self.thr - 1] = 1000   # a sweep never raises the throttle
        return sanitize(v), now


class StdinSource(Source):
    def __init__(self, fh=None):
        self.fh = fh or sys.stdin
        self.lock = threading.Lock()
        self.vals, self.ts = None, None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        for line in self.fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                s = sanitize([float(t) for t in line.split()])
            except ValueError:
                s = None
            if s is None:
                log(f"input dropped (NaN/absurd/malformed): {line[:60]!r}")
                continue
            with self.lock:
                self.vals, self.ts = s, time.monotonic()
        log("stdin EOF: dead-man will take over")

    def poll(self):
        with self.lock:
            return self.vals, self.ts


class EvdevSource(Source):
    """EdgeTX USB joystick via python-evdev. Freshness = device is alive: evdev
    emits events only on change, so a still stick must not trip the dead-man;
    unplugging (read error) or a stalled reader does."""

    def __init__(self, path, mapping):
        try:
            import evdev                      # lazy: not needed by CI or other inputs
        except ImportError:
            raise SystemExit("python-evdev is not installed (pip install evdev); only --input evdev needs it")
        self.ecodes = evdev.ecodes
        try:
            self.dev = evdev.InputDevice(path)
        except OSError as e:
            raise SystemExit(f"cannot open {path}: {e}")
        self.mapping = mapping
        self.lock = threading.Lock()
        self.raw = {}
        caps = dict(self.dev.capabilities(absinfo=True)).get(self.ecodes.EV_ABS, [])
        self.codes = {}
        for name in mapping:
            code = getattr(self.ecodes, name, None)
            if code is None:
                raise SystemExit(f"unknown evdev axis name in mapping: {name}")
            self.codes[code] = name
        for code, info in caps:
            if code in self.codes:
                self.raw[code] = info.value
        missing = [n for c, n in self.codes.items() if c not in self.raw]
        if missing:
            raise SystemExit(f"device {path} has no axes: {missing}")
        self.ts = time.monotonic()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            while True:
                r, _, _ = select.select([self.dev.fd], [], [], EVDEV_POLL_S)
                if r:
                    for ev in self.dev.read():
                        if ev.type == self.ecodes.EV_ABS and ev.code in self.codes:
                            with self.lock:
                                self.raw[ev.code] = ev.value
                with self.lock:
                    self.ts = time.monotonic()
        except OSError as e:
            log(f"evdev device lost: {e}")

    def poll(self):
        with self.lock:
            vals = [None] * NCH
            for code, name in self.codes.items():
                cfg = self.mapping[name]
                vals[cfg["channel"] - 1] = map_axis(self.raw[code], cfg)
            ts = self.ts
        return sanitize(vals), ts


# ---------------------------------------------------------------- lock

def default_lock():
    d = "/run/lock"
    return os.path.join(d if os.access(d, os.W_OK) else tempfile.gettempdir(), "tx12_bridge.lock")


def take_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


# ---------------------------------------------------------------- main

def parse_args(argv=None):
    cfg = CFGLIB.load(["tx12"], argv=sys.argv[1:] if argv is None else argv)
    configure(cfg)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", required=True, help="evdev:/dev/input/eventN | stdin | sweep")
    ap.add_argument("--map", default=cfg["TX12_MAP"] or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tx12_map.example.json"),
                    help="axis mapping JSON for evdev input")
    ap.add_argument("--conn", default=cfg["TX12_CONN"])
    ap.add_argument("--sysid", type=int, default=cfg["TX12_SYSID"],
                    help="MAVLink source sysid; MUST match the FC's MAV_GCS_SYSID (docs/MAVLINK-ROUTER.md)")
    ap.add_argument("--rate", type=float, default=cfg["TX12_RATE_HZ"], help="override rate, Hz")
    ap.add_argument("--max-rate", type=float, default=cfg["TX12_MAX_RATE_HZ"], help="hard cap on frames/s ever sent")
    ap.add_argument("--deadman-ms", type=int, default=cfg["TX12_DEADMAN_MS"])
    ap.add_argument("--throttle-ch", type=int, default=cfg["TX12_THROTTLE_CH"])
    ap.add_argument("--failsafe-throttle", type=int, default=cfg["TX12_FAILSAFE_THROTTLE_US"])
    ap.add_argument("--lock", default=cfg["TX12_LOCK_PATH"] or default_lock())
    ap.add_argument("--tx-trace", default="", metavar="FILE",
                    help="test hook: append the monotonic time of every frame sent to FILE (sender-side timing)")
    ap.add_argument("--i-know", action="store_true",
                    help="allow values beyond the hard bounds of SAFETY keys (config/registry.tsv); logged loudly. Same as SBC_GS_I_KNOW=1")
    ap.add_argument("--duration", type=float, default=0, help="0 = until signal (tests use a value)")
    ap.add_argument("--confirm-props-off", action="store_true",
                    help="bench: you confirm no propellers are fitted and no battery/ESC is connected")
    ap.add_argument("--real-fc-armed-ok", action="store_true",
                    help="real flight controller: override ONLY channels 1-4 (5-8 = 65535 = ignore); "
                         "you accept that this can move an armed aircraft")
    a = ap.parse_args(argv)
    if not (a.confirm_props_off or a.real_fc_armed_ok):
        ap.error("sends RC overrides: add --confirm-props-off (bench, propellers removed) "
                 "or --real-fc-armed-ok (real FC, channels 1-4 only)")
    if not 0 < a.rate <= a.max_rate:
        ap.error("--rate must be > 0 and <= --max-rate")
    if not 1 <= a.throttle_ch <= NCH:
        ap.error("--throttle-ch must be 1-8")
    if not LO <= a.failsafe_throttle <= HI:
        ap.error(f"--failsafe-throttle must be {LO}-{HI}")
    # the registry bounds (hard bounds for SAFETY keys: e.g. --max-rate above 100 Hz, --deadman-ms below 50) apply to the CLI too
    for dest, key in (("max_rate", "TX12_MAX_RATE_HZ"), ("rate", "TX12_RATE_HZ"), ("deadman_ms", "TX12_DEADMAN_MS"),
                      ("throttle_ch", "TX12_THROTTLE_CH"), ("failsafe_throttle", "TX12_FAILSAFE_THROTTLE_US"),
                      ("sysid", "TX12_SYSID")):
        try:
            cfg.check_cli(key, getattr(a, dest))
        except CFGLIB.ConfigError as e:
            ap.error(f"--{dest.replace('_', '-')}: {e}")
    a.cfg = cfg
    return a


def main(argv=None):
    a = parse_args(argv)
    cfg = a.cfg
    COMPONENT_ID, HB_PERIOD_S = cfg["TX12_COMPONENT_ID"], cfg["TX12_HB_PERIOD_S"]
    STAT_PERIOD_S, LOOP_SLEEP_S = cfg["TX12_STAT_PERIOD_S"], cfg["TX12_LOOP_SLEEP_S"]
    stop = []
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.append(1))

    lock_fd = take_lock(a.lock)
    if lock_fd is None:
        print(f"[tx12_bridge] REFUSED: another instance holds {a.lock} (single writer)", file=sys.stderr)
        return 3

    if a.input == "stdin":
        src = StdinSource()
    elif a.input == "sweep":
        src = SweepSource(a.throttle_ch)
    elif a.input.startswith("evdev:"):
        try:
            mapping = load_map(a.map)
        except (OSError, ValueError) as e:
            print(f"[tx12_bridge] bad mapping {a.map}: {e}", file=sys.stderr)
            return 2
        src = EvdevSource(a.input[6:], mapping)
    else:
        print("[tx12_bridge] --input must be evdev:<dev>, stdin or sweep", file=sys.stderr)
        return 2

    from pymavlink import mavutil
    mav = mavutil.mavlink
    m = mavutil.mavlink_connection(a.conn, source_system=a.sysid, source_component=COMPONENT_ID)
    min_gap = 1.0 / a.max_rate
    trace = open(a.tx_trace, "a", buffering=1, encoding="utf-8") if a.tx_trace else None
    last_tx = [0.0]
    target = None
    n_sent = 0

    def tx(vals):
        nonlocal n_sent
        wait = last_tx[0] + min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)       # --max-rate guard
        last_tx[0] = time.monotonic()
        if trace:
            trace.write(f"{last_tx[0]:.6f}\n")
        m.mav.rc_channels_override_send(target, 1, *vals)
        n_sent += 1

    def sticks(vals):
        if a.real_fc_armed_ok:
            vals = vals[:4] + [IGNORE] * 4
        return vals

    def throttle_fs_frame():
        v = [RELEASE] * NCH
        v[a.throttle_ch - 1] = a.failsafe_throttle
        return v

    log(f"{a.conn} sysid={a.sysid} input={a.input} rate={a.rate} deadman={a.deadman_ms}ms "
        f"throttle_ch={a.throttle_ch} failsafe={a.failsafe_throttle} "
        f"mode={'real-fc (ch1-4 only)' if a.real_fc_armed_ok else 'bench'}")
    t0 = time.monotonic()
    state = "WAIT"          # WAIT -> ACTIVE -> RELEASE -> SILENT -> ACTIVE ...
    t_rel = 0.0
    rel_n = 0
    last_hb = last_tick = last_stat = 0.0
    try:
        while not stop:
            now = time.monotonic()
            if a.duration and now - t0 > a.duration:
                break
            if now - last_hb >= HB_PERIOD_S:
                last_hb = now
                m.mav.heartbeat_send(mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID, 0, 0, mav.MAV_STATE_ACTIVE)
            while True:
                msg = m.recv_match(blocking=False)
                if msg is None:
                    break
                if msg.get_type() == "HEARTBEAT" and msg.get_srcSystem() != a.sysid \
                        and msg.type != mav.MAV_TYPE_GCS:
                    if target != msg.get_srcSystem():
                        log(f"FC sysid={msg.get_srcSystem()}")
                    target = msg.get_srcSystem()

            if now - last_tick >= 1.0 / a.rate:
                last_tick = now
                vals, ts = src.poll()
                fresh = vals is not None and ts is not None and now - ts <= a.deadman_ms / 1000.0
                if state in ("WAIT", "SILENT", "RELEASE") and fresh and target is not None:
                    log(f"input fresh: sending sticks (was {state})")
                    state = "ACTIVE"
                elif state == "ACTIVE" and not fresh:
                    log(f"DEAD-MAN: no fresh input for {a.deadman_ms} ms: throttle failsafe, then release")
                    state, t_rel, rel_n = "RELEASE", now, 0
                if state == "ACTIVE":
                    tx(sticks(vals))
                elif state == "RELEASE":
                    if now - t_rel >= RELEASE_HOLD_S:
                        log("release hold done: silent (RC receiver resumes after RC_OVERRIDE_TIME)")
                        state = "SILENT"
                    else:
                        tx(throttle_fs_frame() if rel_n < THROTTLE_FS_FRAMES else [RELEASE] * NCH)
                        rel_n += 1
            if now - last_stat >= STAT_PERIOD_S:
                last_stat = now
                log(f"+{now - t0:5.1f}s state={state} fc={target} frames={n_sent}")
            time.sleep(LOOP_SLEEP_S)
    finally:
        if target is not None and state in ("ACTIVE", "RELEASE"):
            log("exit: throttle failsafe + release channels")
            for _ in range(THROTTLE_FS_FRAMES):
                tx(throttle_fs_frame())
            for _ in range(EXIT_RELEASE_FRAMES):
                tx([RELEASE] * NCH)
        if trace:
            trace.close()
        src.close()
        os.close(lock_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
