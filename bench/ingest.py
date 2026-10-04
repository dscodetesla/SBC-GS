#!/usr/bin/env python3
"""Bench-day ingest: raw instrument outputs -> normalised report -> measured overlay for `calib.py whatif` (stdlib only).

  bench/ingest.py --doctor out/doctor-*.json [--instr-log pmic.log] [--dmesg k.txt] [--meter psu:a.csv --meter dongle:b.csv]
                  [--windows w.txt] [--wfb-json w.jsonl] [--claim-psu-a 5] [--overlay overlay.json] [--report-json r.json]
  then:  python3 tests/sim/models/calib.py whatif overlay.json --scenario nominal_pi5_5a_150m

Reads only files (never touches hardware, never transmits). Every number that is not derived from the inputs stays null in the
overlay; nothing is invented. Constants live in bench/ingest-rules.json with a tag and a source (SRC read from a first-party file,
SNIP third-party sample output, INF = choice of this tool, UNVERIFIED). Formats of the inputs (docs/SIM-POWERLAB.md):
  doctor JSON      sbc-gs-doctor/1 from bench/doctor.sh (new fields are optional; missing = null = not available)
  instrument log   blocks '=== <ISO UTC>' followed by raw `vcgencmd get_throttled / measure_temp / pmic_read_adc` output (bare lines
                   without separators also parse, then without time)
  dmesg            `dmesg`, `dmesg -T` (assumed UTC) or `journalctl -k -o short-iso`; kernel-clock lines need --boot-utc
  meter CSV        generic: a header row with time / volts / amps columns (unit from the header: A or mA, V or mV) or the
                   power_model.py ingest shape `device,state,amps`; --meter POS:FILE with POS = psu | host | dongle
  windows          lines 'start,end,label' (ISO UTC, epoch s, or seconds relative to --boot-utc/--t0-utc); label 'device:state'
                   (rtl8812:tx, board_pi5:load, soc:load ...)
  wfb JSON         one JSON object per line from the wfb-ng api_port (wfb-cli itself is a curses screen and is not parseable)
Exit: 0 ok, 1 with --strict when a CONTRADICTION is found, 2 bad input.
"""
import argparse
import bisect
import csv
import datetime
import io
import json
import math
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "ingest-rules.json")
with open(RULES_PATH, encoding="utf-8") as _f:
    RULES = json.load(_f)
TH = RULES["thresholds"]
ROLE = RULES["throttled_roles"]
KEY = RULES["keys"]
UNIT = RULES["units"]
KP = {k: re.compile(v) for k, v in RULES["kernel_patterns"].items() if k not in ("tag", "source", "note")}
VP = {k: re.compile(v, re.M) for k, v in RULES["vcgencmd_patterns"].items() if k not in ("tag", "source", "note")}
SIG, TFMT = RULES["fmt"]["sig"], RULES["fmt"]["t"]
SCHEMA = "sbc-gs-ingest/1"
SEV_ORDER = {"CONTRADICTION": 0, "WARN": 1, "INFO": 2}
DEVICE_KEYS = {"fc": KEY["fc"], "webcam": KEY["webcam"], "fan": KEY["fan"]}
RADIO_STATE_KEYS = {"idle": KEY["dev_idle"], "rx": KEY["dev_rx"], "tx": KEY["dev_tx"]}
DEVICE_POS = ("host", "dongle")


class InputError(Exception):
    pass


def bit(n):
    return 1 << n


def sig(x):
    return float(SIG % x)


# ---------------------------------------------------------------- time
def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


_ISO_CACHE = {}


def parse_iso(s):
    s = s.strip()
    head, dot, frac = s.rstrip("Z").partition(".")
    if s.endswith("Z") and (not dot or frac.isdigit()):  # fast path for the UTC stamps of long meter logs
        base = _ISO_CACHE.get(head)
        if base is None:
            base = _ISO_CACHE[head] = datetime.datetime.fromisoformat(head + "+00:00").timestamp()
        return base + (float("0." + frac) if dot else 0.0)
    d = datetime.datetime.fromisoformat(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s.replace("Z", "+00:00").replace(",", ".")))
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def parse_time(s, base=None):
    """ISO UTC, epoch seconds, or seconds relative to `base` (epoch)."""
    s = str(s).strip()
    try:
        v = float(s)
    except ValueError:
        try:
            return parse_iso(s)
        except ValueError as e:
            raise InputError("bad time %r (%s)" % (s, e))
    if v >= TH["epoch_min_s"]:
        return v
    if base is None:
        raise InputError("relative time %r needs --boot-utc or --t0-utc" % s)
    return base + v


