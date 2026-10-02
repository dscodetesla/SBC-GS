#!/usr/bin/env python3
"""Deterministic unit tests of apm_model.ApmModel with a fake clock (no sockets, no pymavlink, < 0.1 s)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apm_model import ApmModel  # noqa: E402

n_fail = 0


def check(cond, name):
    global n_fail
    print(("PASS  " if cond else "FAIL  ") + name)
    n_fail += 0 if cond else 1


def run(m, until, step=0.05, t0=0.0):
    t = t0
    while t <= until:
        m.tick(t)
        t += step


# 1. override applies, expires after RC_OVERRIDE_TIME, falls back to the STALE regular RC value
m = ApmModel(rc_override_time=3.0, receiver_present=True)
m.on_rc_override(255, [1700] + [65535] * 7, 0.0)
check(m.channel(0, 1.0) == 1700, "override value visible while fresh")
check(m.channel(0, 3.1) == 1500, "after RC_OVERRIDE_TIME the stale regular RC value returns (docs/CHAINS.md #32862)")

# 2. only a GCS sysid may override (SITL: sysid 77 ignored)
m = ApmModel()
check(m.on_rc_override(77, [1800] + [65535] * 7, 0.0) is False and m.channel(0, 0.1) == 1500, "override from sysid 77 ignored")
m = ApmModel(mav_gcs_sysid=200)
check(m.on_rc_override(255, [1800] + [65535] * 7, 0.0) is False, "MAV_GCS_SYSID=200: sysid 255 no longer a GCS")
check(m.on_rc_override(200, [1800] + [65535] * 7, 0.0) is True, "MAV_GCS_SYSID=200: sysid 200 accepted")
m = ApmModel(mav_gcs_sysid=250, mav_gcs_sysid_hi=255)
check(m.sysid_is_gcs(252) and not m.sysid_is_gcs(249), "MAV_GCS_SYSID_HI range semantics")

# 3. 0 releases a channel, 65535 leaves it alone, per-channel expiry
m = ApmModel()
m.on_rc_override(255, [1700, 1600] + [65535] * 6, 0.0)
m.on_rc_override(255, [0, 65535] + [65535] * 6, 1.0)
check(m.channel(0, 1.1) == 1500 and m.channel(1, 1.1) == 1600, "0 releases ch1, 65535 keeps ch2")
check(m.channel(1, 3.5) == 1500, "ch2 expires 3 s after ITS last update")

# 4. RC_OVERRIDE_TIME special values (SRC rows 97): 0 disables, negative never expires
m = ApmModel(rc_override_time=0)
check(m.on_rc_override(255, [1700] + [65535] * 7, 0.0) is False, "RC_OVERRIDE_TIME=0 disables overrides")
m = ApmModel(rc_override_time=-1)
m.on_rc_override(255, [1700] + [65535] * 7, 0.0)
check(m.channel(0, 600.0) == 1700, "RC_OVERRIDE_TIME=-1 never expires (dangerous: stale stick forever)")

# 5. GCS failsafe: off by default, never armed-without-GCS, timing, sysid filter, sticky mode, flag clears
m = ApmModel(fs_gcs_enable=0)
m.on_heartbeat(255, 0.0); run(m, 60)
check(not m.gcs_failsafe, "FS_GCS_ENABLE=0 (default): no GCS failsafe ever")
m = ApmModel(fs_gcs_enable=1, fs_gcs_timeout=2.0)
run(m, 30)
check(not m.gcs_failsafe, "no GCS ever seen: GCS failsafe stays inactive (SRC gcs-failsafe.html)")
m = ApmModel(fs_gcs_enable=1, fs_gcs_timeout=2.0)
t = 0.0
while t < 5.0:
    m.on_heartbeat(255, t); m.tick(t); t += 1.0
check(not m.gcs_failsafe, "1 Hz GCS heartbeat keeps the failsafe away")
last = t - 1.0
run(m, last + 1.9, t0=last)
check(not m.gcs_failsafe, "no failsafe before FS_GCS_TIMEOUT")
run(m, last + 2.2, t0=last + 1.9)
check(m.gcs_failsafe and m.mode == "RTL", "failsafe after FS_GCS_TIMEOUT -> RTL (FS_GCS_ENABLE=1)")
m.on_heartbeat(255, last + 3.0)
check(not m.gcs_failsafe and m.mode == "RTL", "heartbeat back: flag clears (SITL) but the mode stays RTL (SRC)")
m = ApmModel(fs_gcs_enable=1, fs_gcs_timeout=2.0)
m.on_heartbeat(255, 0.0)
for k in range(1, 20):
    m.on_heartbeat(125, k * 0.5); m.tick(k * 0.5)       # mavp2p's own heartbeat (sysid 125)
check(m.gcs_failsafe, "heartbeats from sysid 125 (mavp2p) do not count (SITL)")
m = ApmModel(fs_gcs_enable=1, fs_gcs_timeout=2.0)
m.on_heartbeat(255, 0.0)
for k in range(1, 20):
    m.on_manual_control(255, k * 0.5); m.tick(k * 0.5)
check(not m.gcs_failsafe, "MANUAL_CONTROL alone keeps the failsafe away (SITL)")
m = ApmModel(fs_gcs_enable=1, fs_gcs_timeout=2.0, armed=False)
m.on_heartbeat(255, 0.0); run(m, 10)
check(m.gcs_failsafe and m.mode == "STABILIZE", "disarmed: flag/text set (SITL) but no mode action")

# 6. bridge dies while it is the only RC source (no receiver): radio failsafe after override time + RC_FS_TIMEOUT
m = ApmModel(rc_override_time=1.0, rc_fs_timeout=1.0, receiver_present=False)
for k in range(20):
    m.on_rc_override(255, [1500, 1500, 1500, 1500] + [65535] * 4, k * 0.05); m.tick(k * 0.05)
last = 19 * 0.05
check(not m.rc_failsafe, "bridge alive: no radio failsafe")
run(m, last + 1.9, t0=last)
check(not m.rc_failsafe, "within RC_OVERRIDE_TIME + RC_FS_TIMEOUT: no radio failsafe yet")
run(m, last + 2.2, t0=last + 1.9)
check(m.rc_failsafe and m.mode == "RTL", "RC_OVERRIDES lost with GCS only: radio failsafe (docs/CHAINS.md)")
m = ApmModel(rc_override_time=1.0, receiver_present=True)
m.on_rc_override(255, [1500] * 4 + [65535] * 4, 0.0); run(m, 10)
check(not m.rc_failsafe, "with a real receiver connected the override loss is NOT a radio failsafe (the receiver decides)")

print("test_apm_model: %s" % ("FAILED %d" % n_fail if n_fail else "ALL PASS"))
sys.exit(1 if n_fail else 0)
