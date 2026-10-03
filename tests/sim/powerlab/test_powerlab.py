#!/usr/bin/env python3
"""Tests of the virtual power lab: parsers of the real instrument formats, virtual instruments, bench/doctor.sh with shims,
bench/ingest.py (report, overlay, contradictions) and the whole pipeline scenario -> logs -> doctor -> ingest -> overlay -> calib whatif.

All numbers are SYNTH (generated from a model): these tests check the tools, they prove nothing about hardware.
Run: tests/sim/powerlab/run.sh --check   (stdlib unittest, no network, no root, < 15 s)
"""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MODELS = os.path.join(ROOT, "tests", "sim", "models")
BENCH = os.path.join(ROOT, "bench")
for p in (BENCH, MODELS, HERE):
    sys.path.insert(0, p)
for _k in ("MODEL_PARAMS", "MODEL_MEASURED", "MODEL_SET", "DEGRADE_PARAMS", "DEGRADE_MEASURED", "DEGRADE_SET"):
    os.environ.pop(_k, None)
import gen  # noqa: E402
import sensitivity  # noqa: E402
import ingest  # noqa: E402
import calib  # noqa: E402
import common  # noqa: E402
import power_model  # noqa: E402

TMP = tempfile.mkdtemp(prefix="powerlab-")
SCEN = os.path.join(HERE, "scenarios")
PIPE = os.path.join(HERE, "pipeline.sh")
NOW = "2026-10-08T00:00:00Z"
FIX = {}  # scenario name -> output dir