# ---------------------------------------------------------------- stats
def pct(xs, q):
    s = sorted(xs)
    k = (len(s) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def mean(xs):
    return sum(xs) / len(xs)


def through_origin(pairs):
    """least squares dV = R * I over [(I, dV)]: (R, lo, hi) with a 95 % interval from the residuals (lo = hi = R for one pair)"""
    sii = sum(i * i for i, _d in pairs)
    r = sum(i * d for i, d in pairs) / sii
    if len(pairs) <= 1:
        return r, r, r
    se = math.sqrt(sum((d - r * i) ** 2 for i, d in pairs) / (len(pairs) - 1) / sii)
    return r, max(0.0, r - TH["z95"] * se), r + TH["z95"] * se


def win_rows(rows, w):
    """rows [(t, ...)] sorted by t inside the window w trimmed by window_trim_frac on both sides"""
    span = w["t1"] - w["t0"]
    a, b = w["t0"] + TH["window_trim_frac"] * span, w["t1"] - TH["window_trim_frac"] * span
    ts = [r[0] for r in rows]
    return rows[bisect.bisect_left(ts, a):bisect.bisect_right(ts, b)]


# ---------------------------------------------------------------- vcgencmd / instrument log
def parse_pmic_text(text):
    out = {}
    for m in VP["pmic"].finditer(text):
        name, kind, idx, val, _unit = m.groups()
        out[name] = (kind, int(idx), float(val))
    return out


def new_sample(t):
    return {"t": t, "throttled": None, "temp_c": None, "pmic": {}, "arm_hz": None}


def parse_instr_log(text, base=None):
    """list of samples {t, throttled, temp_c, pmic{name:(kind,idx,val)}, arm_hz}; '=== <ts>' starts a block, a repeated field
    starts a new (time-less) sample in bare logs"""
    samples, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^===\s*(\S+)", line)
        if m:
            cur = new_sample(parse_time(m.group(1), base))
            cur["_block"] = True
            samples.append(cur)
            continue
        for name, rx, conv in (("throttled", VP["throttled"], lambda g: int(g.group(1), 0)),
                               ("temp_c", VP["temp"], lambda g: float(g.group(1))),
                               ("arm_hz", VP["arm_clock"], lambda g: int(g.group(2)))):
            g = rx.search(line)
            if not g:
                continue
            if cur is None or (cur[name] is not None and not cur.get("_block")):
                cur = new_sample(None)
                samples.append(cur)
            cur[name] = conv(g)
        pm = parse_pmic_text(line)
        if pm:
            if cur is None:
                cur = new_sample(None)
                samples.append(cur)
            cur["pmic"].update(pm)
    return samples


def ext5v(sample):
    v = sample["pmic"].get(RULES["pmic"]["ext5v"])
    return v[2] if v and v[0] == "volt" else None


# ---------------------------------------------------------------- kernel log
def kernel_time(line, boot):
    """(epoch or None, kernel seconds or None, message)"""
    kt = RULES["kernel_time"]
    m = re.match(kt["bracket"], line)
    if m:
        k = float(m.group(1))
        return (boot + k if boot is not None else None), k, line[m.end():].strip()
    m = re.match(kt["dmesg_T"], line)
    if m:
        d = datetime.datetime.strptime(m.group(1), kt["dmesg_T_format"]).replace(tzinfo=datetime.timezone.utc)
        return d.timestamp(), None, line[m.end():].strip()
    m = re.match(kt["iso"], line)
    if m:
        return parse_iso(m.group(1)), None, line[m.end():].strip()
    return None, None, line.strip()


def parse_dmesg(text, boot=None):
    ev = []
    for line in text.splitlines():
        if not line.strip():
            continue
        t, k, msg = kernel_time(line, boot)
        e = None
        m = KP["usb_disconnect"].search(msg)
        if m:
            path, devnum = m.groups()
            e = {"kind": "usb_disconnect", "path": path, "devnum": int(devnum)}
        if e is None:
            m = KP["usb_new"].search(msg)
            if m:
                path, what, speed, devnum, hcd = m.groups()
                e = {"kind": "usb_new" if what == "new" else "usb_reset", "path": path, "speed": speed, "devnum": int(devnum), "hcd": hcd}
        if e is None:
            m = KP["usb_found"].search(msg)
            if m:
                path, vid, pid = m.groups()
                e = {"kind": "usb_found", "path": path, "vid_pid": ("%s:%s" % (vid, pid)).lower()}
        if e is None and KP["uv_detected"].search(msg):
            e = {"kind": "uv_detected"}
        if e is None and KP["uv_normalised"].search(msg):
            e = {"kind": "uv_normalised"}
        if e is None and KP["usb_overcurrent"].search(msg):
            e = {"kind": "usb_overcurrent"}
        if e is None:
            m = KP["usb_enum_fail"].search(msg)
            if m:
                e = {"kind": "usb_enum_fail", "what": m.group(1)}
        if e is not None:
            e.update({"t": t, "t_kernel": k, "msg": msg})
            ev.append(e)
    return ev


# ---------------------------------------------------------------- lsusb -t
def parse_lsusb_t(text):
    buses, devs = [], []
    for line in text.splitlines():
        m = re.match(r"^/:\s+Bus (\d+)\.Port (\d+): Dev (\d+), Class=([^,]+), Driver=([^,/]+)(?:/(\d+)p)?, (\S+?)M", line)
        if m:
            bus, _port, _dev, cls, drv, ports, mbps = m.groups()
            buses.append({"bus": int(bus), "class": cls, "driver": drv, "ports": int(ports or 0), "mbps": mbps})
            continue
        m = re.match(r"^\s*\|__ Port (\d+): Dev (\d+)(?:, If (\d+), Class=([^,]+), Driver=([^,/]+)(?:/\d+p)?)?, (\S+?)M", line)
        if m and buses:
            port, dev, _if, cls, drv, mbps = m.groups()
            devs.append({"bus": buses[-1]["bus"], "port": int(port), "dev": int(dev), "class": cls, "driver": drv, "mbps": mbps})
    return buses, devs


# ---------------------------------------------------------------- doctor JSON
def kv_lines(text):
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def hex_bytes_to_int(h):
    h = h.strip()
    if len(h) != RULES["dt_chosen_power"]["cell_hex_len"]:
        return None
    return int.from_bytes(bytes.fromhex(h), "big")


def parse_gpiodetect(text):
    """libgpiod v1/v2 `gpiodetect` lines: "gpiochipN [label] (M lines)" -> [(n, label, lines)]."""
    out = []
    for ln in text.splitlines():
        m = re.match(r"\s*gpiochip(\d+)\s+\[([^\]]*)\]\s+\((\d+) lines?\)", ln)
        if m:
            n, label, nlines = m.groups()
            out.append((int(n), label, int(nlines)))
    return out


def parse_doctor(doc):
    if doc.get("schema") != "sbc-gs-doctor/1":
        raise InputError("not an sbc-gs-doctor/1 document")
    f = {"raw": doc}
    f["utc"] = parse_iso(doc["utc"]) if doc.get("utc") else None
    f["board_model"] = (doc.get("board_model") or "").strip("\x00 ")
    f["uptime_s"] = float(doc["uptime_s"].split()[0]) if doc.get("uptime_s") else None
    f["throttled"] = int(VP["throttled"].search(doc["get_throttled"]).group(1), 0) if doc.get("get_throttled") and VP["throttled"].search(doc["get_throttled"]) else None
    m = VP["temp"].search(doc.get("soc_temp") or "")
    f["temp_c"] = float(m.group(1)) if m else None
    full = parse_pmic_text("\n".join(x for x in (doc.get("pi5_pmic_adc_full"), doc.get("pi5_pmic_5v_rail")) if x))
    f["pmic"] = full
    cfg = VP["get_config"].search(doc.get("usb_max_current_enable") or "")
    f["usb_max_current_cfg"] = int(cfg.group(2)) if cfg else None
    f["dt_power_raw"] = kv_lines(doc.get("dt_chosen_power"))
    f["dt_power"] = {k: hex_bytes_to_int(v) for k, v in f["dt_power_raw"].items()}
    f["dmesg_rc"] = doc.get("dmesg_rc")
    f["dmesg_lines"] = doc.get("dmesg_power_lines") or ""
    f["dmesg_count"] = doc.get("dmesg_usb_power_events")
    f["lsusb_tree"] = doc.get("lsusb_tree") or ""
    f["usb_sysfs"] = [ln for ln in (doc.get("usb_sysfs") or "").splitlines() if ln.strip()]
    f["usb_oc_count"] = kv_lines(doc.get("usb_port_over_current_count"))
    f["usb_radio_ids"] = [ln for ln in (doc.get("usb_radio_fc_candidates") or "").splitlines() if ln.strip()]
    f["gpiochips"] = parse_gpiodetect(doc.get("gpiodetect") or "")
    f["hwmon"] = kv_lines(doc.get("hwmon"))
    f["thermal_zones"] = [ln for ln in (doc.get("thermal_zones") or "").splitlines() if ln.strip()]
    m = VP["arm_clock"].search(doc.get("arm_clock") or "")
    f["arm_hz"] = int(m.group(2)) if m else None
    return f


def hwmon_rails(hw):
    """[(chip/attr, label, value in SI)] for in/curr/power/temp/fan *_input; label from the matching *_label"""
    chips = {}
    for k, v in hw.items():
        chip, _, attr = k.partition("/")
        chips.setdefault(chip, {})[attr] = v
    out = []
    for chip, attrs in sorted(chips.items()):
        name = attrs.get("name", "")
        for a, v in sorted(attrs.items()):
            m = re.match(r"^(in|curr|power|temp|fan)(\d+)_input$", a)
            if not m:
                continue
            try:
                val = float(v) * RULES["hwmon_scale"][m.group(1)]
            except ValueError:
                continue
            out.append({"chip": chip, "name": name, "attr": a, "kind": m.group(1), "label": attrs.get("%s%s_label" % (m.group(1), m.group(2)), ""), "value": val})
    return out


# ---------------------------------------------------------------- meter CSV and windows
def read_meter(path, cols=None, i_unit=None, base=None, offset=0.0):
    """-> ("series", [(t, v|None, i|None)]) or ("grouped", {label: [amps]})"""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    sample = "\n".join(text.splitlines()[:TH["min_samples"]])
    delim = max(",;\t", key=sample.count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r and not r[0].lstrip().startswith("#")]
    if not rows:
        raise InputError("empty meter file %s" % path)
    head = [c.strip() for c in rows[0]]
    low = [c.lower() for c in head]
    if "device" in low and "amps" in low:
        g = {}
        for r in rows[1:]:
            d = dict(zip(low, (c.strip() for c in r)))
            lab = "%s:%s" % (d["device"], d.get("state", ""))
            g.setdefault(lab if d.get("state") else d["device"], []).append(float(d["amps"]))
        return "grouped", g
    ix = {}
    spec = dict(x.split("=", 1) for x in cols.split(",")) if cols else {}
    for want, rx in (("t", r"^(time|timestamp|t|t_s|ts|utc|datetime|date)\b"), ("v", r"(volt|vbus|^v$|\(v\)|\[v\]|\(mv\)|\[mv\])"),
                     ("i", r"(\bcurr|\bamps?\b|^i$|\(a\)|\[a\]|\(ma\)|\[ma\])")):
        if want in spec:
            if spec[want] not in head:
                raise InputError("column %r not in header %s" % (spec[want], head))
            ix[want] = head.index(spec[want])
            continue
        for n, c in enumerate(low):
            if n not in ix.values() and re.search(rx, c):
                ix[want] = n
                break
    if "t" not in ix or ("v" not in ix and "i" not in ix):
        raise InputError("%s: need a time column and a volts or amps column (header: %s); map them with --meter-cols t=..,v=..,i=.." % (path, head))
    vscale = UNIT["mV"] if "v" in ix and "mv" in low[ix["v"]] else UNIT["V"]
    iscale = UNIT[i_unit] if i_unit else (UNIT["mA"] if "i" in ix and re.search(r"\(ma\)|\[ma\]|_ma\b", low[ix["i"]]) else UNIT["A"])
    series = []
    for r in rows[1:]:
        try:
            t = parse_time(r[ix["t"]], base) + offset
            v = float(r[ix["v"]]) * vscale if "v" in ix else None
            i = float(r[ix["i"]]) * iscale if "i" in ix else None
        except (ValueError, IndexError):
            continue
        series.append((t, v, i))
    series.sort(key=lambda x: x[0])
    return "series", series


def read_windows(path, base):
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = [x.strip() for x in line.split(",")]
            if len(p) <= 2:
                raise InputError("%s:%d: want start,end,label" % (path, n))
            out.append({"t0": parse_time(p[0], base), "t1": parse_time(p[1], base), "label": p[2]})
    return out


# ---------------------------------------------------------------- context
class Ctx:
    def __init__(self, a):
        self.a = a
        self.now = parse_iso(a.now) if a.now else datetime.datetime.now(datetime.timezone.utc).timestamp()
        self.findings, self.overlay, self.notes, self.facts = [], {}, [], {}
        self.doctor = None
        self.samples, self.events, self.meters, self.windows = [], [], {}, []
        self.grouped = {}
        self.base = parse_iso(a.boot_utc) if a.boot_utc else (parse_iso(a.t0_utc) if a.t0_utc else None)
        self.board = a.board

    def find(self, fid, sev, text, evidence=None, tag="INF"):
        self.findings.append({"id": fid, "severity": sev, "text": text, "evidence": evidence or [], "tag": tag})

    def put(self, key, value, lo=None, hi=None, source="", t=None, note="", tag="HW"):
        """overlay entry with source and timestamp; a key is written once (the first derivation wins)"""
        if key in self.overlay and self.overlay[key] is not None:
            return
        e = {"value": sig(value), "source": "%s @%s" % (source, iso(t if t is not None else self.now)),
             "measured_utc": iso(t if t is not None else self.now), "tag": tag}
        if t is None:
            e["measured_utc_basis"] = "ingest-time"
        if lo is not None:
            e["min"] = sig(lo)
        if hi is not None:
            e["max"] = sig(hi)
        if note:
            e["note"] = note
        self.overlay[key] = e


def load_inputs(a):
    c = Ctx(a)
    if a.doctor:
        with open(a.doctor, encoding="utf-8") as f:
            c.doctor = parse_doctor(json.load(f))
        d = c.doctor
        if not c.board:
            for b, name in RULES["board_models"].items():
                if b != "tag" and b != "note" and name in d["board_model"]:
                    c.board = b
        if c.base is None and d["utc"] is not None and d["uptime_s"] is not None:
            c.base = d["utc"] - d["uptime_s"]
            c.notes.append("boot time derived from the doctor snapshot: utc - uptime (kernel timestamps are mapped with it)")
        smp = new_sample(d["utc"])
        smp["throttled"], smp["temp_c"], smp["pmic"], smp["arm_hz"] = d["throttled"], d["temp_c"], d["pmic"], d["arm_hz"]
        c.samples.append(smp)
    for p in a.instr_log or []:
        with open(p, encoding="utf-8") as f:
            c.samples += parse_instr_log(f.read(), c.base)
    c.samples.sort(key=lambda s: (s["t"] is None, s["t"] or 0))
    texts = []
    if a.dmesg:
        with open(a.dmesg, encoding="utf-8", errors="replace") as f:
            texts.append(f.read())
        c.dmesg_source = "file"
    elif c.doctor and c.doctor["dmesg_lines"]:
        texts.append(c.doctor["dmesg_lines"])
        c.dmesg_source = "doctor"
    else:
        c.dmesg_source = None
    for t in texts:
        c.events += parse_dmesg(t, c.base)
    for pos_file in a.meter or []:
        pos, _, path = pos_file.partition(":")
        if not path or pos not in ("psu", "host", "dongle"):
            raise InputError("--meter wants POS:FILE with POS psu|host|dongle, got %r" % pos_file)
        kind, data = read_meter(path, a.meter_cols, a.meter_i_unit, c.base, a.meter_offset_s)
        if kind == "grouped":
            c.grouped[pos] = data
        else:
            c.meters[pos] = data
    if a.windows:
        c.windows = read_windows(a.windows, c.base)
    c.lsusb = parse_lsusb_t(c.doctor["lsusb_tree"]) if c.doctor and c.doctor["lsusb_tree"] else ([], [])
    if a.lsusb_t:
        with open(a.lsusb_t, encoding="utf-8") as f:
            c.lsusb = parse_lsusb_t(f.read())
    c.wfb = None
    if a.wfb_json:
        c.wfb = parse_wfb(a.wfb_json, a.wfb_start_utc)
    return c


# ---------------------------------------------------------------- wfb-ng JSON stats
def parse_wfb(path, start_utc):
    rx_keys = ("all", "out", "fec_rec", "lost", "dec_err", "bad")
    rx = {"n": 0, "totals": dict.fromkeys(rx_keys, 0), "ants": {}, "loss_t": []}
    tx = {"n": 0, "injected": 0, "dropped": 0}
    interval = None
    start = parse_iso(start_utc) if start_utc else None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ty = d.get("type")
            if ty == "settings":
                interval = (d.get("settings", {}).get("common", {}) or {}).get("log_interval")
            elif ty == "rx":
                idx = rx["n"]
                rx["n"] += 1
                for k in rx_keys:
                    rx["totals"][k] += (d.get("packets", {}).get(k) or [0])[0]
                if (d.get("packets", {}).get("lost") or [0])[0] > 0 and start is not None and interval:
                    rx["loss_t"].append(start + idx * interval * UNIT["ms"])
                for a in d.get("rx_ant_stats", []):
                    s = rx["ants"].setdefault(a.get("ant"), {"rssi": [], "snr": [], "pkts": 0})
                    s["rssi"].append(a.get("rssi_avg"))
                    s["snr"].append(a.get("snr_avg"))
                    s["pkts"] += a.get("pkt_recv", 0)
            elif ty == "tx":
                tx["n"] += 1
                tx["injected"] += (d.get("packets", {}).get("injected") or [0])[0]
                tx["dropped"] += (d.get("packets", {}).get("dropped") or [0])[0]
    return {"rx": rx, "tx": tx, "interval_ms": interval}


# ---------------------------------------------------------------- derivations
def radio_paths(c):
    """USB port paths of the radio: --radio-path, else paths whose 'New USB device found' VID:PID is a radio hint"""
    if c.a.radio_path:
        return {c.a.radio_path}
    hints = set(RULES["radio_hints"]["vid_pid"])
    return {e["path"] for e in c.events if e["kind"] == "usb_found" and e["vid_pid"] in hints}


def ext_rows(c):
    return [(s["t"], ext5v(s)) for s in c.samples if s["t"] is not None and ext5v(s) is not None]


def dev_label(label):
    dev, _, state = label.partition(":")
    return dev, state


def derive_currents(c):
    """device and board currents from labelled windows (+ grouped CSVs); median per key, p5..p95 as the range"""
    pooled = {}
    t_last = {}
    down = down_intervals(c)
    for w in c.windows:
        dev, state = dev_label(w["label"])
        if dev.startswith("board_"):
            key = KEY["board_fmt"] % (dev[len("board_"):], state)
            pos_list = ("psu",)
        elif dev == "rtl8812" and state in RADIO_STATE_KEYS:
            key, pos_list = RADIO_STATE_KEYS[state], DEVICE_POS
        elif dev in DEVICE_KEYS:
            key, pos_list = DEVICE_KEYS[dev], DEVICE_POS
        else:
            continue
        for pos in pos_list:
            if pos == "psu":
                rows = [r for r in win_rows(c.meters.get(pos, []), w) if r[2] is not None]
            else:
                rows = [r for r in win_rows(c.meters.get(pos, []), w) if r[2] is not None and not any(a <= r[0] <= b for a, b in down)]
            if len(rows) >= TH["min_samples"]:
                pooled.setdefault((key, pos), []).extend(r[2] for r in rows)
                t_last[(key, pos)] = max(t_last.get((key, pos), w["t1"]), w["t1"])
    for pos, groups in c.grouped.items():
        for lab, amps in groups.items():
            dev, state = dev_label(lab)
            if dev.startswith("board_"):
                key = KEY["board_fmt"] % (dev[len("board_"):], state or "load")
            elif dev == "rtl8812":
                key = RADIO_STATE_KEYS.get(state)
            else:
                key = DEVICE_KEYS.get(dev)
            if key and len(amps) >= 1:
                pooled.setdefault((key, pos), []).extend(amps)
    for (key, pos), xs in sorted(pooled.items()):
        note = "median of %d meter samples (%s meter), range = p5..p95" % (len(xs), pos)
        if key.startswith("power.boards"):
            note += "; the label asserts the board alone is attached (subtract device currents otherwise)"
        c.put(key, statistics.median(xs), pct(xs, TH["pct_lo"]), pct(xs, TH["pct_hi"]), "USB/inline meter, windows %s" % pos, t_last.get((key, pos)), note)


def derive_peak(c):
    for w in c.windows:
        if w["label"] != "rtl8812:tx":
            continue
        for pos in DEVICE_POS:
            rows = device_rows(c, w, (pos,))
            if len(rows) < TH["min_samples"]:
                continue
            span = rows[-1][0] - rows[0][0]
            if span <= 0 or (len(rows) - 1) / span < TH["min_peak_rate_hz"]:
                c.notes.append("tx_peak_factor not derived: meter rate below the limit (a scope is needed for the pulse factor)")
                continue
            xs = [r[2] for r in rows]
            med = statistics.median(xs)
            if med > 0 and pct(xs, TH["pct_peak"]) > med:
                ratio = pct(xs, TH["pct_peak"]) / med
                c.put(KEY["peak"], ratio, ratio, None, "meter p99/median in rtl8812:tx window", w["t1"],
                      "lower bound: sampling filters the pulse; confirm with a scope", "HW")
                return


def window_pairs(c, pos):
    """[(window, mean meter I, mean meter V|None, mean EXT5V_V)] for windows with enough meter and PMIC samples"""
    ext = ext_rows(c)
    out = []
    for w in c.windows:
        m = win_rows(c.meters.get(pos, []), w)
        e = win_rows(ext, w)
        mi = [r[2] for r in m if r[2] is not None]
        if len(mi) < TH["min_samples"] or len(e) < TH["min_samples"]:
            continue
        mv = [r[1] for r in m if r[1] is not None]
        out.append((w, mean(mi), mean(mv) if len(mv) == len(m) else None, mean([r[1] for r in e])))
    return out


def derive_r_path(c):
    pts = window_pairs(c, "psu")
    if not pts:
        return
    t = max(p[0]["t1"] for p in pts)
    direct = [(i, vp - v5) for _w, i, vp, v5 in pts if vp is not None and i >= TH["min_current_for_r_a"]]
    if direct:
        r, lo, hi = through_origin(direct)
        c.put(KEY["r_path"], r, lo, hi, "PSU-side meter V minus PMIC EXT5V_V against meter I, least squares through the origin (%d windows)" % len(direct), t,
              "excludes the PSU's own output droop (meter V taken at the PSU connector); range = 95 %% interval; ADC accuracy UNVERIFIED")
        return
    xs, ys = [i for _w, i, _vp, _v5 in pts], [v5 for _w, _i, _vp, v5 in pts]
    if len(pts) < TH["min_points_r"] or max(xs) - min(xs) < TH["min_delta_i_a"]:
        c.notes.append("cable_resistance_ohm not derived: need >= %d windows with PSU-side current spread >= %g A (or meter volts)" % (TH["min_points_r"], TH["min_delta_i_a"]))
        return
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    icpt = my - slope * mx
    res = [y - (icpt + slope * x) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in res) / (len(xs) - 2) / sxx) if len(xs) > 2 else 0.0
    r = -slope
    if r <= 0:
        c.find("D-R-NEG", "WARN", "EXT5V_V does not fall with the PSU-side current (slope %.4f V/A): resistance not derived" % slope, [], "HW")
        return
    c.put(KEY["r_path"], r, max(0.0, r - TH["z95"] * se), r + TH["z95"] * se, "regression EXT5V_V vs PSU-side current (%d windows)" % len(pts), t,
          "includes PSU output droop; range = 95 %% interval of the slope; ADC accuracy UNVERIFIED")
    c.put(KEY["v_nominal"], icpt, None, None, "regression intercept (no-load voltage at the board input)", t, "extrapolated to zero current", "HW")


