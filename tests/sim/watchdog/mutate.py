#!/usr/bin/env python3
"""Mutation check of the watchdog tests on a COPY (never in place): each mutation must make test_watchdog.py FAIL.
   mutate.py [M1 M5 ...]   Env: PY (default: this interpreter), MAVP2P."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PYF, SHF = "gs/mavlink/gs-mavlink-watchdog.py", "gs/mavlink/gs-mavlink-watchdog.sh"
MUTS = [
    ("M1", PYF, "CRC of the frame is not checked (v2)", 'if crc == struct.unpack("<H", buf[i + 10 + ln:i + 12 + ln])[0]:', "if True:"),
    ("M2", PYF, "the GCS/wfb-ng/router sysids are allowed for the watchdog heartbeat", "FORBIDDEN_SYSIDS = (255, 3, 125)", "FORBIDDEN_SYSIDS = ()"),
    ("M3", PYF, "the restart rate limit is ignored", "if len(self.restarts) < self.max_per_h:", "if True:"),
    ("M4", PYF, "a GCS heartbeat counts as the FC", "and mtype != MAV_TYPE_GCS and ap_id != MAV_AUTOPILOT_INVALID", ""),
    ("M5", PYF, "silence is measured from the start, not from the last heartbeat", "return self.last_hb if self.last_hb is not None else self.t0", "return self.t0"),
    ("M6", PYF, "restart happens as soon as the FC is lost", "silent >= self.restart_after_s", "silent >= self.loss_s"),
    ("M7", PYF, "a heartbeat does not clear the pending restart", 'self.state, self.lost_since, self.restart_pending = "up", None, False', 'self.state, self.lost_since = "up", None'),
    ("M8", PYF, "component id is not checked", "sysid == a.fc_sysid and compid == 1 and", "sysid == a.fc_sysid and"),
    ("M9", PYF, "the restart argv is accepted as an empty list", "assert isinstance(argv_restart, list) and argv_restart and all(isinstance(x, str) for x in argv_restart)", "assert isinstance(argv_restart, list)"),
    ("M10", PYF, "the watchdog heartbeat is a GCS (type 6)", "MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID = 18, 8", "MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID = 6, 8"),
    ("M11", SHF, "the launcher sources the configuration as shell", 'sbc_cfg_load --extra-file "$CONF" --extra-owner gs-mavlink gs-mavlink', '. "$CONF"; sbc_cfg_load gs-mavlink'),
    ("M12", PYF, "a bad heartbeat swallows the following good frame (no resynchronisation)", "                    i += 1                                  # a bad HEARTBEAT: resynchronise byte by byte\n                    continue", "                    pass"),
    ("M13", PYF, "no heartbeat is ever sent to register at the router", "sock.sendto(build_heartbeat(a.hb_sysid, seq), dest)", "pass"),
    ("M14", PYF, "an unplugged FC is not reported in the log", "if a.serial_dev and not os.path.exists(a.serial_dev)", "if False"),
]


# test classes that can kill a mutation (the slow real-router class only where it is needed)
FAST = ["TestParser", "TestWatch", "TestArguments", "TestMainLoopFake", "TestLauncher"]
ROUTER = {"M13": ["TestRealRouter", "TestMainLoopFake"], "M14": ["TestMainLoopFake"], "M10": ["TestParser", "TestRealRouter"]}


def main():
    want = set(sys.argv[1:])
    py = os.environ.get("PY", sys.executable)
    tmp = tempfile.mkdtemp()
    killed = n = 0
    bad = False
    try:
        for mid, f, desc, old, new in MUTS:
            if want and mid not in want:
                continue
            n += 1
            c = os.path.join(tmp, mid)
            os.makedirs(os.path.join(c, "tests", "sim"))
            os.makedirs(os.path.join(c, "gs"))
            shutil.copytree(os.path.join(REPO, "gs", "mavlink"), os.path.join(c, "gs", "mavlink"))
            shutil.copytree(os.path.join(REPO, "config"), os.path.join(c, "config"))
            shutil.copytree(HERE, os.path.join(c, "tests", "sim", "watchdog"), ignore=shutil.ignore_patterns("__pycache__"))
            p = os.path.join(c, f)
            s = open(p).read()
            if old not in s:
                print(f"ERROR    {mid}: mutation text not found ({desc})")
                bad = True
                continue
            open(p, "w").write(s.replace(old, new, 1))
            r = subprocess.run([py, os.path.join(c, "tests", "sim", "watchdog", "test_watchdog.py"), *ROUTER.get(mid, FAST)], capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                print(f"SURVIVED {mid}: {desc}")
                bad = True
            else:
                print(f"KILLED   {mid}: {desc}")
                killed += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"mutation check: killed {killed} of {n}")
    if not bad:
        print("mutation check: all mutations killed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
