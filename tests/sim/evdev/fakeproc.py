#!/usr/bin/env python3
"""The REAL bench/tx12_bridge.py main loop with an in-process fake `evdev` module (vdev.FakeEvdevModule) instead of the kernel.

    fakeproc.py [--profile F] [--plug PATH]... -- <tx12_bridge.py arguments>

Plugs one fake device (default /dev/input/event7; --plug may repeat, each gets the profile; the last --plug-profile NAME=... is not
needed here) and then runs tx12_bridge.main(argv) in this process (main thread, so its signal handlers work). stdin carries commands for the
fake kernel, one per line, so a test can drive it without /dev/uinput:
    set ABS_X=2047 ABS_Y=0        one SYN packet
    raw 3:0:2047 0:3:0            raw events type:code:value (no implicit SYN); 0:0:0 is SYN_REPORT, 0:3:0 is SYN_DROPPED
    silent ABS_X=2047             change the kernel state without any event (what a SYN_DROPPED overrun loses)
    destroy [PATH]                unplug
SYNTH: only the python-evdev calls EvdevSource makes exist; docs/SIM-EVDEV.md. Never talks to hardware.
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("EVDEV_ROOT") or os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "bench"), HERE]
import vdev  # noqa: E402


def serve(fake, devs):
    for line in sys.stdin:
        t = line.split()
        if not t:
            continue
        try:
            if t[0] == "set":
                devs[t[-1] if t[-1].startswith("/") else next(iter(devs))].set(**{k: int(v) for k, v in (x.split("=") for x in t[1:] if "=" in x)})
            elif t[0] == "silent":
                next(iter(devs.values())).silently_change(**{k: int(v) for k, v in (x.split("=") for x in t[1:])})
            elif t[0] == "raw":
                next(iter(devs.values())).raw([tuple(int(n) for n in x.split(":")) for x in t[1:]])
            elif t[0] == "destroy":
                devs[t[1] if len(t) > 1 else next(iter(devs))].destroy()
            elif t[0] == "eagain":
                next(iter(devs.values())).make_eagain(int(t[1]))
        except Exception as e:                       # a bad command must not kill the bridge under test
            print(f"[fakeproc] command {line.strip()!r}: {e!r}", flush=True)


def main():
    argv = sys.argv[1:]
    prof_path, plugs = None, []
    while argv and argv[0] != "--":
        if argv[0] == "--profile":
            prof_path = argv[1]
            argv = argv[2:]
        elif argv[0] == "--plug":
            plugs.append(argv[1])
            argv = argv[2:]
        else:
            sys.exit("usage: fakeproc.py [--profile F] [--plug PATH]... -- <bridge args>")
    argv = argv[1:]
    prof = vdev.load_profile(prof_path)
    fake = vdev.FakeEvdevModule(prof)
    sys.modules["evdev"] = fake.module
    devs = {p: fake.plug(p) for p in (plugs or ["/dev/input/event7"])}
    threading.Thread(target=serve, args=(fake, devs), daemon=True).start()
    import tx12_bridge
    return tx12_bridge.main(argv)


if __name__ == "__main__":
    sys.exit(main())