def derive_r_usb(c):
    ext = ext_rows(c)
    vals = []
    t = None
    for w in c.windows:
        m = win_rows(c.meters.get("dongle", []), w)
        e = win_rows(ext, w)
        mi = [r[2] for r in m if r[2] is not None]
        mv = [r[1] for r in m if r[1] is not None]
        if len(mi) < TH["min_samples"] or len(mv) < TH["min_samples"] or len(e) < TH["min_samples"] or mean(mi) < TH["min_current_for_r_a"]:
            continue
        vals.append((mean(mi), mean([r[1] for r in e]) - mean(mv)))
        t = w["t1"]
    if vals:
        r, lo, hi = through_origin(vals)
        c.put(KEY["r_usb"], r, lo, hi, "EXT5V_V minus dongle-side meter V against dongle current, least squares through the origin (%d windows)" % len(vals), t,
              "includes the Pi-internal USB path: an upper bound for the cable alone; range = 95 %% interval")


def bracket_combine(c, key, brackets, source, t, note, max_width):
    good = [b for b in brackets if b[1] - b[0] <= max_width]
    if not good:
        if brackets:
            c.notes.append("%s not derived: brackets wider than the limit (%g)" % (key, max_width))
        return
    lo, hi = max(b[0] for b in good), min(b[1] for b in good)
    if lo <= hi:
        c.put(key, (lo + hi) / 2, lo, hi, source + " (%d transitions)" % len(good), t, note)
    else:
        mids = [(b[0] + b[1]) / 2 for b in good]
        c.put(key, statistics.median(mids), min(b[0] for b in good), max(b[1] for b in good), source + " (%d transitions, brackets disagree)" % len(good), t, note)


