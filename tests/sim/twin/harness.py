"""Runs the REAL project programs against the impairment hub and records a trace (stdlib + pymavlink in the child processes).

  bench/tx12_bridge.py   (--input stdin, real dead-man / release / single-writer logic)       <- joystick feed from this harness
  tests/sim/apm_fc.py    (ArduPilot failsafe MODEL front end, NOT ArduPilot)                  <- bridge frames through the hub
  tests/sim/udp_probe.py (paced UDP 'video' stream, real instrument)                          <- through the hub, timeline from the hub tap

SAFETY: loopback UDP only; the bridge is started with --confirm-props-off; nothing here can reach a real aircraft.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
BRIDGE = os.path.join(REPO, "bench", "tx12_bridge.py")
APM_FC = os.path.join(REPO, "tests", "sim", "apm_fc.py")
UDP_PROBE = os.path.join(REPO, "tests", "sim", "udp_probe.py")
sys.path.insert(0, HERE)
import channel as chmod  # noqa: E402
import schedule as sched  # noqa: E402

STICKS = "1500 1500 1400 1500 1500 1500 1500 1500\n"   # throttle (ch3) 1400: distinguishable from the FC's stale 'regular RC' 1000


def have_pymavlink(py):
    try:
        return subprocess.run([py, "-c", "import pymavlink"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SBC_GS_", "SBC_CFG_"))}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("HOME", tempfile.gettempdir())
    env.update(extra or {})
    return env


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Proc:
    """Child process whose output lines are timestamped on arrival (monotonic clock)."""

    def __init__(self, argv, env, stdin=False):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, env=env, text=True, bufsize=1)
        self.lines = []
        self._th = threading.Thread(target=self._read, daemon=True)
        self._th.start()

    def _read(self):
        for ln in self.p.stdout:
            self.lines.append((time.monotonic(), ln.rstrip("\n")))

    def wait_for(self, text, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(text in ln for _t, ln in self.lines):
                return True
            if self.p.poll() is not None:
                break
            time.sleep(0.02)
        return any(text in ln for _t, ln in self.lines)

    def stop(self, sig=signal.SIGTERM, timeout=3.0):
        if self.p.poll() is None:
            try:
                self.p.send_signal(sig)
                self.p.wait(timeout)
            except (subprocess.TimeoutExpired, OSError):
                self.p.kill()
                self.p.wait(2)
        self._th.join(1.0)
        for f in (self.p.stdin, self.p.stdout):
            try:
                if f:
                    f.close()
            except (OSError, ValueError):
                pass


def run_plan(plan, P, seed, py=None, input_stalls=(), video=True, tail_s=0.4, ready_timeout=10.0):
    """Execute one replay plan against the real programs. P = schedule.effective(...). Returns the raw trace dict (times relative to plan start)."""
    py = py or sys.executable
    tmp = tempfile.mkdtemp(prefix="twin-")
    ch = chmod.Channel(seed, seg_at=lambda t: sched.seg_at(plan["segs"], t))
    procs = []
    try:
        fc_argv = [py, APM_FC, "--conn", "udpout:127.0.0.1:%d" % ch.ports["fc"], "--sysid", str(P["fc_sysid"]),
                   "--rc-override-time", "%.4f" % P["rc_override_time_real"], "--fs-gcs-enable", str(int(P["fs_gcs_enable"])),
                   "--fs-gcs-timeout", "%.4f" % P["fs_gcs_timeout_real"], "--rc-fs-timeout", "%.4f" % P["rc_fs_timeout_real"],
                   "--mav-gcs-sysid", str(P["mav_gcs_sysid"])]
        if P["receiver_present"]:
            fc_argv.append("--receiver-present")
        br_argv = [py, BRIDGE, "--input", "stdin", "--conn", "udpout:127.0.0.1:%d" % ch.ports["gs"], "--sysid", str(P["bridge_sysid"]),
                   "--confirm-props-off", "--lock", os.path.join(tmp, "tx12.lock"), "--tx-trace", os.path.join(tmp, "tx.trace"),
                   "--rate", "%.3f" % P["rate_hz"], "--deadman-ms", str(P["deadman_ms_real"])]
        benv = _env({"SBC_GS_TX12_HB_PERIOD_S": "%.3f" % P["hb_period_real"], "SBC_GS_TX12_RELEASE_HOLD_S": "%.3f" % P["release_hold_real"]})
        fc = Proc(fc_argv, _env())
        br = Proc(br_argv, benv, stdin=True)
        procs += [fc, br]
        if not (fc.wait_for("[apm_fc]", ready_timeout) and br.wait_for("[tx12_bridge]", ready_timeout)):
            raise RuntimeError("programs did not start: fc=%r bridge=%r" % ([ln for _t, ln in fc.lines][-3:], [ln for _t, ln in br.lines][-3:]))
        stalls = list(input_stalls) + sched.input_stalls(plan)
        stop = threading.Event()
        t0_box = []

        def feeder():
            while not stop.is_set():
                t = (time.monotonic() - t0_box[0]) if t0_box else 0.0
                if not any(a <= t < b for a, b in stalls):
                    try:
                        br.p.stdin.write(STICKS)
                        br.p.stdin.flush()
                    except (OSError, ValueError):
                        return
                time.sleep(1.0 / P["rate_hz"])

        th = threading.Thread(target=feeder, daemon=True)
        th.start()
        vid_port = _free_port()
        ch.set_video_sink(("127.0.0.1", vid_port))
        t0 = time.monotonic()
        t0_box.append(t0)
        ch.start(t0)
        probe = None
        if video:
            n = int(P["video_pps"] * (plan["real_s"] + 0.3))
            probe = Proc([py, UDP_PROBE, "--send-to", "127.0.0.1:%d" % ch.ports["vid_in"], "--listen", "127.0.0.1:%d" % vid_port,
                          "--count", str(n), "--rate", "%.1f" % P["video_pps"], "--size", "300", "--settle", "0.3", "--min-delivery", "0"],
                         _env())
            procs.append(probe)
        while time.monotonic() - t0 < plan["real_s"] + tail_s:
            time.sleep(0.02)
        stop.set()
        t_end = time.monotonic() - t0
        if probe is not None:
            probe.p.wait(timeout=8.0) if probe.p.poll() is None else None
        br.stop()
        fc.stop()
        ch.stop()
        rec = list(ch.rec)
        trace = {
            "real_s": plan["real_s"], "t_end": t_end,
            "g2f": [r for r in rec if r["flow"] == "g2f"], "f2g": [r for r in rec if r["flow"] == "f2g"],
            "video": [r for r in rec if r["flow"] == "vid"],
            "fc_lines": [(t - t0, ln) for t, ln in fc.lines], "bridge_lines": [(t - t0, ln) for t, ln in br.lines],
            "probe_lines": [ln for _t, ln in probe.lines] if probe else [],
            "input_stalls": stalls,
        }
        return trace
    finally:
        for pr in procs:
            try:
                pr.stop(timeout=1.0)
            except OSError:
                pass
        ch.stop() if not ch._stop.is_set() else None
        shutil.rmtree(tmp, ignore_errors=True)
