#!/usr/bin/env python3
"""Unit tests of config/load.py (and, through the shell CLI, config/load.sh) plus the 'no override = legacy constants' proof
for every tool wired to the registry. No pymavlink needed (stubbed). Prints one 'ok'/'FAIL' line per check; exit 1 on any FAIL."""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CLEAN = {k: v for k, v in os.environ.items() if not k.startswith("SBC_GS_")}
for k in ("VIDEO_CODEC", "FC_SYSID", "WFB_CHANNEL", "SMOKE_STRICT", "SMOKE_ONLY"):
    CLEAN.pop(k, None)
os.environ.clear()
os.environ.update(CLEAN)
os.environ["SBC_GS_CONFIG"] = "/nonexistent/sbc-gs.env"
bad = 0


def chk(name, cond, detail=""):
    global bad
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    bad += 0 if cond else 1


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


L = load_mod("sbc_load", "config/load.py")
rows = L.registry()


def res(env=None, **kw):
    e = dict(CLEAN)
    e["SBC_GS_CONFIG"] = "/nonexistent/sbc-gs.env"
    e.update(env or {})
    return L.resolve(env=e, **kw)


def fails(env=None, **kw):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            res(env, **kw)
        return None
    except L.ConfigError as e:
        return str(e)


# ---------------------------------------------------------------- 1. every key: defaults valid, bounds enforced
print("== per-key bounds (python loader)")
nb = nk = 0
for k, r in rows.items():
    d = res()
    nk += 1
    if r.type.split("?")[0] in ("int", "float", "port") and (r.min != "-" or r.max != "-"):
        env = r.env
        lo_ok = r.min if r.min != "-" else None
        hi_ok = r.max if r.max != "-" else None
        for edge, want_ok in ((lo_ok, True), (hi_ok, True)):
            if edge is not None:
                e = fails({env: edge})
                if (e is None) != want_ok:
                    chk(f"{k} edge {edge} accepted", False, e or "")
                nb += 1
        for edge, delta in ((lo_ok, -1), (hi_ok, 1)):
            if edge is None:
                continue
            v = str(int(float(edge)) + delta) if r.type.split("?")[0] != "float" else str(float(edge) + delta)
            e = fails({env: v})
            nb += 1
            if e is None:
                chk(f"{k}={v} (outside {r.min}..{r.max}) rejected", False)
            elif r.safe and "safety key" not in e:
                chk(f"{k} outside bounds mentions the safety rule", False, e)
chk(f"{nk} keys, {nb} bound probes behave (edges accepted, edge+-1 rejected)", bad == 0)
chk("every SAFETY key has both hard bounds", all(r.min != "-" and r.max != "-" for r in rows.values() if r.safe))
chk("every non-str key has a default that passes its own check", fails() is None)

# ---------------------------------------------------------------- 2. safety: --i-know
print("== safety clamps")
chk("deadman 20 ms rejected", "safety key" in (fails({"SBC_GS_TX12_DEADMAN_MS": "20"}) or ""))
chk("deadman 100000 ms (dead-man 'disabled') rejected", fails({"SBC_GS_TX12_DEADMAN_MS": "100000"}) is not None)
chk("failsafe throttle 1500 rejected", fails({"SBC_GS_TX12_FAILSAFE_THROTTLE_US": "1500"}) is not None)
chk("throttle failsafe frames 0 rejected", fails({"SBC_GS_TX12_THROTTLE_FS_FRAMES": "0"}) is not None)
chk("max-rate 500 Hz rejected", fails({"SBC_GS_TX12_MAX_RATE_HZ": "500"}) is not None)
chk("SBC_GS_I_KNOW=1 accepts deadman 20 ms", fails({"SBC_GS_I_KNOW": "1", "SBC_GS_TX12_DEADMAN_MS": "20"}) is None)
chk("--i-know in argv accepts deadman 20 ms", fails({"SBC_GS_TX12_DEADMAN_MS": "20"}, argv=["--i-know"]) is None)
chk("--i-know does not relax a TYPE error", fails({"SBC_GS_TX12_DEADMAN_MS": "abc"}, argv=["--i-know"]) is not None)
chk("--i-know does not relax a non-safety bound", fails({"SBC_GS_TX12_THROTTLE_CH": "9"}, argv=["--i-know"]) is not None)

