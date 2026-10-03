#!/usr/bin/env python3
"""5 V / USB power budget model for Raspberry Pi 3B+/4/5 as a GS or AIR node, plus brown-out fault injection.

A MODEL, not a proof: static currents per state, one lumped cable resistance, the documented USB budget
treated as a hard limit. It ranks setups (which board/PSU/adapter combination browns out first) and
produces a replayable USB fault timeline; it cannot certify that a real supply holds. Every number is in
params.json with provenance; measured values override it (common.py: MODEL_MEASURED / MODEL_SET / --set).

Subcommands
  report    --board pi5 --psu-a 3 --adapter-state tx --with fc,webcam,fan [--throttled-log FILE]
  brownout  --scenario pi5_3a_tx | --scenario-file F.json [--seed N]     JSON events (schema sbc-gs-usbfault/1)
  throttled 0x50005 ... | --file LOG       decode `vcgencmd get_throttled` values / dmesg under-voltage lines
  ingest    --csv meter.csv                 USB-meter CSV (device,state,amps) -> overlay JSON for MODEL_MEASURED

Event schema (consumed by later virtual-USB tests), one JSON object:
  {"schema": "sbc-gs-usbfault/1", "scenario": str, "board": str, "seed": int, "duration_s": float,
   "events": [ {"t_s": float, "kind": "undervoltage"|"voltage_ok"|"throttled"|"usb_drop"|"usb_return", ...} ]}
  undervoltage {"v": volts}                   rail fell below power.undervolt_threshold_v
  voltage_ok   {"v": volts}                   rail is back above it
  throttled    {"value": "0x50005", "bits": [names]}   vcgencmd get_throttled value after this instant
  usb_drop     {"device": id, "bus_port": str, "reason": "undervoltage"|"overcurrent", "v": volts, "i_usb_a": amps}
  usb_return   {"device": id, "bus_port": str}                device re-enumerates
"""
import argparse
import csv
import json
import random
import re
import statistics
import sys

import common

BOARDS = ("pi3bp", "pi4", "pi5")
# vcgencmd get_throttled bits (SRC: raspberrypi/documentation os/graphics-utilities.adoc, section get_throttled)
THROTTLED_BITS = {
    0: "Undervoltage detected", 1: "Arm frequency capped", 2: "Currently throttled",
    3: "Soft temperature limit active", 16: "Undervoltage has occurred", 17: "Arm frequency capping has occurred",
    18: "Throttling has occurred", 19: "Soft temperature limit has occurred",
}
UV_NOW, UV_EVER, THR_NOW, THR_EVER = 1 << 0, 1 << 16, 1 << 2, 1 << 18

SCENARIOS = {
    "pi5_3a_tx": {
        "board": "pi5", "psu_a": 3.0, "usb_max_current": False, "duration_s": 30.0, "load": "active",
        "devices": [{"id": "rtl8812", "kind": "rtl8812", "bus_port": "1-1"}, {"id": "fc", "kind": "fc", "bus_port": "1-2"},
                    {"id": "webcam", "kind": "webcam", "bus_port": "3-1"}],
        "adapter_profile": [{"t_s": 0.0, "state": "rx"}, {"t_s": 5.0, "state": "tx"}], "dips": [], "extra_5v": ["fan"],
    },
    "pi4_3a_dip": {
        "board": "pi4", "psu_a": 3.0, "usb_max_current": False, "duration_s": 30.0, "load": "load",
        "devices": [{"id": "rtl8812", "kind": "rtl8812", "bus_port": "1-1.1"}, {"id": "fc", "kind": "fc", "bus_port": "1-1.2"}],
        "adapter_profile": [{"t_s": 0.0, "state": "rx"}, {"t_s": 8.0, "state": "tx"}],
        "dips": [{"t_s": 15.0, "depth_v": 0.35, "duration_s": 0.4}], "extra_5v": ["fan"],
    },
    "pi5_5a_ok": {
        "board": "pi5", "psu_a": 5.0, "usb_max_current": False, "duration_s": 30.0, "load": "active",
        "devices": [{"id": "rtl8812", "kind": "rtl8812", "bus_port": "1-1"}, {"id": "fc", "kind": "fc", "bus_port": "1-2"}],
        "adapter_profile": [{"t_s": 0.0, "state": "tx"}], "dips": [], "extra_5v": [],
    },
}


# ---------------------------------------------------------------- budget
def usb_budget_a(P, board, psu_a, usb_max_current=False):
    if board == "pi5" and (usb_max_current or psu_a >= 5.0):
        return P.get("power.boards.pi5.usb_budget_a")
    return P.get("power.boards.%s.usb_budget_weak_psu_a" % board)