def derive_thresholds(c):
    seq = [s for s in c.samples if s["t"] is not None and s["throttled"] is not None]
    uv, soft = [], []
    t_uv = t_soft = None
    k = TH["min_samples"]
    for j in range(1, len(seq)):
        a, b = seq[j - 1], seq[j]
        near = seq[max(0, j - k):j + k]
        if not (a["throttled"] & bit(ROLE["uv_now"])) and (b["throttled"] & bit(ROLE["uv_now"])):
            vs = [ext5v(x) for x in near if ext5v(x) is not None]
            if vs:
                uv.append((min(vs), max(vs)))
                t_uv = b["t"]
        if not (a["throttled"] & bit(ROLE["soft_now"])) and (b["throttled"] & bit(ROLE["soft_now"])):
            ts = [x["temp_c"] for x in near if x["temp_c"] is not None]
            if ts:
                soft.append((min(ts), max(ts)))
                t_soft = b["t"]
    bracket_combine(c, KEY["uv_thr"], uv, "EXT5V_V envelope of the %d samples either side of the get_throttled bit0 0->1 transition" % k, t_uv,
                    "envelope, not a confidence interval; PMIC ADC vs detector relation UNVERIFIED", TH["bracket_max_width_v"])
    bracket_combine(c, KEY["soft_limit"], soft, "temperature envelope of the %d samples either side of the get_throttled bit3 0->1 transition" % k, t_soft,
                    "envelope, not a confidence interval", TH["bracket_max_width_c"])