# ---------------------------------------------------------------- 3. strict parser
print("== strict parser")
td = tempfile.mkdtemp(prefix="cfgu-")


def pf(text):
    p = os.path.join(td, "f.env")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p


good = ["HB_SYSID=125\n", "HB_SYSID='125'\n", 'HB_SYSID="125"\n', "  HB_SYSID = 1\n".replace(" = ", "="), "HB_SYSID=125 # c\n",
        "# c\n\nHB_SYSID='125'\r\nROUTER=mavp2p\r\n", "\ufeffHB_SYSID=125\n", "GCS_UDP_PORTS='14560 14561'\n", "GCS_UDP_PORTS=''\n",
        "HB_SYSID=125", "DUMP_PATH='/var/log/x/%Y:a.tlog'\n"]
for t in good:
    try:
        L.parse_file(pf(t), rows)
        ok = True
    except L.ConfigError as e:
        ok = False
    chk(f"accepts {t.strip()[:40]!r}", ok)
badl = ["HB_SYSID=$(id)\n", "HB_SYSID=`id`\n", "HB_SYSID='$(id)'\n", 'HB_SYSID="$HOME"\n', "HB_SYSID=1; id\n", "HB_SYSID=1 ; id\n",
        "export HB_SYSID=1\n", "HB_SYSID=1 2\n", "hb_sysid=1\n", "HB_SYSID\n", "=1\n", "HB_SYSID='1\n", "HB_SYSID=1#c\n",
        "HB_SYSID=\\x41\n", ". /tmp/x\n", "id\n", "HB_SYSID=1\nHB_SYSID=2\n", "NOT_A_KEY=1\n", "HB_SYSID=(1)\n", "HB_SYSID=1\x01\n",
        "HB_SYSID='a'b'\n", "HB_SYSID=1 &\n", "HB_SYSID=1|id\n", "HB_SYSID=${X}\n"]
for t in badl:
    try:
        L.parse_file(pf(t), rows)
        ok = False
    except L.ConfigError:
        ok = True
    chk(f"rejects {t.strip()[:40]!r}", ok)
try:
    L.parse_file(pf("TX12_SYSID=1\n"), rows, "gs-mavlink")
    chk("extra file rejects a key of another owner", False)
except L.ConfigError:
    chk("extra file rejects a key of another owner", True)

# ---------------------------------------------------------------- 4. precedence
print("== precedence")
prof = os.path.join(td, "profiles")
os.makedirs(prof)
with open(os.path.join(prof, "p.env"), "w") as f:
    f.write("TX12_RATE_HZ=11\nTX12_DEADMAN_MS=111\nTX12_MAP='/p'\nTX12_SYSID=7\n")
host = pf("TX12_DEADMAN_MS=222\nTX12_MAP='/h'\nTX12_SYSID=8\n")
extra = os.path.join(td, "x.conf")
with open(extra, "w") as f:
    f.write("HB_SYSID=9\n")
base = {"SBC_GS_PROFILE": "p", "SBC_GS_PROFILE_DIR": prof, "SBC_GS_CONFIG": host}
c = res(base, extra_file=extra, extra_owner="gs-mavlink")
chk("profile only", c["TX12_RATE_HZ"] == 11.0 and c.src["TX12_RATE_HZ"] == "profile:p")
chk("host overrides profile", c["TX12_DEADMAN_MS"] == 222 and c.src["TX12_DEADMAN_MS"].startswith("host:"))
chk("extra file layer", c["HB_SYSID"] == 9 and c.src["HB_SYSID"].startswith("file:"))
c = res(dict(base, SBC_GS_TX12_SYSID="9", SBC_GS_HB_SYSID="10"), extra_file=extra, extra_owner="gs-mavlink")
chk("env overrides host/profile/extra", c["TX12_SYSID"] == 9 and c["HB_SYSID"] == 10 and c.src["HB_SYSID"] == "env:SBC_GS_HB_SYSID")
c = res(dict(base, SBC_GS_TX12_SYSID=""))
chk("empty env var counts as unset", c["TX12_SYSID"] == 8)
c = res(base, defaults_only=True)
chk("defaults_only ignores every layer", c["TX12_DEADMAN_MS"] == 300 and c.src["TX12_DEADMAN_MS"] == "default")
chk("unknown profile rejected", fails({"SBC_GS_PROFILE": "nope", "SBC_GS_PROFILE_DIR": prof}) is not None)
chk("profile name with a slash rejected", fails({"SBC_GS_PROFILE": "../x", "SBC_GS_PROFILE_DIR": prof}) is not None)
chk("legacy bare env name works for bench keys", res({"VIDEO_CODEC": "h265"})["VIDEO_CODEC"] == "h265")
chk("bare env name is NOT honoured for a tx12 key", res({"TX12_SYSID": "9"})["TX12_SYSID"] == 255)