def device_a(P, kind, state="tx", peak=False):
    if kind == "rtl8812":
        a = P.get("power.devices.rtl8812_%s_a" % state)
        return a * (P.get("power.tx_peak_factor") if (peak and state == "tx") else 1.0)
    return P.get({"fc": "power.devices.fc_usb_a", "webcam": "power.devices.webcam_a", "fan": "power.devices.fan_a",
                  "csi": "power.devices.csi_camera_a"}[kind])


def budget(P, board, psu_a=None, adapters=1, state="tx", with_=(), load="active", peak=False, usb_max_current=False):
    if board not in BOARDS:
        raise common.ParamError("board must be one of %s" % ",".join(BOARDS))
    rec = P.get("power.boards.%s.psu_recommended_a" % board)
    psu = rec if psu_a is None else psu_a
    items = [("board_" + load, P.get("power.boards.%s.board_%s_a" % (board, load)), "psu")]
    if adapters:
        items.append(("rtl8812_%s x%d%s" % (state, adapters, " peak" if peak else ""), adapters * device_a(P, "rtl8812", state, peak), "usb"))
    for w in with_:
        items.append((w, device_a(P, w), "psu" if w in ("fan", "csi") else "usb"))
    usb = sum(a for _n, a, c in items if c == "usb")
    total = sum(a for _n, a, _c in items)
    ub = usb_budget_a(P, board, psu, usb_max_current)
    v = P.get("power.psu_nominal_v") - total * P.get("power.cable_resistance_ohm")
    flags = []
    if total > psu:
        flags.append("PSU_OVER")
    elif psu - total < P.get("power.margin_warn_frac") * psu:
        flags.append("LOW_PSU_MARGIN")
    if usb > ub:
        flags.append("USB_OVER")
    if v < P.get("power.undervolt_threshold_v"):
        flags.append("UNDERVOLT")
    verdict = "FAIL" if set(flags) & {"PSU_OVER", "USB_OVER", "UNDERVOLT"} else ("WARN" if flags else "OK")
    return {"board": board, "psu_a": psu, "psu_recommended_a": rec, "items": items, "usb_a": usb, "total_a": total,
            "usb_budget_a": ub, "psu_margin_a": psu - total, "usb_margin_a": ub - usb, "v_board": v,
            "flags": flags, "verdict": verdict}


# ---------------------------------------------------------------- throttled decode
def decode_throttled(value):
    return [THROTTLED_BITS[b] for b in sorted(THROTTLED_BITS) if value & (1 << b)]


def parse_log(text):
    """Extract get_throttled values and kernel under-voltage lines from a log."""
    vals = [int(m, 16) for m in re.findall(r"throttled=(0x[0-9a-fA-F]+)", text)]
    if not vals:  # bare hex lines
        vals = [int(m, 16) for m in re.findall(r"^\s*(0x[0-9a-fA-F]+)\s*$", text, re.M)]
    uv_lines = len(re.findall(r"under-?voltage detected", text, re.I))
    ok_lines = len(re.findall(r"voltage normali[sz]ed", text, re.I))
    return vals, uv_lines, ok_lines


def summarize_log(vals, uv_lines):
    ever = 0
    for v in vals:
        ever |= v
    return {"samples": len(vals), "or_all": ever, "undervoltage_seen": bool(ever & (UV_NOW | UV_EVER)) or uv_lines > 0,
            "throttled_seen": bool(ever & (THR_NOW | THR_EVER)), "dmesg_undervoltage_lines": uv_lines}


