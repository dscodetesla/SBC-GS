#!/usr/bin/env python3
"""Mutation table of tests/sim/evdev/mutate.sh: id -> (description, file relative to the repo copy, old text, new text).
Each 'old' text must occur exactly once. mutants.py list | apply <id> <repo-copy-root>."""
import sys

M = {
    "M1": ("EvdevSource.poll() keeps reporting fresh after the device is lost (dead-man waits for the timer)", "bench/tx12_bridge.py",
           "        if self.lost:\n            return None, None", "        if False:\n            return None, None"),
    "M2": ("axis inversion (reverse) lost in map_axis", "bench/tx12_bridge.py",
           '        if cfg.get("reverse"):\n            n = -n', '        if False:\n            n = -n'),
    "M3": ("deadband ignored in map_axis", "bench/tx12_bridge.py", "        if abs(n) <= db:", "        if False:"),
    "M4": ("clamp removed from clamp_us", "bench/tx12_bridge.py",
           "    return max(LO, min(HI, int(round(v))))", "    return int(round(v))"),
    "M5": ("SYN_DROPPED resync (EVIOCGABS) removed", "bench/tx12_bridge.py", "pending = self._resync()", "pending = {}"),
    "M6": ("axes applied per event instead of at SYN_REPORT (torn frames)", "bench/tx12_bridge.py",
           "                                pending[ev.code] = ev.value\n",
           "                                pending[ev.code] = ev.value\n                                with self.lock:\n                                    self.raw[ev.code] = ev.value\n"),
    "M7": ("example map back to the -1024..1024 placeholder (centred stick = full deflection)", "bench/tx12_map.example.json",
           '"ABS_X":  {"channel": 1, "min": 0, "max": 2047, "center": 1024,', '"ABS_X":  {"channel": 1, "min": -1024, "max": 1024, "center": 0,'),
    "M8": ("ambiguous device selector is not refused", "bench/tx12_bridge.py", "    if len(hits) > 1:", "    if False:"),
    "M9": ("two axes may drive the same channel", "bench/tx12_bridge.py", "        if ch in used:", "        if False:"),
    "M10": ("lost device: bridge never exits (rc 0 instead of 4)", "bench/tx12_bridge.py", "                    rc = 4  # cfg-ok", "                    rc = 0  # cfg-ok"),
    "M11": ("'device' identity guard ignored", "bench/tx12_bridge.py", "        if bad:\n            self.dev.close()", "        if False:\n            self.dev.close()"),
    "M12": ("EAGAIN from read() treated as device loss", "bench/tx12_bridge.py", "except BlockingIOError:", "except ZeroDivisionError:"),
    "M13": ("main loop: dead-man disabled", "bench/tx12_bridge.py", 'elif state == "ACTIVE" and not fresh:', 'elif state == "ACTIVE" and not fresh and False:'),
    "M14": ("failsafe frame does not force the throttle value", "bench/tx12_bridge.py", "        v[a.throttle_ch - 1] = a.failsafe_throttle", "        pass"),
    "M15": ("unusable lock path is not a clean refusal (traceback again)", "bench/tx12_bridge.py",
            "    except OSError as e:\n        print(f\"[tx12_bridge] REFUSED: cannot take", "    except ZeroDivisionError as e:\n        print(f\"[tx12_bridge] REFUSED: cannot take"),
    "M16": ("hat/HID mapping table: ABS_THROTTLE and ABS_RUDDER swapped in the emulator profile helper", "tests/sim/evdev/vdev.py",
            "    return usage & 0xF", "    return {0x36: 7, 0x37: 6}.get(usage, usage & 0xF)"),
}

if __name__ == "__main__":
    if sys.argv[1] == "list":
        print(" ".join(M))
    elif sys.argv[1] == "name":
        print(M[sys.argv[2]][0])
    elif sys.argv[1] == "apply":
        _, f, old, new = M[sys.argv[2]]
        p = sys.argv[3] + "/" + f
        s = open(p).read()
        if s.count(old) != 1:
            sys.exit("mutation %s: pattern occurs %d times in %s" % (sys.argv[2], s.count(old), f))
        open(p, "w").write(s.replace(old, new))