def run_pipeline(name, extra=()):
    out = os.path.join(TMP, name)
    r = subprocess.run([PIPE, os.path.join(SCEN, name + ".json"), out] + list(extra), capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return name, out, r


def setUpModule():
    jobs = [("calib_pi5_5a", ("--", "--now", NOW)), ("calib_pi5_hot_adapter", ("--whatif", "nominal_pi5_5a_150m", "--n", "100", "--", "--now", NOW)), ("uv_ramp_pi5", ("--", "--now", NOW)),
            ("claim_5a_actual_3a", ("--", "--now", NOW)), ("hot_soc", ("--", "--now", NOW))]
    with concurrent.futures.ThreadPoolExecutor(len(jobs)) as ex:
        for name, out, r in ex.map(lambda j: run_pipeline(*j), jobs):
            if r.returncode:
                raise RuntimeError("pipeline %s failed (%d): %s%s" % (name, r.returncode, r.stdout, r.stderr))
            FIX[name] = out


def load(name, f):
    with open(os.path.join(FIX[name], f), encoding="utf-8") as fh:
        return json.load(fh) if f.endswith(".json") else fh.read()


def overlay(name):
    return load(name, "overlay.json")


def val(name, key):
    e = overlay(name)[key]
    return None if e is None else e["value"]


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_ingest(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ingest.main(argv)
    return rc, out.getvalue(), err.getvalue()


def write(name, text):
    p = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def doctor_doc(**kw):
    d = {"schema": "sbc-gs-doctor/1", "tag": "HW", "utc": "2026-10-08T00:00:00Z", "board_model": "Raspberry Pi 5 Model B Rev 1.0"}
    d.update(kw)
    return write("doc/%s.json" % hashlib.md5(json.dumps(d, sort_keys=True).encode()).hexdigest()[:8], json.dumps(d))


def finding_ids(report_json_path, sev=None):
    with open(report_json_path, encoding="utf-8") as f:
        rep = json.load(f)
    return {x["id"] for x in rep["findings"] if sev is None or x["severity"] == sev}


def ingest_findings(argv):
    rj = os.path.join(TMP, "rep-%d.json" % abs(hash(tuple(argv))))
    rc, _o, _e = run_ingest(argv + ["--now", NOW, "--report-json", rj])
    return rc, finding_ids(rj), rj


# ---------------------------------------------------------------- formats of the real tools
class TestFormats(unittest.TestCase):
    def test_throttled_bits_match_the_documented_table_and_the_model(self):
        # SRC raspberrypi/documentation graphics-utilities.adoc (get_throttled); power_model.py carries the same table
        bits = {int(k): v for k, v in ingest.RULES["throttled_bits"].items() if k.isdigit()}
        self.assertEqual(bits, power_model.THROTTLED_BITS)
        roles = ingest.ROLE
        self.assertEqual((roles["uv_now"], roles["throttled_now"], roles["soft_now"], roles["uv_ever"], roles["throttled_ever"], roles["soft_ever"]), (0, 2, 3, 16, 18, 19))
        self.assertEqual(ingest.bit(roles["uv_ever"]), 0x10000)
        self.assertEqual(ingest.bit(roles["soft_ever"]), 0x80000)

    def test_vcgencmd_sample_outputs(self):
        # SNIP: lines as printed in third-party files (geerlingguy blog, homebridge-rpi README), see ingest-rules.json
        log = ("=== 2026-10-06T09:00:00.000Z\nthrottled=0x50005\ntemp=39.5'C\nEXT5V_V volt(24)=5.10540000V\nframe\n"
               "=== 2026-10-06T09:00:00.200Z\nthrottled=0x0\ntemp=52.5'C\n 3V7_WL_SW_A current(0)=0.00780744A\n   3V3_SYS_A current(1)=0.12199130A\n  VDD_CORE_A current(7)=0.75012000A\n"
               "EXT5V_V volt(24)=4.91085000V\nfrequency(48)=1800457088\n")
        s = ingest.parse_instr_log(log)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]["throttled"], 0x50005)
        self.assertAlmostEqual(s[0]["temp_c"], 39.5)
        self.assertAlmostEqual(ingest.ext5v(s[0]), 5.1054)
        self.assertEqual(s[1]["pmic"]["VDD_CORE_A"], ("current", 7, 0.75012))
        self.assertEqual(s[1]["arm_hz"], 1800457088)
        self.assertAlmostEqual(ingest.ext5v(s[1]), 4.91085)

    def test_bare_throttled_log_without_blocks(self):
        s = ingest.parse_instr_log("throttled=0x0\nthrottled=0x10000\nthrottled=0x50005\n")
        self.assertEqual([x["throttled"] for x in s], [0, 0x10000, 0x50005])
        self.assertTrue(all(x["t"] is None for x in s))

    def test_dmesg_kernel_lines(self):
        # SRC hub.c / raspberrypi-hwmon.c message strings; the device prefixes are the dev_printk convention (INF)
        text = ("[ 1234.567890] hwmon hwmon1: Undervoltage detected!\n[ 1236.000000] hwmon hwmon1: Voltage normalised\n"
                "[ 1300.100000] usb 1-1: USB disconnect, device number 4\n[ 1303.200000] usb 1-1: new high-speed USB device number 5 using xhci-hcd\n"
                "[ 1303.210000] usb 1-1: New USB device found, idVendor=0bda, idProduct=8812, bcdDevice= 0.00\n[ 1304.000000] usb usb1-port1: over-current condition\n"
                "[ 1305.000000] usb 1-1: device descriptor read/64, error -71\n[ 1306.000000] usb 1-1: unable to enumerate USB device\n[ 1307.000000] unrelated line\n")
        ev = ingest.parse_dmesg(text, boot=1000.0)
        self.assertEqual([e["kind"] for e in ev], ["uv_detected", "uv_normalised", "usb_disconnect", "usb_new", "usb_found", "usb_overcurrent", "usb_enum_fail", "usb_enum_fail"])
        self.assertEqual(ev[2]["path"], "1-1")
        self.assertEqual(ev[2]["devnum"], 4)
        self.assertEqual(ev[3]["speed"], "high-speed")
        self.assertEqual(ev[4]["vid_pid"], "0bda:8812")
        self.assertAlmostEqual(ev[0]["t"], 1000.0 + 1234.56789, places=4)
        self.assertEqual(ingest.parse_dmesg("[ 1.0] Under-voltage detected! (0x00050005)")[0]["kind"], "uv_detected")

    def test_dmesg_time_forms(self):
        t1 = ingest.parse_dmesg("[Tue Oct  6 09:00:00 2026] usb 1-1: USB disconnect, device number 4")[0]["t"]
        t2 = ingest.parse_dmesg("2026-10-06T09:00:00+0000 pi kernel: usb 1-1: USB disconnect, device number 4".replace("pi kernel: ", ""))
        self.assertEqual(t1, ingest.parse_iso("2026-10-06T09:00:00Z"))
        self.assertEqual(len(t2), 1)
        self.assertEqual(t2[0]["t"], t1)
        self.assertIsNone(ingest.parse_dmesg("[ 5.0] usb 1-1: USB disconnect, device number 4")[0]["t"])  # kernel clock without --boot-utc: no wall time

    def test_lsusb_tree(self):
        # SRC usbutils lsusb-t.c printf formats
        text = ("/:  Bus 001.Port 001: Dev 001, Class=root_hub, Driver=xhci_hcd/4p, 480M\n    |__ Port 001: Dev 002, If 0, Class=Vendor Specific Class, Driver=rtl88xxau, 480M\n"
                "    |__ Port 002: Dev 003, If 0, Class=Hub, Driver=hub/4p, 480M\n        |__ Port 001: Dev 004, If 0, Class=Communications, Driver=cdc_acm, 12M\n"
                "/:  Bus 002.Port 001: Dev 001, Class=root_hub, Driver=xhci_hcd/1p, 5000M\n")
        buses, devs = ingest.parse_lsusb_t(text)
        self.assertEqual([b["bus"] for b in buses], [1, 2])
        self.assertEqual(buses[0]["ports"], 4)
        self.assertEqual(buses[1]["mbps"], "5000")
        self.assertEqual([d["driver"] for d in devs], ["rtl88xxau", "hub", "cdc_acm"])
        self.assertEqual(devs[2]["mbps"], "12")

    def test_meter_csv_variants(self):
        gen_series = write("m/a.csv", "# model X export\nTime,Voltage (V),Current (mA)\n2026-10-06T09:00:00Z,5.01,450\n2026-10-06T09:00:01Z,5.00,460\n")
        kind, s = ingest.read_meter(gen_series)
        self.assertEqual(kind, "series")
        self.assertAlmostEqual(s[0][2], 0.45)
        self.assertAlmostEqual(s[1][1], 5.0)
        semi = write("m/b.csv", "t_s;vbus;amps\n10;4.9;0.5\n11;4.9;0.6\n")
        _k, s = ingest.read_meter(semi, base=ingest.parse_iso("2026-10-06T09:00:00Z"))
        self.assertAlmostEqual(s[0][0], ingest.parse_iso("2026-10-06T09:00:10Z"))
        self.assertAlmostEqual(s[1][2], 0.6)
        grouped = write("m/c.csv", "device,state,amps\nrtl8812,tx,0.9\nrtl8812,tx,1.0\nfc,,0.1\n")
        k, g = ingest.read_meter(grouped)
        self.assertEqual(k, "grouped")
        self.assertEqual(sorted(g), ["fc", "rtl8812:tx"])
        with self.assertRaises(ingest.InputError):
            ingest.read_meter(write("m/d.csv", "foo,bar\n1,2\n"))
        mv = write("m/e.csv", "Time,Voltage (mV),Current (A)\n2026-10-06T09:00:00Z,4900,0.5\n")
        self.assertAlmostEqual(ingest.read_meter(mv)[1][0][1], 4.9)
        over = write("m/f.csv", "ts,V,I\n2026-10-06T09:00:00Z,4.9,450\n")
        self.assertAlmostEqual(ingest.read_meter(over, i_unit="mA")[1][0][2], 0.45)

    def test_wfb_json_lines(self):
        # SRC wfb_ng/protocols.py StatisticsJSONProtocol: settings first, then rx/tx objects, one per line
        lines = [{"type": "settings", "settings": {"common": {"log_interval": 1000}}},
                 {"type": "rx", "id": "video", "packets": {"all": [100, 100], "out": [90, 90], "fec_rec": [2, 2], "lost": [0, 0], "dec_err": [0, 0], "bad": [0, 0]},
                  "rx_ant_stats": [{"ant": 0, "freq": 5805, "mcs": 1, "bw": 20, "pkt_recv": 100, "rssi_min": -60, "rssi_avg": -58, "rssi_max": -55, "snr_min": 20, "snr_avg": 25, "snr_max": 30}]},
                 {"type": "rx", "id": "video", "packets": {"all": [0, 100], "lost": [50, 50]}, "rx_ant_stats": []},
                 {"type": "tx", "id": "mavlink", "packets": {"injected": [10, 10], "dropped": [1, 1]}}]
        p = write("w/a.jsonl", "\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        w = ingest.parse_wfb(p, "2026-10-06T09:00:00Z")
        self.assertEqual(w["interval_ms"], 1000)
        self.assertEqual(w["rx"]["totals"]["lost"], 50)
        self.assertEqual(w["rx"]["totals"]["fec_rec"], 2)
        self.assertEqual(w["tx"]["dropped"], 1)
        self.assertEqual(w["rx"]["loss_t"], [ingest.parse_iso("2026-10-06T09:00:01Z")])
        self.assertEqual(w["rx"]["ants"][0]["rssi"], [-58])

    def test_hwmon_units_and_labels(self):
        # SRC ABI sysfs-class-hwmon: in = millivolt, curr = milliampere, temp = millidegree, power = microWatt
        hw = ingest.kv_lines("hwmon2/name=ina3221\nhwmon2/in1_label=VBUS5V\nhwmon2/in1_input=4950\nhwmon2/curr1_input=1500\nhwmon2/power1_input=7000000\nhwmon0/temp1_input=45500\n")
        r = {(x["chip"], x["attr"]): x for x in ingest.hwmon_rails(hw)}
        self.assertAlmostEqual(r[("hwmon2", "in1_input")]["value"], 4.95)
        self.assertEqual(r[("hwmon2", "in1_input")]["label"], "VBUS5V")
        self.assertAlmostEqual(r[("hwmon2", "curr1_input")]["value"], 1.5)
        self.assertAlmostEqual(r[("hwmon2", "power1_input")]["value"], 7.0)
        self.assertAlmostEqual(r[("hwmon0", "temp1_input")]["value"], 45.5)

    def test_old_doctor_json_without_new_fields(self):
        d = {"schema": "sbc-gs-doctor/1", "tag": "HW", "utc": "2026-10-06T09:00:00Z", "kernel": "6.12", "board_model": None, "get_throttled": "throttled=0x0", "soc_temp": None}
        rc, _o, _e = run_ingest(["--doctor", write("old.json", json.dumps(d)), "--now", NOW])
        self.assertEqual(rc, 0)
        rc, _o, e = run_ingest(["--doctor", write("bad.json", json.dumps({"schema": "other/1"})), "--now", NOW])
        self.assertEqual(rc, 2)
        self.assertIn("sbc-gs-doctor/1", e)

    def test_device_tree_power_properties(self):
        # SRC power-supplies.adoc names the properties; the big-endian u32 encoding is INF/UNVERIFIED
        self.assertEqual(ingest.hex_bytes_to_int("00001388"), 5000)
        self.assertIsNone(ingest.hex_bytes_to_int("000013"))
        self.assertIsNone(ingest.hex_bytes_to_int(""))


# ---------------------------------------------------------------- virtual instruments
class TestGenerator(unittest.TestCase):
    def sc(self, name, dur=None):
        with open(os.path.join(SCEN, name + ".json"), encoding="utf-8") as f:
            sc = json.load(f)
        if dur:  # keep the test fast: every phase this long (the v_ramp is compressed with it)
            for ph in sc["phases"]:
                ph["dur_s"] = dur
        return sc

    def test_deterministic_per_seed(self):
        a, b, c = (os.path.join(TMP, "det-%s" % x) for x in "abc")
        sc = self.sc("claim_5a_actual_3a", 10)
        gen.generate(sc, a, 3)
        gen.generate(sc, b, 3)
        gen.generate(sc, c, 4)

        def digest(d):
            blobs = []
            for f in ("pmic.log", "dmesg.txt", "meter_psu.csv"):
                with open(os.path.join(d, f), "rb") as fh:
                    blobs.append(fh.read())
            return hashlib.sha256(b"".join(blobs)).hexdigest()
        self.assertEqual(digest(a), digest(b))
        self.assertNotEqual(digest(a), digest(c))

    def test_static_state_matches_power_model(self):
        # the generator uses power_model formulas: a static RX state must sit at power_model.budget()'s board voltage
        sc = self.sc("calib_pi5_5a")
        sc["phases"] = [{"name": "rx", "dur_s": 10, "load": "active", "devices": ["rtl8812", "fc"], "adapter": "rx"}]
        out = os.path.join(TMP, "static")
        sim = gen.generate(sc, out, 1)
        P = common.load({k: v for k, v in sc["truth"].items() if k.startswith("power.")})
        b = power_model.budget(P, "pi5", sc["psu_a"], 1, "rx", ("fc",), "active", False, True)
        v = [x[3] for x in sim.pmic]
        self.assertAlmostEqual(sum(v) / len(v), b["v_board"], delta=0.01)

    def test_kernel_log_consistency(self):
        text = load("claim_5a_actual_3a", "dmesg.txt")
        ev = ingest.parse_dmesg(text, boot=0.0)
        ts = [e["t"] for e in ev]
        self.assertEqual(ts, sorted(ts))
        up = {}
        for e in ev:
            if e["kind"] == "usb_new":
                self.assertFalse(up.get(e["path"]), "new while up: %s" % e["msg"])
                up[e["path"]] = True
            elif e["kind"] == "usb_disconnect":
                self.assertTrue(up.get(e["path"]), "disconnect while down: %s" % e["msg"])
                up[e["path"]] = False
        uv = [e["kind"] for e in ev if e["kind"] in ("uv_detected", "uv_normalised")]
        self.assertTrue(all(a != b for a, b in zip(uv, uv[1:])))
        self.assertTrue(any(e["kind"] == "usb_overcurrent" for e in ev))
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(len(lines), len(ev), "every generated kernel line must be parseable")

    def test_sticky_bits_cleared_by_the_kernel_poller(self):
        # SRC raspberrypi-hwmon.c: the poller asks the firmware to clear sticky bits every 2 s -> bit 16 does not stay set after the dip
        sc = self.sc("uv_ramp_pi5", 30)
        sc["phases"][1]["v_ramp"] = [5.1, 4.45]
        thr = [x[1] for x in gen.generate(sc, os.path.join(TMP, "sticky"), 1).pmic]
        self.assertTrue(any(t & 1 for t in thr))
        sc["kernel_clears_sticky"] = False
        thr2 = [x[1] for x in gen.generate(sc, os.path.join(TMP, "sticky2"), 1).pmic]
        self.assertGreater(sum(1 for t in thr2 if t & 0x10000), sum(1 for t in thr if t & 0x10000))

    def test_truth_file_and_scenario_schema(self):
        t = load("calib_pi5_5a", "truth.json")
        self.assertEqual(t["tag"], "SYNTH")
        self.assertAlmostEqual(t["values"]["power.cable_resistance_ohm"], 0.18)
        bad = write("badscn.json", json.dumps({"schema": "x"}))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gen.main([bad, "--out", os.path.join(TMP, "nowhere")]), 2)