# ---------------------------------------------------------------- brownout fault injection
def timeline(P, sc, seed, dt=0.05):
    rnd = random.Random(seed)
    board, psu = sc["board"], sc["psu_a"]
    ub = usb_budget_a(P, board, psu, sc.get("usb_max_current", False))
    nominal = P.get("power.psu_nominal_v")
    r_cab = P.get("power.cable_resistance_ohm")
    thr_v = P.get("power.undervolt_threshold_v")
    drop_v = P.get("power.usb_dropout_v")
    reenum = P.get("power.usb_reenum_s")
    base = P.get("power.boards.%s.board_%s_a" % (board, sc.get("load", "active")))
    extra = sum(device_a(P, x) for x in sc.get("extra_5v", []))
    prof = sorted(sc["adapter_profile"], key=lambda x: x["t_s"])
    dips = [(d["t_s"] + rnd.uniform(-0.05, 0.05), d["depth_v"], d["duration_s"]) for d in sc.get("dips", [])]
    phase = rnd.random()
    devs = {d["id"]: d for d in sc["devices"]}
    order = sc.get("drop_order") or [d["id"] for d in sc["devices"]]
    down = {}  # id -> t when it may return
    ev = []
    sticky = 0
    uv_now = False
    steps = int(round(sc["duration_s"] / dt))
    for i in range(steps + 1):
        t = i * dt
        for did in [d for d, tu in down.items() if t >= tu]:
            ev.append({"t_s": round(t, 3), "kind": "usb_return", "device": did, "bus_port": devs[did]["bus_port"]})
            del down[did]
        state = [p for p in prof if p["t_s"] <= t][-1]["state"] if prof else "idle"
        burst = ((t * 10 + phase) % 1.0) < 0.3
        cur = {}
        for did, d in devs.items():
            if did in down:
                continue
            cur[did] = device_a(P, d["kind"], state, burst) if d["kind"] == "rtl8812" else device_a(P, d["kind"])
        usb = sum(cur.values())
        depth = sum(dp for (t0, dp, du) in dips if t0 <= t < t0 + du)
        v = nominal - (base + extra + usb) * r_cab - depth
        # kernel under-voltage bookkeeping
        if v < thr_v and not uv_now:
            uv_now = True
            sticky |= UV_EVER | THR_EVER
            ev.append({"t_s": round(t, 3), "kind": "undervoltage", "v": round(v, 3)})
            val = UV_NOW | THR_NOW | sticky
            ev.append({"t_s": round(t, 3), "kind": "throttled", "value": "0x%x" % val, "bits": decode_throttled(val)})
        elif v >= thr_v and uv_now:
            uv_now = False
            ev.append({"t_s": round(t, 3), "kind": "voltage_ok", "v": round(v, 3)})
            ev.append({"t_s": round(t, 3), "kind": "throttled", "value": "0x%x" % sticky, "bits": decode_throttled(sticky)})
        # USB drops: undervoltage first, then over-budget (policy INF: highest-draw device resets)
        reason = None
        if v < drop_v:
            reason = "undervoltage"
        elif usb > ub:
            reason = "overcurrent"
        if reason and cur:
            cand = [x for x in order if x in cur] if reason == "undervoltage" else sorted(cur, key=lambda x: -cur[x])
            did = cand[0]
            down[did] = t + reenum
            ev.append({"t_s": round(t, 3), "kind": "usb_drop", "device": did, "bus_port": devs[did]["bus_port"],
                       "reason": reason, "v": round(v, 3), "i_usb_a": round(usb, 3)})
    return ev


def build_events(P, name, sc, seed):
    return {"schema": "sbc-gs-usbfault/1", "scenario": name, "board": sc["board"], "seed": seed,
            "duration_s": sc["duration_s"],
            "model": {"psu_a": sc["psu_a"], "usb_budget_a": usb_budget_a(P, sc["board"], sc["psu_a"], sc.get("usb_max_current", False)),
                      "undervolt_threshold_v": P.get("power.undervolt_threshold_v"),
                      "usb_dropout_v": P.get("power.usb_dropout_v"), "usb_reenum_s": P.get("power.usb_reenum_s")},
            "events": timeline(P, sc, seed)}


# ---------------------------------------------------------------- CLI
def fmt_report(b, a, thr_v):
    out = ["# power_model: board=%s psu=%.1fA (recommended %.1fA) adapter=%dx%s load=%s usb_max_current=%d"
           % (b["board"], b["psu_a"], b["psu_recommended_a"], a.adapters, a.adapter_state, a.load, int(a.usb_max_current)),
           "item,amps,counts_against"]
    out += ["%s,%.3f,%s" % it for it in b["items"]]
    out += ["total_a=%.3f psu_margin_a=%+.3f" % (b["total_a"], b["psu_margin_a"]),
            "usb_a=%.3f usb_budget_a=%.2f usb_margin_a=%+.3f" % (b["usb_a"], b["usb_budget_a"], b["usb_margin_a"]),
            "v_board=%.3fV (undervolt threshold %.2fV)" % (b["v_board"], thr_v),
            "flags=%s" % (",".join(b["flags"]) or "-"), "verdict=%s" % b["verdict"]]
    return out


def cmd_report(P, a):
    with_ = [x for x in a.with_.split(",") if x]
    for w in with_:
        if w not in ("fc", "webcam", "fan", "csi"):
            raise common.ParamError("--with items: fc,webcam,fan,csi")
    b = budget(P, a.board, a.psu_a, a.adapters, a.adapter_state, with_, a.load, a.peak, a.usb_max_current)
    out = fmt_report(b, a, P.get("power.undervolt_threshold_v"))
    if a.throttled_log:
        with open(a.throttled_log, encoding="utf-8") as f:
            vals, uvl, _ = parse_log(f.read())
        s = summarize_log(vals, uvl)
        out.append("measured: samples=%d or_all=0x%x undervoltage_seen=%d throttled_seen=%d dmesg_uv_lines=%d"
                   % (s["samples"], s["or_all"], s["undervoltage_seen"], s["throttled_seen"], s["dmesg_undervoltage_lines"]))
        if s["undervoltage_seen"] and b["verdict"] != "FAIL":
            out.append("agreement=MODEL_OPTIMISTIC (undervoltage measured, model says %s): raise power.cable_resistance_ohm or the device currents" % b["verdict"])
        elif not s["undervoltage_seen"] and "UNDERVOLT" in b["flags"]:
            out.append("agreement=MODEL_PESSIMISTIC (model predicts UNDERVOLT, none measured): lower power.cable_resistance_ohm")
        else:
            out.append("agreement=CONSISTENT (model %s, undervoltage_seen=%d)" % (b["verdict"], s["undervoltage_seen"]))
    return out