def around_time(rows, t):
    """values of the min_samples rows before t and the min_samples rows after t, from [(t, v)] sorted"""
    ts = [r[0] for r in rows]
    k = bisect.bisect_right(ts, t)
    n = TH["min_samples"]
    return [r[1] for r in rows[max(0, k - n):k + n]]


def uv_intervals(c):
    """[(t0, t1)] where undervoltage was reported: kernel 'detected'..'normalised' pairs and runs of samples with the live flag"""
    out, start = [], None
    for e in c.events:
        if e["t"] is None:
            continue
        if e["kind"] == "uv_detected" and start is None:
            start = e["t"]
        elif e["kind"] == "uv_normalised" and start is not None:
            out.append((start, e["t"]))
            start = None
    if start is not None:
        out.append((start, math.inf))
    run = None
    for s in c.samples:
        if s["t"] is None or s["throttled"] is None:
            continue
        if s["throttled"] & bit(ROLE["uv_now"]):
            run = s["t"] if run is None else run
            last = s["t"]
        elif run is not None:
            out.append((run, last))
            run = None
    if run is not None:
        out.append((run, last))
    return out


def drop_cause(c, e):
    """'overcurrent' (an over-current line right before), 'undervoltage' (a reported undervoltage interval covers it) or 'unknown'"""
    if any(x["kind"] == "usb_overcurrent" and x["t"] is not None and 0 <= e["t"] - x["t"] <= TH["cause_window_s"] for x in c.events):
        return "overcurrent"
    if any(a - TH["cause_window_s"] <= e["t"] <= b + TH["cause_window_s"] for a, b in uv_intervals(c)):
        return "undervoltage"
    return "unknown"


def usb_pairs(c):
    """radio re-enumeration pairs [(disconnect event, new event or None, cause)] on the same port path"""
    rp = radio_paths(c)
    ev = [e for e in c.events if e["t"] is not None]
    out = []
    for i, e in enumerate(ev):
        if e["kind"] != "usb_disconnect" or e["path"] not in rp:
            continue
        nxt = next((x for x in ev[i + 1:] if x["kind"] == "usb_new" and x["path"] == e["path"]), None)
        out.append((e, nxt, drop_cause(c, e)))
    return out


def down_intervals(c):
    """[(t0, t1)] while the radio was disconnected (disconnect .. next 'new' on its port; open end = still down)"""
    return [(e["t"], n["t"] if n else math.inf) for e, n, _cause in usb_pairs(c)]


def device_rows(c, w, positions):
    """meter rows of window w (device-side meters), without the rows recorded while the radio was disconnected"""
    down = down_intervals(c)
    rows = []
    for pos in positions:
        rows += [r for r in win_rows(c.meters.get(pos, []), w) if r[2] is not None and not any(a <= r[0] <= b for a, b in down)]
    return rows


def derive_usb(c):
    pairs = usb_pairs(c)
    sel = [(e, n) for e, n, cause in pairs if n is not None and (cause == "overcurrent" or c.a.reenum_all)]
    skipped = len([1 for _e, n, cause in pairs if n is not None and cause != "overcurrent"])
    if sel and not c.a.reenum_all and skipped:
        c.notes.append("usb_reenum_s: %d re-enumeration(s) after undervoltage/unknown cause excluded (their delay includes the supply recovery or a manual unplug); --reenum-all keeps them" % skipped)
    d = [n["t"] - e["t"] for e, n in sel]
    if d:
        c.put(KEY["reenum"], statistics.median(d), min(d), max(d), "dmesg: radio 'USB disconnect' to next 'new ... USB device' after an over-current line (%d pairs)" % len(d),
              max(n["t"] for _e, n in sel), "kernel-visible remove->add only; excludes driver bind and the wfb-ng restart")
    elif pairs and not skipped:
        c.notes.append("usb_reenum_s not derived: no re-enumeration after the disconnects")
    elif skipped and not sel:
        c.notes.append("usb_reenum_s not derived: only undervoltage/unknown-cause drops (%d); use a forced or over-current drop, or --reenum-all" % skipped)
    ext = ext_rows(c)
    brackets = []
    for e, _n, cause in pairs:
        vs = around_time(ext, e["t"]) if cause == "undervoltage" else []
        if vs:
            brackets.append((min(vs), max(vs)))
    bracket_combine(c, KEY["usb_dropout"], brackets, "EXT5V_V envelope of the samples around the radio disconnect while an undervoltage was reported",
                    max((e["t"] for e, _n, cause in pairs if cause == "undervoltage"), default=None),
                    "the PMIC rail is the board input; the dongle may see a lower voltage; with ADC noise the bracket is only as tight as the voltage slope allows", TH["bracket_max_width_v"])
    if c.a.soak_window:
        a, b = (parse_time(x, c.base) for x in c.a.soak_window.split(","))
        n = sum(1 for e, _n, _cause in pairs if a <= e["t"] <= b)
        hours = (b - a) / TH["seconds_per_hour"]
        if hours >= TH["min_soak_h"] and n >= 1:
            half = TH["z95"] * math.sqrt(n)
            c.put(KEY["drop_rate"], n / hours, max(0.0, n - half) / hours, (n + half) / hours, "dmesg: %d radio disconnects in a %.1f h soak window" % (n, hours), b,
                  "every disconnect in the window counts (cause not separated); range = normal approximation of the Poisson count")
        else:
            c.notes.append("usb.drop_rate_per_h not derived: soak %.2f h (< %g h) or no disconnects (n=%d); with n=0 only an upper bound exists" % (hours, TH["min_soak_h"], n))


