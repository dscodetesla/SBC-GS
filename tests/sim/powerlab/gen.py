#!/usr/bin/env python3
"""Virtual instruments for the power lab: a controlled power scenario -> synthetic logs in the REAL formats (SYNTH, never a measurement).

  gen.py scenarios/calib_pi5_5a.json --out DIR [--seed N]

Time-steps a small electrical model of the GS (Pi + RTL8812 dongle + FC) with the SAME formulas and parameter files as
tests/sim/models (power_model.budget/device_a/usb_budget_a, degrade_model.Pi5UsbLimiter) but with "truth" values that differ from
the priors, and writes what the instruments would have recorded:
  DIR/pmic.log                '=== <ISO>' blocks: get_throttled, measure_temp, pmic_read_adc EXT5V_V
  DIR/dmesg.txt               kernel log ('[secs.usecs]' stamps; USB disconnect/new, hwmon under-voltage lines, over-current)
  DIR/meter_psu.csv, meter_dongle.csv   inline meter logs (generic CSV, header with units)
  DIR/windows.txt             operator windows 'start,end,label'
  DIR/wfb.jsonl               wfb-ng api_port style JSON lines (settings, rx, tx)
  DIR/vcgencmd/*, lsusb*.txt, now_utc, sysroot/{proc,sys}/...   replay tree for tests/sim/powerlab/shims + bench/doctor.sh (DOCTOR_ROOT)
  DIR/truth.json              the true values the generator used (what bench/ingest.py should recover)
Formats: vcgencmd/kernel/lsusb formats follow the sources in bench/ingest-rules.json; the Pi 5 over-current message and the
re-enumeration timing on a Pi 5 are UNVERIFIED (SYNTH here, hub.c wording used). Deterministic for a given seed.
"""
import argparse
import datetime
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "..", "models")
sys.path.insert(0, MODELS)
import common  # noqa: E402
import degrade_model  # noqa: E402
import power_model  # noqa: E402
import priors  # noqa: E402

BOARD_MODEL = {"pi5": "Raspberry Pi 5 Model B Rev 1.0", "pi4": "Raspberry Pi 4 Model B Rev 1.4", "pi3bp": "Raspberry Pi 3 Model B Plus Rev 1.3"}  # SNIP strings
SPEED = {"rtl8812": "high-speed", "fc": "full-speed", "webcam": "high-speed"}
MBPS = {"high-speed": "480", "full-speed": "12"}
CLASS = {"rtl8812": ("Vendor Specific Class", "rtl88xxau"), "fc": ("Communications", "cdc_acm"), "webcam": ("Video", "uvcvideo")}
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
STEP_S = 0.02  # simulation step; the meter logs every step (50 Hz)
IDLE_RISE_FRAC = 0.4  # SYNTH: SoC rise at idle relative to the load rise
SOC_TAU_S = 60.0  # SYNTH default; scenarios may set soc_tau_s (the tests use a short one to settle fast)
ADC_SIGMA_V = 0.004  # SYNTH: PMIC ADC noise
METER_SIGMA_V, METER_SIGMA_A, METER_REL = 0.002, 0.002, 0.005  # SYNTH
DROP_HYST_V = 0.05  # SYNTH: the dongle comes back only this far above the drop voltage (computed without its own load)
PMIC_JITTER = 0.2  # SYNTH: a shell polling loop has jitter; a fixed 5 Hz loop would alias with the 5 Hz TX pulses
POLL_S = 2.0  # raspberrypi-hwmon.c: schedule_delayed_work(..., 2 * HZ)  (SRC)
BURST_DUTY = 0.3  # same pulse shape as power_model.timeline (SYNTH)
BURST_HZ = 5.0  # SYNTH TX pulse rate; local on purpose: the model parameter for it was removed from params.degrade.json
LIMITER_DEFAULTS = {"usb.pi5_trip_tol": 0.075, "usb.pi5_trip_off_s": 2.0, "usb.pi5_trip_latch_n": 4.0}  # SYNTH fall-backs when the model file lacks a key
FIRST_DEVNUM = 2


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(TIME_FMT)


