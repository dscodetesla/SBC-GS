#!/usr/bin/env python3
"""Probe REAL ArduPilot semantics against the prebuilt Copter SITL binary (manual tool, not part of smoke.sh).

The binary is fetched from firmware.ardupilot.org (HTTPS, public) into $SITL_DIR (default: a temp dir) and
run with --model + (no radio, no hardware, x86-64 static ELF). Each scenario starts a fresh SITL (-w).
Scenarios (results are printed as 'OBS key=value'; --check turns the documented expectations into PASS/FAIL):
  params    FS_GCS_ENABLE/FS_GCS_TIMEOUT/RC_OVERRIDE_TIME/MAV_GCS_SYSID/RC_OPTIONS defaults, firmware version
  override  RC_CHANNELS_OVERRIDE accepted from sysid 255, ignored from sysid 77; time until it expires
  gcsfs     GCS failsafe: which sender counts as a GCS heartbeat (255 / 125 / MANUAL_CONTROL), latency, clear on resume
SAFETY: simulator only. Never point this at a real vehicle.  Needs pymavlink (bench/requirements.txt).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from pymavlink import mavutil

M = mavutil.mavlink
BASE = "https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter"
# copter.parm is only needed for model defaults; SITL runs without it too (we pass none).


def fetch(d):
    exe = os.path.join(d, "arducopter")
    if not os.path.exists(exe):
        with urllib.request.urlopen(BASE, timeout=60) as r, open(exe, "wb") as f:
            shutil.copyfileobj(r, f)
        os.chmod(exe, 0o755)
    return exe


class Sitl:
    def __init__(self, exe, wd):
        with open(os.path.join(wd, "defaults.parm"), "w") as f:
            f.write("FRAME_CLASS 1\nFRAME_TYPE 0\nARMING_SKIPCHK -1\nFS_THR_ENABLE 1\n")
        self.p = subprocess.Popen([exe, "-w", "--model", "+", "--speedup", "1", "-I0", "--defaults",
                                   os.path.join(wd, "defaults.parm")], cwd=wd,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        t = time.time()
        while True:
            try:
                self.gcs = self.link(5760, 255)
                if self.gcs.wait_heartbeat(timeout=3):
                    break
            except OSError:
                pass
            if time.time() - t > 30:
                raise SystemExit("SITL did not come up")
            time.sleep(0.5)
        self.t, self.c = self.gcs.target_system, self.gcs.target_component

    @staticmethod
    def link(port, sysid):
        return mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}", source_system=sysid, source_component=190)

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()

    def getp(self, n):
        self.gcs.mav.param_request_read_send(self.t, self.c, n.encode(), -1)
        end = time.time() + 5
        while time.time() < end:
            r = self.gcs.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
            if r and r.param_id == n:
                return r.param_value

    def setp(self, n, v):
        self.gcs.mav.param_set_send(self.t, self.c, n.encode(), v, M.MAV_PARAM_TYPE_REAL32)
        end = time.time() + 5
        while time.time() < end:
            r = self.gcs.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
            if r and r.param_id == n:
                return r.param_value

    def rc1(self, link=None):
        link = link or self.gcs
        v = None
        end = time.time() + 0.35
        while time.time() < end:
            r = link.recv_match(type="RC_CHANNELS", blocking=True, timeout=0.2)
            if r:
                v = r.chan1_raw
        return v


def obs(k, v):
    print(f"OBS {k}={v}", flush=True)


def sc_params(s, check):
    ok = True
    for n, want in (("FS_GCS_ENABLE", 0), ("FS_GCS_TIMEOUT", 5), ("RC_OVERRIDE_TIME", 3), ("MAV_GCS_SYSID", 255),
                    ("MAV_GCS_SYSID_HI", 0), ("RC_FS_TIMEOUT", 1)):
        v = s.getp(n)
        obs(n, v)
        ok &= v == want
    obs("SYSID_MYGCS_exists", s.getp("SYSID_MYGCS") is not None)
    s.gcs.mav.command_long_send(s.t, s.c, M.MAV_CMD_REQUEST_MESSAGE, 0, 148, 0, 0, 0, 0, 0, 0)
    v = s.gcs.recv_match(type="AUTOPILOT_VERSION", blocking=True, timeout=3)
    if v:
        sv = v.flight_sw_version
        obs("firmware", f"{sv >> 24}.{(sv >> 16) & 255}.{(sv >> 8) & 255}")
    return ok


def sc_override(s, check):
    s.gcs.mav.request_data_stream_send(s.t, s.c, M.MAV_DATA_STREAM_RC_CHANNELS, 10, 1)
    base = None
    for _ in range(10):
        base = s.rc1()
        if base is not None:
            break
    obs("rc_chan1_baseline", base)
    other = s.link(5762, 77)   # SERIAL1 of the SITL: second MAVLink link with a NON-GCS sysid
    other.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, 0)
    other.wait_heartbeat(timeout=10)
    t0 = time.time()
    while time.time() - t0 < 1.5:
        other.mav.rc_channels_override_send(s.t, s.c, 1800, *([65535] * 7))
        time.sleep(0.05)
    from_77 = s.rc1()
    obs("chan1_while_sysid77_overrides", from_77)
    t0 = time.time()
    while time.time() - t0 < 1.5:
        s.gcs.mav.rc_channels_override_send(s.t, s.c, 1700, *([65535] * 7))
        time.sleep(0.05)
    during = s.rc1()
    obs("chan1_while_sysid255_overrides", during)
    tstop = time.time()
    exp = None
    while time.time() - tstop < 8:
        v = s.rc1()
        if v is not None and v != 1700:
            exp = time.time() - tstop
            break
    obs("override_expiry_s", None if exp is None else round(exp, 2))
    obs("chan1_after_expiry", s.rc1())
    return from_77 == base and during == 1700 and exp is not None and 2.5 <= exp <= 3.6


def sc_gcsfs(s, check):
    s.setp("FS_GCS_ENABLE", 1)
    s.setp("FS_GCS_TIMEOUT", 2)
    s.gcs.mav.set_mode_send(s.t, M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 0)   # STABILIZE
    end = time.time() + 60
    while time.time() < end:   # wait for the EKF, otherwise even a forced arm is refused
        r = s.gcs.recv_match(type="STATUSTEXT", blocking=True, timeout=0.5)
        if r and "EKF3 active" in r.text:
            break
    time.sleep(1)
    s.gcs.mav.command_long_send(s.t, s.c, M.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 21196, 0, 0, 0, 0, 0)  # force arm (SITL only)
    texts = []
    timeline = []

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            r = s.gcs.recv_match(type="STATUSTEXT", blocking=True, timeout=0.1)
            if r:
                texts.append((time.time(), r.text))
                if "ailsafe" in r.text:
                    timeline.append(r.text)

    def beat(kind, sec):
        end = time.time() + sec
        while time.time() < end:
            if kind == "hb255":
                s.gcs.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            elif kind == "hb125":
                other.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            elif kind == "manual":
                s.gcs.mav.manual_control_send(s.t, 0, 0, 500, 0, 0)
            pump(1.0 if kind != "none" else 0.1)

    other = s.link(5762, 125)
    pump(2)
    armed = False
    end = time.time() + 5
    while time.time() < end and not armed:
        r = s.gcs.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
        armed = bool(r and r.get_srcSystem() == s.t and r.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED)
    obs("armed", armed)
    obs("texts_after_arm", [t for _, t in texts])
    texts.clear()
    results = {}
    for kind in ("hb255", "hb125", "manual"):
        beat("hb255", 3)               # make failsafe state known-good first
        texts.clear()
        t0 = time.time()
        beat(kind, 5)                  # only this sender keeps talking
        fs = [t - t0 for t, x in texts if "GCS Failsafe" in x and "Cleared" not in x]
        results[kind] = bool(fs)
        obs(f"failsafe_when_only_{kind}_sends", f"{bool(fs)}" + (f" after {fs[0]:.1f}s" if fs else ""))
        beat("none", 0.1)
    beat("hb255", 3)
    obs("failsafe_text_timeline", timeline)
    return results.get("hb255") is False and results.get("hb125") is True


SCEN = {"params": sc_params, "override": sc_override, "gcsfs": sc_gcsfs}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario", nargs="*", help="one or more of: " + ", ".join(SCEN) + " (default: all)")
    ap.add_argument("--check", action="store_true", help="exit 1 if an observation differs from the documented expectation")
    a = ap.parse_args()
    bad = [n for n in a.scenario if n not in SCEN]
    if bad:
        ap.error(f"unknown scenario {bad}")
    d = os.environ.get("SITL_DIR") or tempfile.mkdtemp(prefix="sitl-")
    os.makedirs(d, exist_ok=True)
    exe = fetch(d)
    rc = 0
    for name in a.scenario or list(SCEN):
        wd = tempfile.mkdtemp(prefix="sitl-wd-")
        s = Sitl(exe, wd)
        try:
            ok = SCEN[name](s, a.check)
        finally:
            s.stop()
            shutil.rmtree(wd, ignore_errors=True)
        print(f"{'PASS' if ok else 'FAIL'}  sitl {name}", flush=True)
        rc |= 0 if ok or not a.check else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