def derive_thermal(c):
    if c.a.ambient_c is not None:
        c.put(KEY["ambient"], c.a.ambient_c, None, None, "operator thermometer (--ambient-c)", None, "typed in by the operator, not read from an instrument", "HW")
    rows = [(s["t"], s["temp_c"]) for s in c.samples if s["t"] is not None and s["temp_c"] is not None]
    for w in c.windows:
        dev, _state = dev_label(w["label"])
        if dev != "soc" or c.a.ambient_c is None:
            continue
        r = win_rows(rows, w)
        if len(r) < TH["min_samples"]:
            continue
        half = len(r) // 2
        m1, m2 = statistics.median([x[1] for x in r[:half]]), statistics.median([x[1] for x in r[half:]])
        steady = abs(m2 - m1) <= TH["steady_delta_c"]
        c.put(KEY["soc_rise"], m2 - c.a.ambient_c, None, None, "measure_temp in soc window minus operator ambient", w["t1"],
              "steady-state check %s (second-half minus first-half median %.2f C)" % ("passed" if steady else "FAILED: value is a lower bound", m2 - m1))


# ---------------------------------------------------------------- checks
def usb_budget_a(c):
    """USB current budget in A for the effective configuration; None when it cannot be told (no state read, no claim)"""
    if c.board != "pi5":
        return TH.get("usb_budget_%s_a" % c.board)
    d = c.doctor
    cfg = None
    if d:
        cfg = d["dt_power"].get("usb_max_current_enable")
        if cfg is None:
            cfg = d["usb_max_current_cfg"]
    if cfg is None:
        cfg = c.a.claim_usb_max_current
    if cfg is None and c.a.claim_psu_a is not None:
        cfg = int(c.a.claim_psu_a >= TH["psu_full_budget_a"])
    if cfg is None:
        return None
    return TH["usb_budget_full_a"] if cfg else TH["usb_budget_weak_a"]


def check_claims(c):
    a = c.a
    thr = TH["undervolt_v"]
    uv_samples = [s for s in c.samples if s["throttled"] is not None and s["throttled"] & (bit(ROLE["uv_now"]) | bit(ROLE["uv_ever"]))]
    uv_kernel = [e for e in c.events if e["kind"] == "uv_detected"]
    v_all = [ext5v(s) for s in c.samples if ext5v(s) is not None]
    for r in hwmon_rails(c.doctor["hwmon"]) if c.doctor else []:
        if r["kind"] == "in" and re.search(RULES["hwmon_rail_label"], r["label"]):
            v_all.append(r["value"])
    vmin = min(v_all) if v_all else None
    rec = RULES["board_recommended_psu_a"].get(c.board)
    ev = []
    if uv_samples:
        ev.append("get_throttled undervoltage bits in %d sample(s)" % len(uv_samples))
    if uv_kernel:
        ev.append("%d kernel under-voltage line(s)" % len(uv_kernel))
    if vmin is not None and vmin < thr:
        ev.append("EXT5V_V min %.3f V < %.2f V" % (vmin, thr))
    if a.claim_psu_a is not None and ev and rec is None:
        c.find("K1", "WARN", "undervoltage evidence with a PSU claim, but the board is unknown (use --board): the claim cannot be judged", ev, "HW")
    elif a.claim_psu_a is not None and ev:
        if a.claim_psu_a >= rec:
            c.find("K1", "CONTRADICTION", "claimed PSU %.1f A (>= recommended %.1f A) but undervoltage evidence: the supply path (PSU/cable/connector) does not hold the claim" % (a.claim_psu_a, rec), ev, "HW")
        else:
            c.find("K1", "INFO", "undervoltage evidence with a PSU claimed below the recommendation (%.1f A): consistent" % rec, ev, "INF")
    elif ev:
        c.find("K1", "WARN", "undervoltage evidence (no PSU claim given, use --claim-psu-a to test it)", ev, "HW")
    d = c.doctor
    if d and a.claim_psu_a is not None and d["dt_power"].get("max_current") is not None:
        mx = d["dt_power"]["max_current"] * UNIT[RULES["dt_chosen_power"]["max_current_unit"]]
        if mx < a.claim_psu_a:
            c.find("K2", "CONTRADICTION", "device-tree chosen/power max_current = %.3f A (as negotiated by the bootloader) < claimed PSU %.1f A" % (mx, a.claim_psu_a),
                   ["encoding of the property is UNVERIFIED (read as big-endian u32 in mA)"], "HW")
    cfg = None
    if d:
        cfg = d["dt_power"].get("usb_max_current_enable")
        if cfg is None:
            cfg = d["usb_max_current_cfg"]
    if c.board == "pi5" and cfg is not None and cfg == 0 and (a.claim_usb_max_current == 1 or (a.claim_psu_a is not None and a.claim_psu_a >= TH["psu_full_budget_a"])):
        c.find("K3", "CONTRADICTION", "usb_max_current_enable = 0 while a 5 A PSU / high USB limit is claimed: the Pi 5 limits USB to %.1f A (SRC power-supplies.adoc)" % TH["usb_budget_weak_a"],
               ["claimed: %s" % (("--claim-usb-max-current %s" % a.claim_usb_max_current) if a.claim_usb_max_current is not None else "--claim-psu-a %g" % a.claim_psu_a)], "SRC")
    # soft limit flag with a cool SoC (same sample)
    lo = TH["soft_temp_pi3bp_c"] if c.board == "pi3bp" else TH["soft_temp_lo_c"]
    for s in c.samples:
        if s["throttled"] is not None and s["temp_c"] is not None and s["throttled"] & bit(ROLE["soft_now"]) and s["temp_c"] < lo:
            c.find("K4", "WARN", "soft temperature limit flag with measure_temp %.1f C below the documented %d C" % (s["temp_c"], lo), [iso(s["t"]) if s["t"] else "no time"], "SRC")
            break
    temps = [s["temp_c"] for s in c.samples if s["temp_c"] is not None]
    if temps and max(temps) >= TH["soft_temp_hi_c"] and not any(s["throttled"] is not None and s["throttled"] & (bit(ROLE["soft_ever"]) | bit(ROLE["soft_now"]) | bit(ROLE["throttled_ever"]) | bit(ROLE["capped_ever"])) for s in c.samples):
        c.find("K4b", "WARN", "SoC reached %.1f C (>= %d C) without any throttle flag in the samples (sampling gap or sticky bits cleared)" % (max(temps), TH["soft_temp_hi_c"]), [], "SRC")
    # sticky bits vs kernel lines (the hwmon poller asks the firmware to clear sticky bits, SRC raspberrypi-hwmon.c)
    readable = c.dmesg_source == "file" or (d is not None and d["dmesg_rc"] == "0")
    if uv_kernel and samples_have_flags(c) and not uv_samples:
        c.find("K5", "INFO", "kernel under-voltage line(s) but get_throttled never showed an undervoltage bit: expected when the kernel hwmon poller clears the sticky bits (SRC raspberrypi-hwmon.c); trust the kernel lines",
               [], "SRC")
    if uv_samples and readable and not uv_kernel and any(s["throttled"] & bit(ROLE["uv_ever"]) for s in uv_samples):
        c.find("K5b", "WARN", "undervoltage-has-occurred flag set but no kernel line in the readable log (ring buffer wrapped, or the flag predates the log)", [], "INF")
    if vmin is not None and not uv_samples and not uv_kernel and vmin < thr * (1 - TH["undervolt_tol_frac"]):
        c.find("K6", "WARN", "EXT5V_V %.3f V is below the detector threshold band (%.2f V, tolerance fraction %g) with no undervoltage flag or kernel line: ADC and detector disagree" % (vmin, thr, TH["undervolt_tol_frac"]), [], "SRC")
    elif vmin is not None and not uv_samples and not uv_kernel and vmin < thr:
        c.find("K6", "INFO", "EXT5V_V %.3f V is below %.2f V but inside the detector tolerance band: no flag expected either way" % (vmin, thr), [], "SRC")
    if d and d["dmesg_rc"] not in (None, "0"):
        c.find("K7", "WARN", "dmesg exit status %s: the kernel log was not readable, zero event counts are not evidence" % d["dmesg_rc"], [], "REPO")
    # model assumption: the USB budget is a hard limit
    budget = usb_budget_a(c)
    if budget is not None:
        pairs = usb_pairs(c)
        ocs = [e["t"] for e in c.events if e["kind"] == "usb_overcurrent" and e["t"] is not None]
        for w in c.windows:
            rows = device_rows(c, w, DEVICE_POS)
            if len(rows) < TH["min_samples"]:
                continue
            hi = pct([r[2] for r in rows], TH["pct_peak"])
            acted = any(w["t0"] <= e["t"] <= w["t1"] for e, _n, _cause in pairs) or any(w["t0"] <= t <= w["t1"] for t in ocs)
            if hi > budget and not acted:
                c.find("K8", "WARN", "window %s: current p99 %.2f A above the %.2f A budget (claims) and no over-current line or radio disconnect: the 'hard budget' of the model did not act here" % (w["label"], hi, budget),
                       ["tests/sim/models/power_model.py treats the budget as a hard limit (UNVERIFIED on hardware)"], "HW")
    # meter volts above the PMIC rail
    ext = ext_rows(c)
    for pos in ("host", "dongle"):
        for w in c.windows:
            m = [r[1] for r in win_rows(c.meters.get(pos, []), w) if r[1] is not None]
            e = [r[1] for r in win_rows(ext, w)]
            if len(m) >= TH["min_samples"] and len(e) >= TH["min_samples"] and mean(m) > mean(e) + TH["meter_over_pmic_tol_v"]:
                c.find("K9", "WARN", "%s meter voltage %.3f V above EXT5V_V %.3f V (window %s): meter position or ADC offset" % (pos, mean(m), mean(e), w["label"]), [], "INF")
                break
    if a.board and c.doctor:
        for b, name in RULES["board_models"].items():
            if b not in ("tag", "note") and name in c.doctor["board_model"] and b != a.board:
                c.find("K10", "WARN", "--board %s but the doctor snapshot says %r" % (a.board, c.doctor["board_model"]), [], "INF")
                break
    if c.doctor and (c.board == "pi5" or "Raspberry Pi 5" in c.doctor["board_model"]) and c.doctor.get("gpiochips") is not None and (c.doctor["raw"].get("gpiodetect") or "").strip():
        lbl = RULES["gpio"]["pi5_chip_label"]
        hit = [n for n, lab, _k in c.doctor["gpiochips"] if lab == lbl]
        if len(hit) == 1:
            c.find("K12", "INFO", "RP1 GPIO chip '%s' is gpiochip%d on this system (found by label: the number is not fixed; write it into docs/BOARD-RPI5.md)" % (lbl, hit[0]),
                   [str(c.doctor["gpiochips"])], "HW")
        elif not hit:
            c.find("K12", "WARN", "no GPIO chip with the label '%s' (gpiodetect: %s): the Pi 5 board profile assumes it" % (lbl, [x[1] for x in c.doctor["gpiochips"]]), [], "HW")
        else:
            c.find("K12", "WARN", "%d GPIO chips with the label '%s': gpiofind by line name may pick the wrong one" % (len(hit), lbl), [], "HW")
    if c.board == "pi5" and not v_all and (c.windows or c.doctor):
        c.find("K11", "INFO", "no EXT5V_V in any input: resistance and voltage thresholds cannot be derived (vcgencmd pmic_read_adc is needed, Pi 5)", [], "SRC")