def iso_ms(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class Sim:
    def __init__(self, sc, seed):
        self.sc, self.rnd = sc, random.Random(seed)
        truth = dict(sc.get("truth", {}))
        self.P = common.load({k: v for k, v in truth.items() if k.startswith("power.")})
        self.D = priors.load_degrade()
        self.truth = {}
        def g(k):
            if k in truth:
                return truth[k]
            try:
                return self.P.get(k) if k.startswith("power.") else self.D.get(k)
            except common.ParamError:
                return LIMITER_DEFAULTS[k]
        self.g = g
        self.board, self.psu_a = sc["board"], sc["psu_a"]
        self.limit = power_model.usb_budget_a(self.P, self.board, self.psu_a, sc.get("usb_max_current", False))
        self.t0 = parse_iso(sc["t0_utc"])
        self.boot = self.t0 - sc.get("boot_offset_s", 600.0)
        self.devs = {d["id"]: dict(d, attached=False, up=False, devnum=0, until=0.0) for d in sc["devices"]}
        self.klog, self.pmic, self.meter_psu, self.meter_dongle, self.wfb = [], [], [], [], []
        self.devnum = FIRST_DEVNUM
        self.oc_count = 0

    # ---- kernel log helpers
    def kmsg(self, t, text):
        self.klog.append((t, text))

    def plug(self, t, d):
        d["attached"] = d["up"] = True
        d["devnum"] = self.devnum
        self.devnum += 1
        sp = SPEED[d["kind"]]
        self.kmsg(t, "usb %s: new %s USB device number %d using xhci-hcd" % (d["bus_port"], sp, d["devnum"]))
        vid, pid = d["vid_pid"].split(":")
        self.kmsg(t, "usb %s: New USB device found, idVendor=%s, idProduct=%s, bcdDevice= 0.00" % (d["bus_port"], vid, pid))

    def unplug(self, t, d):
        if d["up"]:
            self.kmsg(t, "usb %s: USB disconnect, device number %d" % (d["bus_port"], d["devnum"]))
        d["attached"] = d["up"] = False

    def drop(self, t, d, until):
        self.kmsg(t, "usb %s: USB disconnect, device number %d" % (d["bus_port"], d["devnum"]))
        d["up"], d["until"] = False, until

    def run(self):
        sc, g, P, rnd = self.sc, self.g, self.P, self.rnd
        board, nominal, r_path = self.board, g("power.psu_nominal_v"), g("power.cable_resistance_ohm")
        r_usb, thr_v, drop_v = g("usb.cable_r_ohm"), g("power.undervolt_threshold_v"), g("power.usb_dropout_v")
        reenum, soft_c, rise = g("power.usb_reenum_s"), g("hw.soc_soft_limit_c"), g("hw.soc_rise_c")
        amb = sc.get("ambient_c", g("ext.ambient_c"))
        peak, burst_hz = g("power.tx_peak_factor"), truth_burst_hz(sc)
        clears = sc.get("kernel_clears_sticky", True)
        lim = degrade_model.Pi5UsbLimiter(self.limit, g("usb.pi5_trip_tol"), int(g("usb.pi5_trip_latch_n"))) if sc.get("limiter") and board == "pi5" else None
        off_s = g("usb.pi5_trip_off_s")
        extra = sum(power_model.device_a(P, x) for x in sc.get("extra_5v", []))
        self.truth = {"power.cable_resistance_ohm": r_path, "power.psu_nominal_v": nominal, "usb.cable_r_ohm": r_usb, "power.undervolt_threshold_v": thr_v,
                      "power.usb_dropout_v": drop_v, "power.usb_reenum_s": reenum, "hw.soc_soft_limit_c": soft_c, "hw.soc_rise_c": rise, "ext.ambient_c": amb,
                      "power.tx_peak_factor": peak,
                      "power.devices.rtl8812_idle_a": g("power.devices.rtl8812_idle_a"), "power.devices.rtl8812_rx_a": g("power.devices.rtl8812_rx_a"),
                      "power.devices.rtl8812_tx_a": g("power.devices.rtl8812_tx_a"), "power.devices.fc_usb_a": g("power.devices.fc_usb_a"),
                      "power.boards.%s.board_idle_a" % board: g("power.boards.%s.board_idle_a" % board),
                      "power.boards.%s.board_load_a" % board: g("power.boards.%s.board_load_a" % board),
                      "usb_budget_a": self.limit}
        temp = amb + rise * IDLE_RISE_FRAC
        soc_gain = 1.0 - math.exp(-STEP_S / sc.get("soc_tau_s", SOC_TAU_S))
        sticky_uv = sticky_soft = False
        last_uv_reported = False
        next_poll = self.boot + math.ceil((self.t0 - self.boot) / POLL_S) * POLL_S
        next_pmic = self.t0
        next_wfb = self.t0
        wfb_loss = 0
        t = self.t0
        windows = []
        v_board = nominal
        total_i = 0.0
        thr_now = 0
        for ph in sc["phases"]:
            t_end = t + ph["dur_s"]
            if ph.get("label"):
                windows.append((t, t_end, ph["label"]))
            want = set(ph.get("devices", []))
            for did, d in self.devs.items():  # hot-plug at the phase start
                if did in want and not d["attached"]:
                    self.plug(t, d)
                elif did not in want and d["attached"]:
                    self.unplug(t, d)
            load = ph.get("load", "active")
            base_a = P.get("power.boards.%s.board_%s_a" % (board, load))
            ad = ph.get("adapter")
            ramp = ph.get("v_ramp")
            n = int(round(ph["dur_s"] / STEP_S))
            for k in range(n):
                t = t_end - (n - k) * STEP_S
                v0 = nominal if not ramp else ramp[0] + (ramp[1] - ramp[0]) * (k / max(n - 1, 1))
                depth = sum(dp["depth_v"] for dp in ph.get("dips", []) if dp["t_s"] <= t - (t_end - ph["dur_s"]) < dp["t_s"] + dp["duration_s"])
                # device currents (static state current, TX pulses at BURST_DUTY x peak)
                burst = ((t * burst_hz) % 1.0) < BURST_DUTY
                cur = {}
                for did, d in self.devs.items():
                    if not (d["attached"] and d["up"]):
                        continue
                    if d["kind"] == "rtl8812":
                        a = power_model.device_a(P, "rtl8812", ad or "idle", burst)
                    else:
                        a = power_model.device_a(P, d["kind"])
                    cur[did] = a * (1.0 + METER_REL * rnd.gauss(0, 1))
                i_usb = sum(cur.values())
                i_all = sum(power_model.device_a(P, x["kind"], ad or "idle") if x["kind"] == "rtl8812" else power_model.device_a(P, x["kind"]) for x in self.devs.values() if x["attached"])
                total_i = base_a + extra + i_usb
                v_board = v0 - total_i * r_path - depth
                uv_now = v_board < thr_v
                # soc temperature
                tgt = amb + rise * (1.0 if load == "load" else IDLE_RISE_FRAC)
                temp += (tgt - temp) * soc_gain
                soft_now = temp >= soft_c
                sticky_uv |= uv_now
                sticky_soft |= soft_now
                # limiter and undervoltage drops
                if lim is not None:
                    ev = lim.step(t, i_usb, off_s)
                    if ev in ("usb_trip", "usb_latched"):
                        self.oc_count += 1
                        self.kmsg(t, "usb usb1-port1: over-current condition")
                        for d in self.devs.values():
                            if d["up"]:
                                self.drop(t, d, t + reenum if ev == "usb_trip" else math.inf)
                if v_board < drop_v:
                    for d in self.devs.values():
                        if d["up"]:
                            self.drop(t, d, t + reenum)
                for d in self.devs.values():
                    if d["attached"] and not d["up"] and t >= d["until"] and v0 - (base_a + extra + i_all) * r_path - depth >= drop_v + DROP_HYST_V \
                            and not (lim is not None and lim.state != "OK"):
                        self.plug(t, d)
                        d["attached"] = True
                # kernel hwmon poll (every 2 s): reports sticky transitions and, with the driver, clears the sticky bits
                while t >= next_poll:
                    if sticky_uv != last_uv_reported:
                        self.kmsg(next_poll, "hwmon hwmon1: Undervoltage detected!" if sticky_uv else "hwmon hwmon1: Voltage normalised")
                        last_uv_reported = sticky_uv
                    if clears:
                        sticky_uv, sticky_soft = uv_now, soft_now
                    next_poll += POLL_S
                thr_now = ((power_model.UV_NOW | power_model.THR_NOW) if uv_now else 0) | ((1 << 3) if soft_now else 0) \
                    | ((power_model.UV_EVER | power_model.THR_EVER) if sticky_uv else 0) | ((1 << 19) if sticky_soft else 0)
                # instruments
                dong = cur.get("rtl8812", 0.0)
                self.meter_psu.append((t, v0 + METER_SIGMA_V * rnd.gauss(0, 1), total_i + METER_SIGMA_A * rnd.gauss(0, 1)))
                vd = v_board - dong * r_usb + METER_SIGMA_V * rnd.gauss(0, 1) if dong > 0 else v_board
                self.meter_dongle.append((t, vd, max(0.0, dong + METER_SIGMA_A * rnd.gauss(0, 1)) if dong > 0 else 0.0))
                if t >= next_pmic:
                    self.pmic.append((t, thr_now, temp, v_board + ADC_SIGMA_V * rnd.gauss(0, 1)))
                    next_pmic += sc.get("pmic_period_s", 0.2) * rnd.uniform(1.0 - PMIC_JITTER, 1.0 + PMIC_JITTER)
                if t >= next_wfb:
                    up = "rtl8812" in cur
                    self.wfb.append((t, up))
                    next_wfb += 1.0
        self.t_end = t
        self.windows = windows
        self.final = {"thr": thr_now, "temp": temp, "v_board": v_board, "total_i": total_i, "sticky_uv": sticky_uv}
        return self


def truth_burst_hz(sc):
    return sc.get("tx_burst_hz", BURST_HZ)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def emit(sim, out):
    sc = sim.sc
    # kernel log
    lines = []
    for t, msg in sorted(sim.klog, key=lambda x: x[0]):
        k = t - sim.boot
        lines.append("[%5d.%06d] %s" % (int(k), int(round((k - int(k)) * 1e6)), msg))
    write(os.path.join(out, "dmesg.txt"), "\n".join(lines) + "\n")
    # instrument log
    rows = []
    for t, thr, temp, v in sim.pmic:
        rows.append("=== %s\nthrottled=0x%x\ntemp=%.1f'C\nEXT5V_V volt(24)=%.8fV" % (iso_ms(t), thr, temp, v))
    write(os.path.join(out, "pmic.log"), "\n".join(rows) + "\n")
    # meters (psu in A, dongle in the unit the scenario asks for, header says which)
    mu = sc.get("meters", {})
    for pos, series in (("psu", sim.meter_psu), ("dongle", sim.meter_dongle)):
        ma = mu.get(pos, {}).get("unit", "A") == "mA"
        head = "timestamp,Voltage (V),Current (mA)" if ma else "timestamp,voltage_V,current_A"
        body = ["%s,%.4f,%.4f" % (iso_ms(t), v, i * (1000.0 if ma else 1.0)) for t, v, i in series]
        write(os.path.join(out, "meter_%s.csv" % pos), "# synthetic inline meter log (SYNTH)\n" + head + "\n" + "\n".join(body) + "\n")
    write(os.path.join(out, "windows.txt"), "\n".join("%s,%s,%s" % (iso_ms(a), iso_ms(b), lab) for a, b, lab in sim.windows) + "\n")
    # wfb-ng api_port style lines
    wl = [json.dumps({"type": "settings", "profile": "gs", "is_cluster": False, "wlans": ["wlan0"], "settings": {"common": {"log_interval": 1000}}})]
    rnd = random.Random(1)
    for t, up in sim.wfb:
        if up:
            pk = 480 + rnd.randint(-5, 5)
            pkts = {k: [0, 0] for k in ("all", "out", "session", "fec_rec", "lost", "dec_err", "bad", "data", "uniq", "all_bytes", "out_bytes")}
            pkts.update({"all": [pk, 0], "out": [pk - 8, 0], "data": [pk, 0], "uniq": [pk, 0]})
            ants = [{"ant": 0, "freq": 5805, "mcs": 1, "bw": 20, "pkt_recv": pk, "rssi_min": -62, "rssi_avg": -58, "rssi_max": -55, "snr_min": 25, "snr_avg": 28, "snr_max": 30}]
        else:
            pkts = {k: [0, 0] for k in ("all", "out", "session", "fec_rec", "lost", "dec_err", "bad", "data", "uniq", "all_bytes", "out_bytes")}
            pkts["lost"] = [480, 0]
            ants = []
        wl.append(json.dumps({"type": "rx", "id": "video", "packets": pkts, "session": {"fec_k": 8, "fec_n": 12}, "rx_ant_stats": ants}))
    write(os.path.join(out, "wfb.jsonl"), "\n".join(wl) + "\n")
    # replay tree for the shims and doctor.sh
    fin = sim.final
    now = sim.t_end
    write(os.path.join(out, "now_utc"), iso(now) + "\n")
    vc = os.path.join(out, "vcgencmd")
    write(os.path.join(vc, "get_throttled"), "throttled=0x%x\n" % fin["thr"])
    write(os.path.join(vc, "measure_temp"), "temp=%.1f'C\n" % fin["temp"])
    write(os.path.join(vc, "measure_clock_arm"), "frequency(48)=%d\n" % (2400000000 if not fin["thr"] & 8 else 1800000000))
    cfg = int(bool(sc.get("usb_max_current", False)))
    write(os.path.join(vc, "get_config_usb_max_current_enable"), "usb_max_current_enable=%d\n" % cfg)
    ext = "EXT5V_V volt(24)=%.8fV\n" % fin["v_board"]
    write(os.path.join(vc, "pmic_read_adc_EXT5V_V"), ext)
    rails = [("3V7_WL_SW_A", 0.01), ("3V3_SYS_A", 0.12), ("1V8_SYS_A", 0.24), ("DDR_VDD2_A", 0.02), ("DDR_VDDQ_A", 0.0), ("1V1_SYS_A", 0.19), ("0V8_SYS_A", 0.27), ("VDD_CORE_A", 0.75)]
    full = "".join("%12s current(%d)=%.8fA\n" % (n, i, a) for i, (n, a) in enumerate(rails)) + "%12s volt(8)=3.70627200V\n" % "3V7_WL_SW_V" + "%12s volt(24)=%.8fV\n" % ("EXT5V_V", fin["v_board"])
    write(os.path.join(vc, "pmic_read_adc"), full)
    # lsusb
    sp = {d["id"]: d for d in sim.devs.values() if d["up"]}
    tree = ["/:  Bus 001.Port 001: Dev 001, Class=root_hub, Driver=xhci-hcd/2p, 480M"]
    flat = []
    for n, d in enumerate(sorted(sp.values(), key=lambda x: x["bus_port"]), 1):
        cls, drv = CLASS[d["kind"]]
        tree.append("    |__ Port %03d: Dev %03d, If 0, Class=%s, Driver=%s, %sM" % (n, d["devnum"], cls, drv, MBPS[SPEED[d["kind"]]]))
        flat.append("Bus 001 Device %03d: ID %s Synthetic %s" % (d["devnum"], d["vid_pid"], d["kind"]))
    tree.append("/:  Bus 002.Port 001: Dev 001, Class=root_hub, Driver=xhci-hcd/1p, 5000M")
    write(os.path.join(out, "lsusb_t.txt"), "\n".join(tree) + "\n")
    write(os.path.join(out, "lsusb.txt"), "\n".join(flat + ["Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub"]) + "\n")
    # sysroot
    sr = os.path.join(out, "sysroot")
    write(os.path.join(sr, "proc/device-tree/model"), BOARD_MODEL[sim.board] + "\x00")
    be = lambda v: int(v).to_bytes(4, "big")  # noqa: E731
    cp = os.path.join(sr, "proc/device-tree/chosen/power")
    os.makedirs(cp, exist_ok=True)
    for name, val in (("max_current", int(round(sim.psu_a * 1000))), ("usb_max_current_enable", cfg), ("usb_over_current_detected", int(sim.oc_count > 0)), ("reset_event", 0)):
        with open(os.path.join(cp, name), "wb") as f:
            f.write(be(val))
    write(os.path.join(sr, "proc/uptime"), "%.2f %.2f\n" % (now - sim.boot, (now - sim.boot) * 3))
    write(os.path.join(sr, "sys/class/thermal/thermal_zone0/temp"), "%d\n" % int(round(fin["temp"] * 1000)))
    write(os.path.join(sr, "sys/class/thermal/thermal_zone0/type"), "cpu-thermal\n")
    write(os.path.join(sr, "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"), "2400000\n")
    write(os.path.join(sr, "sys/class/hwmon/hwmon1/name"), "rpi_volt\n")
    write(os.path.join(sr, "sys/class/hwmon/hwmon1/in0_lcrit_alarm"), "%d\n" % int(fin["sticky_uv"]))
    if sc.get("ext_hwmon_ina"):
        write(os.path.join(sr, "sys/class/hwmon/hwmon2/name"), "ina3221\n")
        write(os.path.join(sr, "sys/class/hwmon/hwmon2/in1_label"), "VBUS5V\n")
        write(os.path.join(sr, "sys/class/hwmon/hwmon2/in1_input"), "%d\n" % int(round(fin["v_board"] * 1000)))
        write(os.path.join(sr, "sys/class/hwmon/hwmon2/curr1_input"), "%d\n" % int(round(fin["total_i"] * 1000)))
    write(os.path.join(sr, "sys/bus/usb/devices/usb1/idVendor"), "1d6b\n")
    write(os.path.join(sr, "sys/bus/usb/devices/usb1/idProduct"), "0002\n")
    write(os.path.join(sr, "sys/bus/usb/devices/usb1/bMaxPower"), "0mA\n")
    write(os.path.join(sr, "sys/bus/usb/devices/usb1/speed"), "480\n")
    for d in sp.values():
        base = os.path.join(sr, "sys/bus/usb/devices", d["bus_port"])
        vid, pid = d["vid_pid"].split(":")
        write(os.path.join(base, "idVendor"), vid + "\n")
        write(os.path.join(base, "idProduct"), pid + "\n")
        write(os.path.join(base, "bMaxPower"), "%dmA\n" % (500 if d["kind"] != "fc" else 100))
        write(os.path.join(base, "speed"), MBPS[SPEED[d["kind"]]] + "\n")
        write(os.path.join(base, "product"), "synthetic %s\n" % d["kind"])
    write(os.path.join(sr, "sys/bus/usb/devices/usb1/1-0:1.0/usb1-port1/over_current_count"), "%d\n" % sim.oc_count)
    # truth and the resolved scenario
    write(os.path.join(out, "truth.json"), json.dumps({"scenario": sc["name"], "t0_utc": sc["t0_utc"], "boot_utc": iso(sim.boot), "claim_psu_a": sc.get("claim_psu_a"),
                                                       "usb_max_current_claim": sc.get("claim_usb_max_current"), "board": sim.board, "values": sim.truth,
                                                       "tag": "SYNTH"}, indent=1) + "\n")
    write(os.path.join(out, "scenario.json"), json.dumps(sc, indent=1) + "\n")
    # the operator's side of the story: what a bench log reader would pass to bench/ingest.py (one argument per line)
    args = ["--board", sim.board, "--wfb-start-utc", iso(sim.t0)]
    if sc.get("claim_psu_a") is not None:
        args += ["--claim-psu-a", str(sc["claim_psu_a"])]
    if sc.get("claim_usb_max_current") is not None:
        args += ["--claim-usb-max-current", str(sc["claim_usb_max_current"])]
    if sc.get("ambient_c") is not None:
        args += ["--ambient-c", str(sc["ambient_c"])]
    write(os.path.join(out, "ingest.args"), "\n".join(args) + "\n")


def generate(sc, out, seed=1):
    sim = Sim(sc, seed).run()
    emit(sim, out)
    return sim


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    with open(a.scenario, encoding="utf-8") as f:
        sc = json.load(f)
    if sc.get("schema") != "sbc-gs-powerlab-scenario/1":
        print("gen: error: scenario schema must be sbc-gs-powerlab-scenario/1", file=sys.stderr)
        return 2
    sim = generate(sc, a.out, a.seed)
    print("gen: %s -> %s (%d meter rows, %d pmic samples, %d kernel lines)" % (sc["name"], a.out, len(sim.meter_psu), len(sim.pmic), len(sim.klog)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