class TestShimsAndDoctor(unittest.TestCase):
    def env(self, name, **kw):
        e = dict(os.environ, PATH=os.path.join(HERE, "shims") + os.pathsep + os.environ["PATH"], POWERLAB_FIXTURE=FIX[name], DOCTOR_ROOT=os.path.join(FIX[name], "sysroot"))
        e.update(kw)
        return e

    def test_vcgencmd_shim(self):
        e = self.env("calib_pi5_5a")
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, env=e)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "throttled=0x0"))
        r = subprocess.run(["vcgencmd", "pmic_read_adc", "EXT5V_V"], capture_output=True, text=True, env=e)
        self.assertRegex(r.stdout, r"^EXT5V_V volt\(24\)=\d\.\d+V$")
        r = subprocess.run(["vcgencmd", "no_such_command"], capture_output=True, text=True, env=e)
        self.assertEqual((r.returncode, r.stdout), (1, ""))

    def test_dmesg_shim_restricted(self):
        r = subprocess.run(["dmesg"], capture_output=True, text=True, env=self.env("calib_pi5_5a", POWERLAB_DMESG_RC="1"))
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("Operation not permitted", r.stderr)

    def test_doctor_json_keeps_the_old_fields_and_adds_new_ones(self):
        d = load("calib_pi5_5a", "doctor.json")
        old = ["schema", "tag", "utc", "kernel", "pagesize", "board_model", "get_throttled", "soc_temp", "pi5_pmic_5v_rail", "usb_max_current_enable", "thermal_zone0_mC", "lsusb_tree",
               "usb_radio_fc_candidates", "iw_monitor_capable", "iw_dev", "wifi_modules", "gpioinfo_head", "dmesg_usb_power_events", "serial_and_joystick", "systemd_state"]
        new = ["dmesg_rc", "dmesg_power_lines", "pi5_pmic_adc_full", "arm_clock", "dt_chosen_power", "uptime_s", "cpu0_cur_freq_khz", "thermal_zones", "hwmon", "usb_sysfs",
               "usb_port_over_current_count"]
        for k in old + new:
            self.assertIn(k, d, k)
        self.assertEqual(d["schema"], "sbc-gs-doctor/1")
        self.assertEqual(d["get_throttled"], "throttled=0x0")
        self.assertIn("EXT5V_V", d["pi5_pmic_5v_rail"])
        self.assertIn("max_current=00001388", d["dt_chosen_power"])
        self.assertEqual(d["dmesg_rc"], "0")
        self.assertIn("ina3221", d["hwmon"])

    def test_doctor_reports_an_unreadable_kernel_log(self):
        out = os.path.join(TMP, "doc-restricted.json")
        r = subprocess.run([os.path.join(BENCH, "doctor.sh"), out], capture_output=True, text=True, env=self.env("calib_pi5_5a", POWERLAB_DMESG_RC="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        d = jload(out)
        self.assertEqual(d["dmesg_rc"], "1")
        self.assertIsNone(d["dmesg_power_lines"])
        self.assertEqual(d["dmesg_usb_power_events"], "0")  # the old counter says 0: the new rc is what tells it is not evidence
        rc, ids, _ = ingest_findings(["--doctor", out])
        self.assertIn("K7", ids)

    def test_doctor_stays_read_only(self):
        with open(os.path.join(BENCH, "doctor.sh"), encoding="utf-8") as f:
            src = f.read()
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        for bad in ("modprobe", "insmod", "rmmod", "iw dev .* set", "ip link set", "iwconfig", "wfb_tx", "tcpdump", "sudo", "rm -", " > /sys", " > /proc", "dd "):
            self.assertIsNone(re.search(bad, code), "doctor.sh must stay read-only, found %r" % bad)
        self.assertIn("set -u", code)


# ---------------------------------------------------------------- ingest end to end
class TestPipeline(unittest.TestCase):
    def assertNear(self, name, key, truth_key, tol):
        v = val(name, key)
        self.assertIsNotNone(v, "%s: %s not derived" % (name, key))
        t = load(name, "truth.json")["values"][truth_key]
        self.assertAlmostEqual(v, t, delta=tol, msg="%s %s truth %s" % (name, key, t))

    def test_healthy_calibration_recovers_the_truth(self):
        n = "calib_pi5_5a"
        for key, tol in (("power.devices.rtl8812_idle_a", 0.01), ("power.devices.rtl8812_rx_a", 0.015), ("power.devices.rtl8812_tx_a", 0.03), ("power.cable_resistance_ohm", 0.01),
                         ("usb.cable_r_ohm", 0.02), ("power.boards.pi5.board_idle_a", 0.02), ("power.boards.pi5.board_load_a", 0.02), ("hw.soc_rise_c", 1.5), ("ext.ambient_c", 0.01),
                         ("power.tx_peak_factor", 0.1)):
            self.assertNear(n, key, key, tol)
        ov = overlay(n)
        self.assertLessEqual(ov["power.tx_peak_factor"]["value"], load(n, "truth.json")["values"]["power.tx_peak_factor"] + 0.02)  # lower bound by construction
        self.assertIn("lower bound", ov["power.tx_peak_factor"]["note"])
        for key in ("power.devices.fc_usb_a", "power.devices.fan_a", "power.undervolt_threshold_v", "power.usb_dropout_v", "power.usb_reenum_s", "hw.soc_soft_limit_c", "usb.drop_rate_per_h",
                    "power.boards.pi5.board_active_a"):
            self.assertIsNone(ov[key], key)
        rep = load(n, "report.json")
        self.assertEqual([f for f in rep["findings"] if f["severity"] == "CONTRADICTION"], [])
        self.assertTrue(overlay(n)["power.cable_resistance_ohm"]["min"] <= 0.18 <= overlay(n)["power.cable_resistance_ohm"]["max"] + 0.005)

    def test_overlay_entries_carry_source_value_and_time(self):
        for n in FIX:
            for k, e in overlay(n).items():
                if e is None:
                    continue
                self.assertIsInstance(e["value"], float, k)
                self.assertTrue(e["source"], k)
                self.assertRegex(e["measured_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", k)
                self.assertIn(e["measured_utc"], e["source"], k)
                self.assertIn(e["tag"], ("HW",), k)
                if "min" in e and "max" in e:
                    self.assertLessEqual(e["min"], e["max"] + 1e-9, k)

    def test_overlay_is_accepted_by_calib_without_warnings_and_keys_exist(self):
        for n in FIX:
            model, degrade, flagged = calib.split_overlay(os.path.join(FIX[n], "overlay.json"))
            self.assertEqual(flagged, [], n)
            self.assertGreaterEqual(len(model) + len(degrade), 1, n)
            self.assertEqual(ingest.validate_keys(overlay(n), MODELS), [], n)

    def test_uv_ramp_brackets_contain_the_thresholds(self):
        n = "uv_ramp_pi5"
        ov = overlay(n)
        for key in ("power.undervolt_threshold_v", "power.usb_dropout_v"):
            e = ov[key]
            self.assertIsNotNone(e, key)
            t = load(n, "truth.json")["values"][key]
            self.assertAlmostEqual(e["value"], t, delta=0.03, msg=key)
            self.assertLessEqual(e["min"] - 0.01, t, key)
            self.assertGreaterEqual(e["max"] + 0.01, t, key)
        self.assertIsNone(ov["power.usb_reenum_s"])  # the drop was caused by undervoltage: its delay includes the supply recovery
        rep = load(n, "report.json")
        self.assertTrue(any("usb_reenum_s" in x for x in rep["notes"]))
        self.assertEqual(sorted(f["id"] for f in rep["findings"] if f["severity"] == "CONTRADICTION"), [])

    def test_claim_contradictions_are_found(self):
        n = "claim_5a_actual_3a"
        rep = load(n, "report.json")
        ids = {f["id"] for f in rep["findings"] if f["severity"] == "CONTRADICTION"}
        self.assertEqual(ids, {"K1", "K2", "K3"})
        self.assertEqual(rep["scenario_cfg_hint"]["psu_a"], 3.0)
        self.assertFalse(rep["scenario_cfg_hint"]["usb_max_current"])
        # over-current trips give automatic re-enumerations: this is what usb_reenum_s may use
        self.assertNear(n, "power.usb_reenum_s", "power.usb_reenum_s", 0.2)
        self.assertNear(n, "power.cable_resistance_ohm", "power.cable_resistance_ohm", 0.01)
        # no value for the TX current: the radio was down for almost the whole TX window (no samples of the device being up)
        self.assertIsNone(val(n, "power.devices.rtl8812_tx_a"))
        # wfb loss seconds fall inside the USB drops
        w = rep["wfb"]
        self.assertGreater(w["loss_seconds"], 0)
        self.assertGreaterEqual(w["loss_seconds_inside_usb_drops"], w["loss_seconds"] - 2)
        rc = subprocess.run([sys.executable, os.path.join(BENCH, "ingest.py"), "--doctor", os.path.join(FIX[n], "doctor.json"), "--instr-log", os.path.join(FIX[n], "pmic.log"), "--claim-psu-a", "5",
                             "--strict", "--now", NOW], capture_output=True, text=True).returncode
        self.assertEqual(rc, 1)

    def test_hot_soc_soft_limit_bracket(self):
        n = "hot_soc"
        e = overlay(n)["hw.soc_soft_limit_c"]
        self.assertIsNotNone(e)
        self.assertAlmostEqual(e["value"], 80.0, delta=1.0)
        self.assertLessEqual(e["min"] - 0.3, 80.0)
        self.assertGreaterEqual(e["max"] + 0.3, 80.0)
        self.assertNear(n, "hw.soc_rise_c", "hw.soc_rise_c", 1.5)
        self.assertIn("passed", overlay(n)["hw.soc_rise_c"]["note"])

    def test_whatif_moves_in_the_expected_direction(self):
        # the bench found a 1.5 A adapter (prior 0.9 A): the model must now predict USB trips and a worse link on a Pi 5 with a 1.6 A budget
        n = "calib_pi5_hot_adapter"
        self.assertAlmostEqual(val(n, "power.devices.rtl8812_tx_a"), 1.5, delta=0.05)
        rows = {}
        for line in load(n, "whatif.txt").splitlines():
            if line and not line.startswith("#") and "," in line and not line.startswith("output"):
                c = line.split(",")
                rows[c[0]] = [float(x) for x in c[1:]]
        # output,p5_before,p5_after,p50_before,p50_after,p95_before,p95_after,spread_before,spread_after
        self.assertLess(rows["availability"][3], rows["availability"][2])  # p50 availability falls
        self.assertGreater(rows["residual"][3], 1.5 * rows["residual"][2])  # p50 residual loss rises (x1.5 at least)
        model, degrade, _ = calib.split_overlay(os.path.join(FIX[n], "overlay.json"))
        files = {}
        for name, d in (("MODEL_MEASURED", model), ("DEGRADE_MEASURED", degrade)):
            files[name] = write("whatif-%s.json" % name, json.dumps(d))
        cmd = [sys.executable, os.path.join(MODELS, "scenario_engine.py"), "run", "nominal_pi5_5a_150m", "--n", "100"]

        def modes(extra):
            out = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **extra)).stdout
            return {m.group(1): float(m.group(2)) for m in re.finditer(r"^(usb_dropout|undervoltage),([0-9.]+)", out, re.M)}
        before, after = modes({}), modes(files)
        # the measured 1.5 A adapter and the thicker supply path raise the USB-drop and undervoltage probabilities of the model
        self.assertGreater(after["usb_dropout"], before["usb_dropout"] + 0.2)
        self.assertGreater(after["undervoltage"], before["undervoltage"] + 0.1)

    def test_report_text_and_json_agree(self):
        n = "claim_5a_actual_3a"
        txt = load(n, "report.txt")
        self.assertIn("[CONTRADICTION] K1", txt)
        self.assertIn("overlay (null = not derived from these inputs):", txt)
        self.assertIn("scenario cfg hint: psu_a=3.000 usb_max_current=False", txt)


# ---------------------------------------------------------------- contradiction rules on hand-made inputs
class TestContradictions(unittest.TestCase):
    D5 = {"board_model": "Raspberry Pi 5 Model B Rev 1.0"}

    def test_k1_undervoltage_with_a_5a_claim(self):
        d = doctor_doc(get_throttled="throttled=0x50005", **self.D5)
        rc, ids, rj = ingest_findings(["--doctor", d, "--claim-psu-a", "5", "--strict"])
        self.assertEqual(rc, 1)
        self.assertIn("K1", finding_ids(rj, "CONTRADICTION"))
        rc, ids, rj = ingest_findings(["--doctor", d, "--strict"])  # no claim: only a warning
        self.assertEqual(rc, 0)
        self.assertIn("K1", finding_ids(rj, "WARN"))
        rc, ids, rj = ingest_findings(["--doctor", d, "--claim-psu-a", "3", "--strict"])  # a 3 A claim explains it
        self.assertEqual(rc, 0)
        self.assertIn("K1", finding_ids(rj, "INFO"))
        d0 = doctor_doc(get_throttled="throttled=0x0", **self.D5)
        rc, ids, rj = ingest_findings(["--doctor", d0, "--claim-psu-a", "5", "--strict"])
        self.assertEqual(rc, 0)
        self.assertNotIn("K1", ids)

    def test_k1_from_kernel_line_or_pmic_voltage_alone(self):
        d = doctor_doc(get_throttled="throttled=0x0", dmesg_rc="0", dmesg_power_lines="[ 100.000000] hwmon hwmon1: Undervoltage detected!", **self.D5)
        rc, ids, _ = ingest_findings(["--doctor", d, "--claim-psu-a", "5"])
        self.assertIn("K1", ids)
        self.assertIn("K5", ids)  # the flags never showed it: explained by the sticky-bit clearing (SRC raspberrypi-hwmon.c)
        d = doctor_doc(get_throttled="throttled=0x0", pi5_pmic_5v_rail="EXT5V_V volt(24)=4.31000000V", **self.D5)
        rc, ids, rj = ingest_findings(["--doctor", d, "--claim-psu-a", "5"])
        self.assertIn("K1", ids)
        self.assertIn("K6", ids)

    def test_k2_k3_device_tree_state(self):
        d = doctor_doc(dt_chosen_power="max_current=00000bb8\nusb_max_current_enable=00000000", **self.D5)  # 3000 mA, limiter low
        rc, ids, rj = ingest_findings(["--doctor", d, "--claim-psu-a", "5"])
        self.assertEqual(finding_ids(rj, "CONTRADICTION"), {"K2", "K3"})
        d = doctor_doc(dt_chosen_power="max_current=00001388\nusb_max_current_enable=00000001", **self.D5)
        rc, ids, rj = ingest_findings(["--doctor", d, "--claim-psu-a", "5"])
        self.assertEqual(finding_ids(rj, "CONTRADICTION"), set())
        d = doctor_doc(usb_max_current_enable="usb_max_current_enable=0", **self.D5)  # get_config form
        rc, ids, rj = ingest_findings(["--doctor", d, "--claim-usb-max-current", "1"])
        self.assertIn("K3", finding_ids(rj, "CONTRADICTION"))

    def test_k4_soft_limit_flag_with_a_cool_soc(self):
        d = doctor_doc(get_throttled="throttled=0x8", soc_temp="temp=45.0'C", **self.D5)
        _rc, ids, _ = ingest_findings(["--doctor", d])
        self.assertIn("K4", ids)
        d = doctor_doc(get_throttled="throttled=0x8", soc_temp="temp=81.0'C", **self.D5)
        self.assertNotIn("K4", ingest_findings(["--doctor", d])[1])
        d = doctor_doc(get_throttled="throttled=0x0", soc_temp="temp=88.0'C", **self.D5)
        self.assertIn("K4b", ingest_findings(["--doctor", d])[1])

    def test_k5b_flag_without_a_kernel_line_only_when_the_log_is_readable(self):
        d = doctor_doc(get_throttled="throttled=0x10000", dmesg_rc="0", dmesg_power_lines=None, **self.D5)
        self.assertIn("K5b", ingest_findings(["--doctor", d])[1])
        d = doctor_doc(get_throttled="throttled=0x10000", dmesg_rc="1", **self.D5)
        ids = ingest_findings(["--doctor", d])[1]
        self.assertNotIn("K5b", ids)
        self.assertIn("K7", ids)

    def test_k8_hard_budget_assumption_of_the_model(self):
        rows = "\n".join("2026-10-06T09:00:%02d.%03dZ,4.9,%.3f" % (s, ms, 2.0) for s in range(10) for ms in (0, 500))
        meter = write("k8/m.csv", "timestamp,voltage_V,current_A\n" + rows + "\n")
        win = write("k8/w.txt", "2026-10-06T09:00:00Z,2026-10-06T09:00:10Z,rtl8812:tx\n")
        d = doctor_doc(dt_chosen_power="usb_max_current_enable=00000000", **self.D5)
        rc, ids, rj = ingest_findings(["--doctor", d, "--meter", "dongle:" + meter, "--windows", win])
        self.assertIn("K8", ids)
        # an over-current line in the window means the limiter acted: no finding
        dm = write("k8/dmesg.txt", "2026-10-06T09:00:05+0000 usb usb1-port1: over-current condition\n")
        self.assertNotIn("K8", ingest_findings(["--doctor", d, "--meter", "dongle:" + meter, "--windows", win, "--dmesg", dm])[1])
        # unknown budget (no state, no claim): nothing to compare with
        self.assertNotIn("K8", ingest_findings(["--meter", "dongle:" + meter, "--windows", win, "--board", "pi5"])[1])

    def test_k9_meter_voltage_above_the_pmic_rail(self):
        rows = "\n".join("2026-10-06T09:00:%02d.%03dZ,5.2,0.5" % (s, ms) for s in range(10) for ms in (0, 500))
        meter = write("k9/m.csv", "timestamp,voltage_V,current_A\n" + rows + "\n")
        log = "\n".join("=== 2026-10-06T09:00:%02d.%03dZ\nEXT5V_V volt(24)=4.80000000V" % (s, ms) for s in range(10) for ms in (0, 500))
        win = write("k9/w.txt", "2026-10-06T09:00:00Z,2026-10-06T09:00:10Z,rtl8812:rx\n")
        _rc, ids, _ = ingest_findings(["--meter", "dongle:" + meter, "--windows", win, "--instr-log", write("k9/p.log", log)])
        self.assertIn("K9", ids)

    def test_board_mismatch(self):
        d = doctor_doc(board_model="Raspberry Pi 4 Model B Rev 1.4")
        self.assertIn("K10", ingest_findings(["--doctor", d, "--board", "pi5"])[1])

    def test_hwmon_rail_label_counts_as_a_voltage_source(self):
        d = doctor_doc(hwmon="hwmon2/name=ina3221\nhwmon2/in1_label=VBUS5V\nhwmon2/in1_input=4300\n", **self.D5)
        self.assertIn("K1", ingest_findings(["--doctor", d, "--claim-psu-a", "5"])[1])

    def test_unknown_overlay_keys_are_refused(self):
        self.assertEqual(ingest.validate_keys({"power.no_such_key": None, "power.cable_resistance_ohm": None}, MODELS), ["power.no_such_key"])

    def test_empty_input_gives_an_all_null_overlay_that_calib_refuses(self):
        out = os.path.join(TMP, "empty-overlay.json")
        rc, _o, _e = run_ingest(["--overlay", out, "--now", NOW])
        self.assertEqual(rc, 0)
        ov = jload(out)
        self.assertGreaterEqual(len(ov), 20)
        self.assertEqual(set(ov.values()), {None})
        self.assertEqual(ingest.validate_keys(ov, MODELS), [])
        with self.assertRaises(SystemExit):
            calib.cmd_whatif(type("A", (), {"file": out, "scenario": "nominal_pi5_5a_150m", "n": 4, "seed": 1})())

    def test_soak_window_gives_a_drop_rate_only_with_events_and_enough_hours(self):
        lines = ["2026-10-06T09:00:10+0000 usb 1-1: New USB device found, idVendor=0bda, idProduct=8812, bcdDevice= 0.00"]
        for t in ("09:30:00", "10:00:00", "10:30:00"):
            lines.append("2026-10-06T%s+0000 usb 1-1: USB disconnect, device number 3" % t)
            lines.append("2026-10-06T%s+0000 usb 1-1: new high-speed USB device number 4 using xhci-hcd" % t[:-2] + "05")
        dm = write("soak/d.txt", "\n".join(lines) + "\n")
        out = os.path.join(TMP, "soak-overlay.json")
        rc, _o, _e = run_ingest(["--dmesg", dm, "--soak-window", "2026-10-06T09:00:00Z,2026-10-06T11:00:00Z", "--overlay", out, "--now", NOW])
        self.assertEqual(rc, 0)
        e = jload(out)["usb.drop_rate_per_h"]
        self.assertAlmostEqual(e["value"], 1.5)  # 3 disconnects in 2 h
        self.assertLess(e["min"], 1.5)
        self.assertGreater(e["max"], 1.5)
        run_ingest(["--dmesg", dm, "--soak-window", "2026-10-06T09:00:00Z,2026-10-06T09:20:00Z", "--overlay", out, "--now", NOW])
        self.assertIsNone(jload(out)["usb.drop_rate_per_h"])  # 20 min: too short, and no event inside

    def test_missing_input_is_an_error_not_a_crash(self):
        rc, _o, e = run_ingest(["--doctor", os.path.join(TMP, "does-not-exist.json")])
        self.assertEqual(rc, 2)
        self.assertIn("ingest: error", e)
        rc, _o, e = run_ingest(["--meter", "nonsense"])
        self.assertEqual(rc, 2)


class TestSensitivity(unittest.TestCase):
    ENGINE_TEXT = ("output,unit,p5,p50,p95,p99,mean\nresidual,frac,0.1,0.2,0.3,0.4,0.25\navailability,frac,0,0.8,1,1,0.7\nfailure_mode,probability\n"
                   "usb_trip,0.125\nusb_dropout,0.5\nundervoltage,0.25\nsoc_throttle,0.0\n")

    def test_engine_output_parser(self):
        o = sensitivity.parse_engine(self.ENGINE_TEXT)
        self.assertEqual(o, {"residual": 0.25, "availability": 0.7, "usb_trip": 0.125, "usb_dropout": 0.5, "undervoltage": 0.25, "soc_throttle": 0.0})

    def test_shares_sum_to_one_hundred_per_informative_output(self):
        sw = {("a", "s"): {"availability": 0.3, "usb_trip": 0.0}, ("b", "s"): {"availability": 0.1, "usb_trip": 0.0}}
        sh = sensitivity.shares(sw)
        self.assertAlmostEqual(sh["a"], 75.0)
        self.assertAlmostEqual(sh["b"], 25.0)  # outputs without any swing are not part of the average
        self.assertEqual(sensitivity.shares({("a", "s"): {}}), {"a": 0.0})

    def test_prior_points_follow_the_engine_priors_and_the_fallback_range(self):
        import priors
        base, deg = common.load(), priors.load_degrade()
        sp = priors.Space(base, deg)
        lo, med, hi, sampled = sensitivity.prior_points(sp, base, deg, "power.devices.rtl8812_tx_a", None)
        self.assertTrue(sampled)
        self.assertLess(lo, med)
        self.assertLess(med, hi)
        lo, med, hi, sampled = sensitivity.prior_points(sp, base, deg, "power.undervolt_threshold_v", (4.40, 4.86))
        self.assertFalse(sampled)  # the engine fixes it at the documented 4.63 V: a measurement cannot narrow anything unless it becomes a dimension
        self.assertEqual((lo, hi), (4.40, 4.86))
        self.assertAlmostEqual(med, 4.63)
        self.assertIsNone(sensitivity.prior_points(sp, base, deg, "power.no_such_key", None))

    def test_every_channel_key_exists_in_the_model(self):
        import priors
        base, deg = common.load(), priors.load_degrade()
        sp = priors.Space(base, deg)
        missing = [k for lst in sensitivity.CHANNELS.values() for k, _e, fb in lst if sensitivity.prior_points(sp, base, deg, k, fb) is None]
        self.assertEqual(missing, [])

    def test_one_engine_run_gives_the_outputs(self):
        o = sensitivity.run_engine("nominal_pi5_5a_150m", {"power.devices.rtl8812_tx_a": 1.4}, 20, 1)
        for k in ("availability", "residual", "usb_dropout", "undervoltage"):
            self.assertIn(k, o)
            self.assertGreaterEqual(o[k], 0.0)


class TestRepoRules(unittest.TestCase):
    def test_every_rules_section_names_a_tag(self):
        rules = jload(os.path.join(BENCH, "ingest-rules.json"))
        for sec, body in rules.items():
            if isinstance(body, dict):
                self.assertTrue(any(k == "tag" or k.endswith("_tag") for k in body), "rules section %s has no tag" % sec)

    def test_ingest_py_adds_no_hardcoded_literals(self):
        sys.path.insert(0, os.path.join(ROOT, "tests", "static"))
        import config_scan
        with open(os.path.join(BENCH, "ingest.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(config_scan.scan_py("bench/ingest.py", src), [])

    def test_docs_exist_and_name_the_tools(self):
        p = os.path.join(ROOT, "docs", "SIM-POWERLAB.md")
        self.assertTrue(os.path.isfile(p))
        with open(p, encoding="utf-8") as f:
            txt = f.read()
        for w in ("bench/ingest.py", "tests/sim/powerlab/run.sh", "UNVERIFIED", "SRC", "HW", "get_throttled", "pmic_read_adc", "не доводить"):
            self.assertIn(w, txt, w)

    def test_shell_scripts_pass_shellcheck(self):
        import shutil
        if not shutil.which("shellcheck"):
            self.skipTest("shellcheck not installed")
        files = [os.path.join(BENCH, "doctor.sh"), PIPE, os.path.join(HERE, "run.sh"), os.path.join(HERE, "mutate.sh")] + [os.path.join(HERE, "shims", s) for s in ("vcgencmd", "dmesg", "lsusb", "date")]
        r = subprocess.run(["shellcheck", "-x", "-S", "warning"] + files, capture_output=True, text=True, cwd=BENCH)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