def samples_have_flags(c):
    return any(s["throttled"] is not None for s in c.samples)


# ---------------------------------------------------------------- report
CANDIDATES = [KEY[k] for k in ("dev_idle", "dev_rx", "dev_tx", "fc", "webcam", "fan", "peak", "r_path", "v_nominal", "r_usb", "uv_thr", "usb_dropout", "reenum", "drop_rate", "soc_rise", "soft_limit", "ambient")]


def candidate_keys(c):
    keys = list(CANDIDATES)
    boards = [c.board] if c.board else [b for b in RULES["board_models"] if b not in ("tag", "note")]
    for b in boards:
        for st in ("idle", "active", "load"):
            keys.append(KEY["board_fmt"] % (b, st))
    return keys


def validate_keys(overlay, models_dir):
    if not models_dir or not os.path.isdir(models_dir):
        return None
    sys.path.insert(0, models_dir)
    import common
    leaves = set()
    for fn in ("params.json", "params.degrade.json"):
        with open(os.path.join(models_dir, fn), encoding="utf-8") as f:
            doc = json.load(f)
        for sec, body in doc["sections"].items():
            leaves |= {k for k, _ in common.iter_leaves(body.get("params", {}), sec + ".")}
    return sorted(k for k in overlay if k not in leaves)