# ---------------------------------------------------------------- 5. shell loader agrees
print("== shell loader == python loader")
SH = os.path.join(ROOT, "config", "sbc-gs-config")
PYCLI = [sys.executable, os.path.join(ROOT, "config", "load.py")]


def run(cmd, env):
    e = dict(CLEAN)
    e["SBC_GS_CONFIG"] = "/nonexistent/sbc-gs.env"
    e.update(env)
    p = subprocess.run(cmd, env=e, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


cases = [{}, {"SBC_GS_PROFILE": "p", "SBC_GS_PROFILE_DIR": prof, "SBC_GS_CONFIG": host},
         {"SBC_GS_TX12_DEADMAN_MS": "20"}, {"SBC_GS_TX12_DEADMAN_MS": "1001"}, {"SBC_GS_TX12_DEADMAN_MS": "1000"},
         {"SBC_GS_TX12_DEADMAN_MS": "abc"}, {"SBC_GS_I_KNOW": "1", "SBC_GS_TX12_DEADMAN_MS": "20"}, {"SBC_GS_TX12_THROTTLE_CH": "9"},
         {"SBC_GS_TX12_RATE_HZ": "0.05"}, {"SBC_GS_TX12_RATE_HZ": "7.5"}, {"SBC_GS_LISTEN_ADDR": "300.1.1.1"}, {"SBC_GS_LISTEN_ADDR": "127.0.0.1"},
         {"SBC_GS_ROUTER": "x"}, {"SBC_GS_TCP_PORT": "70000"}, {"SBC_GS_DUMP_PATH": "relative"}, {"SBC_GS_DUMP_PATH": "/a/../b"},
         {"VIDEO_CODEC": "h266"}, {"TX_PWR_IDX": "64"}, {"SBC_GS_TCP_ENABLE": "2"}, {"SBC_GS_FAKEFC_RC_OVERRIDE_TIME_S": "-1"},
         {"SBC_GS_APM_RC_OVERRIDE_TIME_S": "-1"}, {"SBC_GS_GS_FORWARD_IP": "1"}, {"GS_FORWARD_IP": "10.0.0.5"}, {"GS_FORWARD_IP": "10.0.0"}]
nd = 0
for env in cases:
    a = run([SH, "show"], env)
    b = run(PYCLI + ["show"], env)
    same = (a[0] == b[0]) and (a[0] != 0 or a[1] == b[1]) and (a[0] == 0 or a[2].replace("sbc-gs-config: error: ", "") == b[2].replace("sbc-gs-config: error: ", ""))
    if not same:
        nd += 1
        print("   diff:", env, a[0], b[0], a[2][:150], "|", b[2][:150])
chk(f"{len(cases)} override cases give the same verdict, output and message in both loaders", nd == 0)
a, b = run([SH, "show", "--defaults"], {}), run(PYCLI + ["show", "--defaults"], {})
chk("show --defaults identical (shell vs python)", a[0] == 0 and a == b)

# the shell parser must agree with the python parser on every accepted/rejected line (a loader that executes or accepts shell syntax is S1)
nd = 0
for t in good + badl:
    try:
        L.parse_file(pf(t), rows)
        want_ok = True
    except L.ConfigError:
        want_ok = False
    got = run([SH, "check", "--syntax", pf(t)], {})[0] == 0
    if got != want_ok:
        nd += 1
        print("   parser diff:", repr(t), "python ok=", want_ok, "shell ok=", got)
chk(f"shell parser agrees with python parser on {len(good) + len(badl)} lines", nd == 0)

# ---------------------------------------------------------------- 6. legacy constants through the real tools
print("== no overrides == legacy constants (tools)")


def stub_pymavlink():
    pm = types.ModuleType("pymavlink")
    mu = types.ModuleType("pymavlink.mavutil")
    mu.mavlink = types.SimpleNamespace()
    pm.mavutil = mu
    sys.modules["pymavlink"] = pm
    sys.modules["pymavlink.mavutil"] = mu


stub_pymavlink()
sys.path.insert(0, os.path.join(ROOT, "tests", "sim"))
tx = load_mod("tx12_bridge", "bench/tx12_bridge.py")
a = tx.parse_args(["--input", "sweep", "--confirm-props-off"])
want = dict(conn="udpout:127.0.0.1:14550", sysid=255, rate=20.0, max_rate=50.0, deadman_ms=300, throttle_ch=3, failsafe_throttle=1000)
chk("tx12_bridge CLI defaults", all(getattr(a, k) == v for k, v in want.items()), str({k: getattr(a, k) for k in want}))
chk("tx12_bridge module constants (clamp 1000-2000, sane 0-4000, release 1 s, 3 failsafe frames)",
    (tx.LO, tx.HI, tx.SANE_MIN, tx.SANE_MAX, tx.RELEASE_HOLD_S, tx.THROTTLE_FS_FRAMES, tx.EXIT_RELEASE_FRAMES, tx.EVDEV_POLL_S)
    == (1000, 2000, 0, 4000, 1.0, 3, 5, 0.05))
c = a.cfg
chk("tx12_bridge loop constants (component 190, hb 1 s, stat 2 s, sleep 5 ms)",
    (c["TX12_COMPONENT_ID"], c["TX12_HB_PERIOD_S"], c["TX12_STAT_PERIOD_S"], c["TX12_LOOP_SLEEP_S"]) == (190, 1.0, 2.0, 0.005))
chk("tx12_bridge lock path = /run/lock or temp dir", a.lock == tx.default_lock())
chk("tx12_bridge map = bundled example", a.map.endswith("bench/tx12_map.example.json"))
for bad_args, label in ((["--deadman-ms", "20"], "--deadman-ms 20"), (["--max-rate", "500"], "--max-rate 500"),
                        (["--failsafe-throttle", "1500"], "--failsafe-throttle 1500"), (["--rate", "60"], "--rate 60 > --max-rate")):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            tx.parse_args(["--input", "sweep", "--confirm-props-off"] + bad_args)
        chk(f"tx12_bridge refuses {label}", False)
    except SystemExit as e:
        chk(f"tx12_bridge refuses {label}", e.code == 2)
try:
    with contextlib.redirect_stderr(io.StringIO()):
        a2 = tx.parse_args(["--input", "sweep", "--confirm-props-off", "--deadman-ms", "20", "--i-know"])
    chk("tx12_bridge --i-know accepts --deadman-ms 20", a2.deadman_ms == 20)
except SystemExit:
    chk("tx12_bridge --i-know accepts --deadman-ms 20", False)
tx.configure(L.resolve(["tx12"], defaults_only=True))
chk("tx12_bridge sanitize/clamp unchanged", tx.sanitize([2500, 500]) == [2000, 1000] + [65535] * 6 and tx.sanitize([1500, 5000]) is None)
os.environ["SBC_GS_TX12_CLAMP_HI_US"] = "1900"
a3 = tx.parse_args(["--input", "sweep", "--confirm-props-off"])
chk("env override reaches the tool (clamp hi 1900)", tx.sanitize([2500]) [0] == 1900 and a3.cfg.src["TX12_CLAMP_HI_US"].startswith("env:"))
del os.environ["SBC_GS_TX12_CLAMP_HI_US"]
tx.configure(L.resolve(["tx12"], defaults_only=True))

gm = load_mod("gs_mav", "bench/gs_mav.py")
a = gm.parse_args([])
chk("gs_mav defaults (conn, rc off, 20 Hz)", (a.conn, a.rc, a.rate) == ("udpin:127.0.0.1:14550", "off", 20.0))
g = a.cfg
chk("gs_mav constants (sysid 255, comp 190, neutral 1500, amp 400, release 5 x 0.05 s, window 40)",
    (g["GSMAV_SYSID"], g["GSMAV_COMPONENT_ID"], g["GSMAV_NEUTRAL_US"], g["GSMAV_SWEEP_AMP_US"], g["GSMAV_RELEASE_REPEATS"],
     g["GSMAV_RELEASE_INTERVAL_S"], g["GSMAV_RTT_WINDOW"]) == (255, 190, 1500, 400, 5, 0.05, 40))
try:
    with contextlib.redirect_stderr(io.StringIO()):
        gm.parse_args(["--rate", "500"])
    chk("gs_mav refuses --rate 500 (audit: no rate limit)", False)
except SystemExit:
    chk("gs_mav refuses --rate 500 (audit: no rate limit)", True)

ff = load_mod("fake_fc", "bench/fake_fc.py")
a = ff.parse_args([])
chk("fake_fc defaults (conn, sysid 1, rc-override 3 s, gcs-timeout 5 s)",
    (a.conn, a.sysid, a.rc_override_time, a.gcs_timeout) == ("udpout:127.0.0.1:14550", 1, 3.0, 5.0))
f = a.cfg
chk("fake_fc constants (gcs sysid 255, comp 1, periods 1 s / 0.1 s / 5 s, sleep 5 ms)",
    (f["FAKEFC_GCS_SYSID"], f["FAKEFC_COMPONENT_ID"], f["FAKEFC_SLOW_PERIOD_S"], f["FAKEFC_FAST_PERIOD_S"], f["FAKEFC_REPORT_PERIOD_S"],
     f["FAKEFC_LOOP_SLEEP_S"]) == (255, 1, 1.0, 0.1, 5.0, 0.005))

# tests/sim tools: parse defaults by running them with --help is not enough; read the loader values they use
s = L.resolve(["sim"], defaults_only=True)
chk("apm_fc defaults (3.0 s, fs-gcs 0/5 s, rc-fs 1 s, gcs sysid 255/0)",
    (s["APM_CONN"], s["APM_SYSID"], s["APM_RC_OVERRIDE_TIME_S"], s["APM_FS_GCS_ENABLE"], s["APM_FS_GCS_TIMEOUT_S"], s["APM_RC_FS_TIMEOUT_S"],
     s["APM_MAV_GCS_SYSID"], s["APM_MAV_GCS_SYSID_HI"]) == ("udpout:127.0.0.1:14550", 1, 3.0, 0, 5.0, 1.0, 255, 0))
chk("video_latency defaults (port 15600, 90 frames, 30 fps, 640x360, 2000 kbps, p95 500 ms, lost 3)",
    (s["SIM_LAT_PORT"], s["SIM_LAT_FRAMES"], s["SIM_LAT_FPS"], s["SIM_LAT_SIZE"], s["SIM_LAT_BITRATE_KBPS"], s["SIM_LAT_MAX_P95_MS"], s["SIM_LAT_MAX_LOST"])
    == (15600, 90, 30, "640x360", 2000, 500.0, 3))
chk("udp_probe defaults (200 packets, 100 pps, 1000 B, 1 s settle, 0.99)",
    (s["SIM_UDP_COUNT"], s["SIM_UDP_RATE_PPS"], s["SIM_UDP_SIZE"], s["SIM_UDP_SETTLE_S"], s["SIM_UDP_MIN_DELIVERY"]) == (200, 100.0, 1000, 1.0, 0.99))
chk("air_relay defaults (loss 0, delay 0, jitter 0, seed 1, -50 dBm)",
    (s["RELAY_LOSS"], s["RELAY_DELAY_MS"], s["RELAY_JITTER_MS"], s["RELAY_SEED"], s["RELAY_RX_SIGNAL_DBM"]) == (0.0, 0.0, 0.0, 1, -50))
b = L.resolve(["bench"], defaults_only=True)
chk("bench defaults (h264 1280x720@30 4000 kbps, ports 5602/5600/14550, ch 165, FC sysid 1, pt 96 mtu 1400)",
    (b["VIDEO_CODEC"], b["VIDEO_W"], b["VIDEO_H"], b["VIDEO_FPS"], b["VIDEO_BITRATE_KBPS"], b["AIR_VIDEO_PORT"], b["GS_VIDEO_PORT"], b["MAV_PORT"],
     b["WFB_CHANNEL"], b["FC_SYSID"], b["VIDEO_RTP_PT"], b["VIDEO_RTP_MTU"]) == ("h264", 1280, 720, 30, 4000, 5602, 5600, 14550, 165, 1, 96, 1400))

print(f"unit failures: {bad}")
sys.exit(1 if bad else 0)