def cmd_brownout(P, a):
    if a.scenario_file:
        with open(a.scenario_file, encoding="utf-8") as f:
            sc = json.load(f)
        name = sc.get("name", a.scenario_file)
    else:
        if a.scenario not in SCENARIOS:
            raise common.ParamError("scenario must be one of %s" % ",".join(sorted(SCENARIOS)))
        sc, name = SCENARIOS[a.scenario], a.scenario
    return [json.dumps(build_events(P, name, sc, a.seed), indent=1)]


def cmd_throttled(P, a):
    out = []
    vals = [int(x, 16) for x in a.values]
    uvl = 0
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            v2, uvl, _ = parse_log(f.read())
        vals += v2
    for v in vals:
        out.append("0x%x: %s" % (v, "; ".join("bit%d %s" % (b, THROTTLED_BITS[b]) for b in sorted(THROTTLED_BITS) if v & (1 << b)) or "none"))
    s = summarize_log(vals, uvl)
    out.append("summary: samples=%d or_all=0x%x undervoltage_seen=%d throttled_seen=%d dmesg_uv_lines=%d"
               % (s["samples"], s["or_all"], s["undervoltage_seen"], s["throttled_seen"], s["dmesg_undervoltage_lines"]))
    return out


METER_MAP = {("rtl8812", "idle"): "power.devices.rtl8812_idle_a", ("rtl8812", "rx"): "power.devices.rtl8812_rx_a",
             ("rtl8812", "tx"): "power.devices.rtl8812_tx_a", ("fc", ""): "power.devices.fc_usb_a",
             ("webcam", ""): "power.devices.webcam_a", ("fan", ""): "power.devices.fan_a"}


def cmd_ingest(P, a):
    groups = {}
    with open(a.csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dev, state, amps = row["device"].strip(), (row.get("state") or "").strip(), float(row["amps"])
            if dev.startswith("board_"):
                key = "power.boards.%s.board_%s_a" % (dev[6:], state or "load")
            else:
                key = METER_MAP.get((dev, state if dev == "rtl8812" else ""))
            if key is None or key not in P.leaves:
                raise common.ParamError("no model parameter for device=%s state=%s" % (dev, state))
            groups.setdefault(key, []).append(amps)
    overlay = {k: {"value": round(statistics.median(v), 4), "min": round(min(v), 4), "max": round(max(v), 4),
                   "source": "USB meter CSV %s n=%d (median)" % (a.csv, len(v))} for k, v in sorted(groups.items())}
    return [json.dumps(overlay, indent=1)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--board", required=True, choices=BOARDS)
    r.add_argument("--psu-a", type=float, help="PSU current rating (default: the documented recommendation)")
    r.add_argument("--adapters", type=int, default=1)
    r.add_argument("--adapter-state", choices=("idle", "rx", "tx"), default="tx")
    r.add_argument("--with", dest="with_", default="", help="comma list: fc,webcam,fan,csi")
    r.add_argument("--load", choices=("idle", "active", "load"), default="active")
    r.add_argument("--peak", action="store_true", help="use the TX pulse current (tx_peak_factor)")
    r.add_argument("--usb-max-current", action="store_true", help="Pi 5 usb_max_current_enable=1")
    r.add_argument("--throttled-log", help="log with vcgencmd get_throttled / dmesg lines to compare against")
    b = sub.add_parser("brownout")
    b.add_argument("--scenario", default="pi5_3a_tx")
    b.add_argument("--scenario-file")
    b.add_argument("--seed", type=int, default=1)
    t = sub.add_parser("throttled")
    t.add_argument("values", nargs="*")
    t.add_argument("--file")
    i = sub.add_parser("ingest")
    i.add_argument("--csv", required=True)
    for sp in (r, b, t, i):
        common.add_cli(sp)
    a = ap.parse_args(argv)
    try:
        P = common.from_args(a)
        out = {"report": cmd_report, "brownout": cmd_brownout, "throttled": cmd_throttled, "ingest": cmd_ingest}[a.cmd](P, a)
    except (common.ParamError, OSError, ValueError, KeyError) as e:
        print("power_model: error: %s" % e, file=sys.stderr)
        return 2
    print("\n".join(out))
    if a.cmd == "report":
        print(P.footer())
    elif a.cmd == "brownout":
        print(P.footer(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