def build_report(c):
    ov = {k: c.overlay.get(k) for k in candidate_keys(c)}
    ov.update({k: v for k, v in c.overlay.items() if k not in ov})
    sample_t = [s["t"] for s in c.samples if s["t"] is not None]
    thr_or = 0
    for s in c.samples:
        thr_or |= s["throttled"] or 0
    v_all = [ext5v(s) for s in c.samples if ext5v(s) is not None]
    kinds = {}
    for e in c.events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    rep = {"schema": SCHEMA, "utc": iso(c.now), "board": c.board, "notes": c.notes,
           "inputs": {"doctor": bool(c.doctor), "samples": len(c.samples), "dmesg_events": len(c.events), "meters": sorted(list(c.meters) + list(c.grouped)), "windows": len(c.windows),
                      "wfb": bool(c.wfb)},
           "throttled": {"or_all": thr_or, "bits": [RULES["throttled_bits"][str(b)] for b in sorted(int(x) for x in RULES["throttled_bits"] if x.isdigit()) if thr_or & bit(b)]},
           "ext5v_v": {"n": len(v_all), "min": min(v_all) if v_all else None, "max": max(v_all) if v_all else None},
           "temp_c": {"max": max([s["temp_c"] for s in c.samples if s["temp_c"] is not None], default=None)},
           "kernel_events": kinds, "span_s": (max(sample_t) - min(sample_t)) if len(sample_t) > 1 else None,
           "findings": sorted(c.findings, key=lambda f: (SEV_ORDER[f["severity"]], f["id"])), "overlay": ov}
    d = c.doctor
    if d:
        rep["doctor"] = {"board_model": d["board_model"], "uptime_s": d["uptime_s"], "usb_max_current_cfg": d["usb_max_current_cfg"], "dt_chosen_power": d["dt_power"],
                         "dt_chosen_power_raw": d["dt_power_raw"], "dmesg_rc": d["dmesg_rc"], "usb_sysfs": d["usb_sysfs"], "usb_over_current_count": d["usb_oc_count"],
                         "hwmon": hwmon_rails(d["hwmon"]), "thermal_zones": d["thermal_zones"], "pmic_rails": {k: v[2] for k, v in d["pmic"].items()}}
        mx = d["dt_power"].get("max_current")
        if mx is not None:
            rep["scenario_cfg_hint"] = {"psu_a": mx * UNIT["mA"], "usb_max_current": bool(d["dt_power"].get("usb_max_current_enable") or d["usb_max_current_cfg"]),
                                        "note": "for scenarios/*.json cfg of tests/sim/models; the overlay holds parameters only"}
    buses, devs = c.lsusb
    rep["usb_tree"] = {"buses": buses, "devices": devs}
    if c.wfb:
        rx, tx = c.wfb["rx"], c.wfb["tx"]
        rep["wfb"] = {"rx_reports": rx["n"], "rx_totals": rx["totals"], "tx_reports": tx["n"], "tx_injected": tx["injected"], "tx_dropped": tx["dropped"], "interval_ms": c.wfb["interval_ms"],
                      "antennas": {str(k): {"rssi_avg_mean": mean([x for x in v["rssi"] if x is not None]) if any(x is not None for x in v["rssi"]) else None,
                                            "snr_avg_mean": mean([x for x in v["snr"] if x is not None]) if any(x is not None for x in v["snr"]) else None, "pkts": v["pkts"]}
                                   for k, v in rx["ants"].items()}, "loss_seconds": len(rx["loss_t"])}
        pairs = usb_pairs(c)
        if rx["loss_t"] and pairs:
            win = [(e["t"], (n["t"] if n else e["t"]) + TH["cause_window_s"]) for e, n, _cause in pairs]
            expl = sum(1 for t in rx["loss_t"] if any(a <= t <= b for a, b in win))
            rep["wfb"]["loss_seconds_inside_usb_drops"] = expl
    return rep


def print_report(rep, out):
    p = lambda s="": print(s, file=out)  # noqa: E731
    p("# ingest report (%s) board=%s utc=%s" % (SCHEMA, rep["board"], rep["utc"]))
    i = rep["inputs"]
    p("inputs: doctor=%s samples=%d dmesg_events=%d meters=%s windows=%d wfb=%s" % (i["doctor"], i["samples"], i["dmesg_events"], ",".join(i["meters"]) or "-", i["windows"], i["wfb"]))
    t = rep["throttled"]
    p("get_throttled OR of all samples: 0x%x  %s" % (t["or_all"], "; ".join(t["bits"]) or "none"))
    e5 = rep["ext5v_v"]
    p("EXT5V_V: n=%d min=%s max=%s   SoC temp max=%s" % (e5["n"], "%.3f" % e5["min"] if e5["min"] is not None else "-", "%.3f" % e5["max"] if e5["max"] is not None else "-", rep["temp_c"]["max"]))
    p("kernel events: %s" % (", ".join("%s=%d" % kv for kv in sorted(rep["kernel_events"].items())) or "none"))
    if "scenario_cfg_hint" in rep:
        h = rep["scenario_cfg_hint"]
        p("scenario cfg hint: psu_a=%.3f usb_max_current=%s" % (h["psu_a"], h["usb_max_current"]))
    if "wfb" in rep:
        w = rep["wfb"]
        p("wfb: rx reports=%d lost=%d fec_rec=%d dec_err=%d bad=%d; tx injected=%d dropped=%d; loss seconds=%d%s" % (
            w["rx_reports"], w["rx_totals"]["lost"], w["rx_totals"]["fec_rec"], w["rx_totals"]["dec_err"], w["rx_totals"]["bad"], w["tx_injected"], w["tx_dropped"], w["loss_seconds"],
            (" (inside USB drops: %d)" % w["loss_seconds_inside_usb_drops"]) if "loss_seconds_inside_usb_drops" in w else ""))
    p("findings:")
    for f in rep["findings"] or [{"severity": "-", "id": "-", "text": "none", "evidence": []}]:
        p("  [%s] %s: %s" % (f["severity"], f["id"], f["text"]))
        for x in f["evidence"]:
            p("      - %s" % x)
    for n in rep["notes"]:
        p("note: %s" % n)
    p("overlay (null = not derived from these inputs):")
    for k, v in rep["overlay"].items():
        if v is None:
            p("  %-44s null" % k)
        else:
            p("  %-44s %s  [%s..%s]  %s" % (k, v["value"], v.get("min", "-"), v.get("max", "-"), v["source"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doctor")
    ap.add_argument("--instr-log", action="append", help="timestamped vcgencmd log (repeatable); alias --pmic-log")
    ap.add_argument("--pmic-log", dest="instr_log", action="append", help=argparse.SUPPRESS)
    ap.add_argument("--dmesg")
    ap.add_argument("--lsusb-t")
    ap.add_argument("--meter", action="append", help="POS:FILE, POS = psu|host|dongle")
    ap.add_argument("--meter-cols", help="t=Time,v=Volts,i=Amps (header names)")
    ap.add_argument("--meter-i-unit", choices=("A", "mA"))
    ap.add_argument("--meter-offset-s", type=float, default=0.0, help="meter clock minus the Pi clock is removed from the meter times")
    ap.add_argument("--windows")
    ap.add_argument("--wfb-json")
    ap.add_argument("--wfb-start-utc")
    ap.add_argument("--board", choices=[b for b in RULES["board_models"] if b not in ("tag", "note")])
    ap.add_argument("--claim-psu-a", type=float)
    ap.add_argument("--claim-usb-max-current", type=int, choices=(0, 1))
    ap.add_argument("--ambient-c", type=float)
    ap.add_argument("--boot-utc")
    ap.add_argument("--t0-utc")
    ap.add_argument("--radio-path")
    ap.add_argument("--reenum-all", action="store_true", help="also use re-enumerations without an over-current cause for usb_reenum_s")
    ap.add_argument("--soak-window", help="START,END of an undisturbed soak for the USB drop rate")
    ap.add_argument("--now", help="ISO UTC used as the report time (tests)")
    ap.add_argument("--overlay", help="write the overlay for calib.py whatif")
    ap.add_argument("--report-json")
    ap.add_argument("--models", default=os.path.join(HERE, "..", "tests", "sim", "models"))
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    try:
        c = load_inputs(a)
        derive_currents(c)
        derive_peak(c)
        derive_r_path(c)
        derive_r_usb(c)
        derive_thresholds(c)
        derive_usb(c)
        derive_thermal(c)
        check_claims(c)
        rep = build_report(c)
        bad = validate_keys(rep["overlay"], a.models)
        if bad:
            raise InputError("overlay keys unknown to the model parameter files: %s" % ", ".join(bad))
    except (InputError, OSError, KeyError, ValueError) as e:
        print("ingest: error: %s" % e, file=sys.stderr)
        return 2
    print_report(rep, sys.stdout)
    if a.overlay:
        with open(a.overlay, "w", encoding="utf-8") as f:
            json.dump(rep["overlay"], f, indent=1)
            f.write("\n")
    if a.report_json:
        with open(a.report_json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
            f.write("\n")
    if a.strict and any(x["severity"] == "CONTRADICTION" for x in rep["findings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
