#!/usr/bin/env python3
"""Virtual EdgeTX-like USB joystick for the REAL EvdevSource of bench/tx12_bridge.py (docs/SIM-EVDEV.md).

Two kernel-level back ends, both written against the raw kernel ABI with the standard library only (no python-evdev: the
producer must not share code with the consumer under test, otherwise a library bug would cancel out):

  UinputDevice  /dev/uinput. Creates an input device straight from a profile (name, ids, ABS min/max/fuzz/flat, buttons).
                struct input_event written with write(); one write() = one SYN_REPORT packet.
  UhidDevice    /dev/uhid. Creates a *HID* device from a report descriptor (EdgeTX Classic descriptor, bytes copied from
                EdgeTX/edgetx radio/src/usb_joystick.cpp) and feeds 19-byte HID input reports; the kernel's hid-generic/hid-input
                derive the evdev axes, ranges, fuzz/flat and button codes exactly as for a real USB radio.
  FakeEvdevModule  (offline, no kernel): an in-process stand-in for the `evdev` module so EvdevSource logic can be unit tested
                where /dev/uinput does not exist. It is SYNTH of the python-evdev API; the kernel back ends validate it.

Every number is tagged in the profile (profile_tx12_classic.json, key "_provenance"): SRC (read in EdgeTX sources), INF (derived
from kernel hid-input rules), UNVERIFIED (no source; parameter, not fact). Nothing here has ever seen a real TX12 (HW).

CLI (manual use):  vdev.py show | serve [--backend uinput|uhid] [--profile F]   (serve: prints the node, reads commands on stdin:
  "set ABS_X 1500", "sync", "storm N", "destroy", "quit"). Needs root or access to /dev/uinput //dev/uhid.
Safety: this only creates a fake joystick on the machine it runs on; it is never connected to an aircraft.
"""
import errno
import fcntl
import glob
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, "profile_tx12_classic.json")

# ------------------------------------------------------------------ constants (linux/input-event-codes.h, linux/uinput.h, linux/uhid.h)
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT, SYN_DROPPED = 0, 3
BUS_USB = 3
ABS = {  # name -> code, cross-checked against /usr/include/linux/input-event-codes.h by test_evdev.py when the header exists
    "ABS_X": 0x00, "ABS_Y": 0x01, "ABS_Z": 0x02, "ABS_RX": 0x03, "ABS_RY": 0x04, "ABS_RZ": 0x05, "ABS_THROTTLE": 0x06,
    "ABS_RUDDER": 0x07, "ABS_WHEEL": 0x08, "ABS_GAS": 0x09, "ABS_BRAKE": 0x0A, "ABS_HAT0X": 0x10, "ABS_HAT0Y": 0x11,
    "ABS_HAT1X": 0x12, "ABS_HAT1Y": 0x13, "ABS_HAT2X": 0x14, "ABS_HAT2Y": 0x15, "ABS_HAT3X": 0x16, "ABS_HAT3Y": 0x17,
    "ABS_PRESSURE": 0x18, "ABS_DISTANCE": 0x19, "ABS_TILT_X": 0x1A, "ABS_TILT_Y": 0x1B, "ABS_TOOL_WIDTH": 0x1C,
    "ABS_VOLUME": 0x20, "ABS_MISC": 0x28,
}
ABS_NAME = {v: k for k, v in ABS.items()}
BTN_GAMEPAD, BTN_TRIGGER_HAPPY = 0x130, 0x2C0
EVENT_FMT = "llHHi"                        # struct input_event on LP64 (x86_64, aarch64): timeval(2 x long) type code value
EVENT_SIZE = struct.calcsize(EVENT_FMT)    # 24

_IOC_NRSHIFT, _IOC_TYPESHIFT, _IOC_SIZESHIFT, _IOC_DIRSHIFT = 0, 8, 16, 30
_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(d, t, nr, size):
    return (d << _IOC_DIRSHIFT) | (ord(t) << _IOC_TYPESHIFT) | (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)


UINPUT_SETUP_FMT = "=HHHH80sI"           # struct uinput_setup: input_id{bustype,vendor,product,version}, name[80], ff_effects_max
UINPUT_ABS_FMT = "=H2x6i"                # struct uinput_abs_setup: code, (pad), input_absinfo{value,min,max,fuzz,flat,resolution}
UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)
UI_DEV_SETUP = _ioc(_IOC_WRITE, "U", 3, struct.calcsize(UINPUT_SETUP_FMT))
UI_ABS_SETUP = _ioc(_IOC_WRITE, "U", 4, struct.calcsize(UINPUT_ABS_FMT))
UI_SET_EVBIT = _ioc(_IOC_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, "U", 101, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, "U", 103, 4)
UI_GET_SYSNAME_64 = _ioc(_IOC_READ, "U", 44, 64)

UHID_DESTROY, UHID_CREATE2, UHID_INPUT2 = 1, 11, 12
UHID_CREATE2_FMT = "<I128s64s64sHHIIII"  # type + uhid_create2_req up to rd_data
UHID_EVENT_SIZE = 4380      # sizeof(struct uhid_event) from linux/uhid.h (checked with gcc); the kernel copies min(count, sizeof) and zero-fills

# EdgeTX Classic HID report descriptor, copied byte for byte from radio/src/usb_joystick.cpp (SRC raw.githubusercontent.com
# EdgeTX/edgetx main, HID_JOYSTICK_ClassicReportDesc, read 2026-10-03): Game Pad, 24 buttons, 8 x 16-bit axes X Y Z Rx Ry Rz Slider Dial,
# logical 0..2047, no report id, 19-byte report (usbClassicStateUpdate: 3 button bytes then 8 x u16 LE masked to 11 bits).
HID_CLASSIC_DESC = bytes([
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x18, 0x15, 0x00, 0x25, 0x01, 0x95, 0x18, 0x75, 0x01, 0x81, 0x02,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x33, 0x09, 0x34, 0x09, 0x35, 0x09, 0x36, 0x09, 0x37,
    0x16, 0x00, 0x00, 0x26, 0xFF, 0x07, 0x75, 0x10, 0x95, 0x08, 0x81, 0x02,
    0xC0, 0xC0])
HID_CLASSIC_REPORT_LEN = 19
HID_AXIS_USAGES = [0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37]   # X Y Z Rx Ry Rz Slider Dial = CH1..CH8 (SRC usb_joystick.cpp)


def load_profile(path=None):
    with open(path or PROFILE, encoding="utf-8") as f:
        p = json.load(f)
    for k in ("vendor", "product", "version"):
        if isinstance(p.get(k), str):
            p[k] = int(p[k], 0)
    for ax in p["axes"].values():
        ax.setdefault("fuzz", 0)
        ax.setdefault("flat", 0)
        ax.setdefault("value", ax["min"])
    return p


def parse_hid_descriptor(d):
    """Tiny HID report descriptor parser (Input items only). Returns {'buttons': n, 'axes': [(usage, lmin, lmax, bits)], 'bits': total}.
    Enough for the EdgeTX Classic layout; raises on anything else it does not understand (no silent guessing)."""
    i, usage_page, lmin, lmax, rsize, rcount = 0, 0, 0, 0, 0, 0
    usages, umin, umax = [], None, None
    out = {"buttons": 0, "axes": [], "bits": 0}
    while i < len(d):
        b = d[i]
        size = (0, 1, 2, 4)[b & 3]
        typ, tag = (b >> 2) & 3, b >> 4
        raw = d[i + 1:i + 1 + size]
        val = int.from_bytes(raw, "little", signed=(typ == 1 and tag in (1, 2)))   # logical min/max are signed
        i += 1 + size
        if typ == 1:                                            # global
            if tag == 0: usage_page = val
            elif tag == 1: lmin = val
            elif tag == 2: lmax = val
            elif tag == 7: rsize = val
            elif tag == 9: rcount = val
        elif typ == 2:                                          # local
            if tag == 0: usages.append(val)
            elif tag == 1: umin = val
            elif tag == 2: umax = val
        elif typ == 0:                                          # main
            if tag == 8:                                        # Input
                if usage_page == 0x09:
                    out["buttons"] += rcount
                elif usage_page == 0x01:
                    us = usages or list(range(umin, umax + 1))
                    if len(us) != rcount:
                        raise ValueError("usage count != report count")
                    out["axes"] += [(u, lmin, lmax, rsize) for u in us]
                else:
                    raise ValueError("unsupported usage page %#x" % usage_page)
                out["bits"] += rsize * rcount
            usages, umin, umax = [], None, None
    return out


def hid_usage_to_abs(usage):
    """Linux hid-input.c (unknown: branch, generic desktop X..Dial): code = usage & 0xf -> ABS_X..ABS_RUDDER (INF, verified on the 6.8 guest kernel)."""
    return usage & 0xF


def hid_button_code(n):
    """Linux hid-input.c, application Game Pad: button n (0-based) <= 15 -> BTN_GAMEPAD+n, else BTN_TRIGGER_HAPPY + n - 16 (INF, verified by uhid test)."""
    return BTN_GAMEPAD + n if n <= 15 else BTN_TRIGGER_HAPPY + n - 16


def hid_classic_report(axes, buttons=0):
    """19-byte Classic report: 3 button bytes (LSB = button 1), then 8 x u16 LE, 11 bit (usbClassicStateUpdate, SRC). axes: 8 raw ints 0..2047."""
    if len(axes) != 8:
        raise ValueError("8 axes")
    b = (buttons & 0xFFFFFF).to_bytes(3, "little")
    return b + b"".join((max(0, min(2047, int(v))) & 0x7FF).to_bytes(2, "little") for v in axes)


def _pack_events(events):
    return b"".join(struct.pack(EVENT_FMT, 0, 0, t, c, v) for t, c, v in events)


def packet(changes):
    """[(EV_ABS|EV_KEY, code, value), ...] + SYN_REPORT as one write() worth of bytes."""
    return _pack_events(list(changes) + [(EV_SYN, SYN_REPORT, 0)])


def _wait_node(find, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p = find()
        if p and os.path.exists(p):
            return p
        time.sleep(0.02)
    raise OSError(errno.ENOENT, "event node did not appear")


def _event_nodes():
    return {os.path.basename(p) for p in glob.glob("/sys/class/input/event*")}


def _node_for_new_event(before):
    def find():
        new = sorted(_event_nodes() - before)
        return "/dev/input/" + new[-1] if new else None
    return _wait_node(find)


class Device:
    """Common interface. .path = /dev/input/eventN; set()/sync() model one HID report as one SYN packet."""
    backend = "?"
    path = None

    def set(self, **axes):                          # set(ABS_X=2047, ABS_Y=0): one atomic packet
        raise NotImplementedError

    def burst(self, n, codes=("ABS_X", "ABS_Y"), lo=100, hi=1900):   # n packets as fast as possible: the event storm
        raise NotImplementedError

    def destroy(self):                              # unplug
        raise NotImplementedError


class UinputDevice(Device):
    backend = "uinput"

    def __init__(self, profile, node_timeout=5.0):
        self.profile = profile
        self.fd = None
        for p in ("/dev/uinput", "/dev/input/uinput"):
            try:
                self.fd = os.open(p, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as e:
                self.err = e
        if self.fd is None:
            raise self.err
        before = _event_nodes()
        fd = self.fd
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        if profile.get("button_codes"):
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for c in profile["button_codes"]:
                fcntl.ioctl(fd, UI_SET_KEYBIT, c)
        for name, ax in profile["axes"].items():
            code = ABS[name]
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, UI_ABS_SETUP, struct.pack(UINPUT_ABS_FMT, code, ax["value"], ax["min"], ax["max"], ax["fuzz"], ax["flat"], 0))
        fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack(UINPUT_SETUP_FMT, profile.get("bustype", BUS_USB), profile["vendor"], profile["product"],
                                                  profile.get("version", 0), profile["name"].encode()[:79], 0))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        self.created = True
        try:
            buf = fcntl.ioctl(fd, UI_GET_SYSNAME_64, bytearray(64))
            sysname = bytes(buf).split(b"\0")[0].decode()
            self.path = _wait_node(lambda: self._node_of(sysname), node_timeout)
        except OSError:
            self.path = _node_for_new_event(before)
        self.state = {ABS[n]: a["value"] for n, a in profile["axes"].items()}

    @staticmethod
    def _node_of(sysname):
        g = glob.glob("/sys/devices/virtual/input/%s/event*" % sysname)
        return "/dev/input/" + os.path.basename(g[0]) if g else None

    def set(self, **axes):
        ev = [(EV_ABS, ABS[k], int(v)) for k, v in axes.items()]
        os.write(self.fd, packet(ev))
        for k, v in axes.items():
            self.state[ABS[k]] = int(v)

    def raw(self, events):
        """Write raw (type, code, value) events with no implicit SYN (for torn-packet tests)."""
        os.write(self.fd, _pack_events(events))

    def burst(self, n, codes=("ABS_X", "ABS_Y"), lo=100, hi=1900):
        span = hi - lo
        blob = bytearray()
        for i in range(n):
            for j, c in enumerate(codes):
                blob += struct.pack(EVENT_FMT, 0, 0, EV_ABS, ABS[c], lo + (i * 7 + j * 331) % span)
            blob += struct.pack(EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0)
        mv, off = memoryview(bytes(blob)), 0
        while off < len(mv):
            try:
                off += os.write(self.fd, mv[off:off + EVENT_SIZE * 3 * 64])
            except BlockingIOError:
                time.sleep(0.0005)

    def destroy(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None


class UhidDevice(Device):
    backend = "uhid"

    def __init__(self, profile=None, desc=HID_CLASSIC_DESC, node_timeout=5.0):
        p = profile or {}
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_NONBLOCK)
        before = _event_nodes()
        uniq = ("vdev-%d-%d" % (os.getpid(), int(time.time() * 1000) % 100000000)).encode()
        name = (p.get("name") or "OpenTX Joystick").encode()[:127]
        ev = struct.pack(UHID_CREATE2_FMT, UHID_CREATE2, name, b"vdev/uhid", uniq, len(desc), p.get("bustype", BUS_USB),
                         p.get("vendor", 0x1209), p.get("product", 0x4F54), p.get("version", 0x0200), 0) + desc
        os.write(self.fd, ev.ljust(UHID_EVENT_SIZE, b"\0"))
        # EdgeTX channelOutputs 0 -> 1024 (usbClassicStateUpdate); a profile may start an axis elsewhere (the radio streams its state from the first ms)
        self.axes = [p.get("axes", {}).get(ABS_NAME[i], {}).get("value", 1024) for i in range(8)]
        self.buttons = 0
        self.path = _node_for_new_event(before)
        self.send()

    def _drain(self):
        try:
            while os.read(self.fd, UHID_EVENT_SIZE):
                pass
        except OSError:
            pass

    def send(self):
        data = hid_classic_report(self.axes, self.buttons)
        os.write(self.fd, (struct.pack("<IH", UHID_INPUT2, len(data)) + data).ljust(UHID_EVENT_SIZE, b"\0"))

    def set(self, **axes):
        for k, v in axes.items():
            self.axes[ABS[k]] = int(v)         # ABS_X..ABS_RUDDER = codes 0..7 = report positions
        self.send()
        self._drain()

    def press(self, n, on=True):
        self.buttons = (self.buttons | (1 << (n - 1))) if on else (self.buttons & ~(1 << (n - 1)))
        self.send()

    def burst(self, n, codes=("ABS_X", "ABS_Y"), lo=100, hi=1900):
        span = hi - lo
        for i in range(n):
            for j, c in enumerate(codes):
                self.axes[ABS[c]] = lo + (i * 7 + j * 331) % span
            try:
                self.send()
            except BlockingIOError:
                time.sleep(0.0005)

    def destroy(self):
        if self.fd is not None:
            try:
                os.write(self.fd, struct.pack("<I", UHID_DESTROY).ljust(UHID_EVENT_SIZE, b"\0"))
            finally:
                os.close(self.fd)
                self.fd = None


def available(backend):
    """(ok, reason). uinput needs /dev/uinput writable; uhid needs /dev/uhid read/write."""
    path = {"uinput": "/dev/uinput", "uhid": "/dev/uhid"}[backend]
    if backend == "uinput" and not os.path.exists(path) and os.path.exists("/dev/input/uinput"):
        path = "/dev/input/uinput"
    if not os.path.exists(path):
        return False, "%s does not exist (kernel without the driver, or container without the device)" % path
    if not os.access(path, os.R_OK | os.W_OK):
        return False, "no read/write access to %s (uid %d)" % (path, os.getuid())
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        os.close(fd)
    except OSError as e:
        return False, "%s: %s" % (path, e)
    return True, ""


def create(backend, profile=None):
    profile = profile or load_profile()
    return UinputDevice(profile) if backend == "uinput" else UhidDevice(profile)


def stream(dev, hz, seconds, fn):
    """Best-effort fixed-rate report generator: every 1/hz s call fn(i) -> dict of axes, send one packet. Returns (sent, achieved_hz)."""
    period, t0, i = 1.0 / hz, time.perf_counter(), 0
    while True:
        now = time.perf_counter()
        if now - t0 >= seconds:
            break
        if now - t0 >= i * period:
            dev.set(**fn(i))
            i += 1
        else:
            time.sleep(max(0.0, min(period / 4, i * period - (now - t0))))
    return i, i / max(1e-9, time.perf_counter() - t0)


# ------------------------------------------------------------------ in-process fake of the python-evdev API (offline unit tests)

class FakeEvdevModule:
    """Builds a module object usable as sys.modules['evdev'] for EvdevSource: InputDevice backed by os.pipe(), the same
    OSError/BlockingIOError semantics as python-evdev 2.0.0 + kernel (read() on an empty fd -> BlockingIOError, on a removed
    device -> OSError(ENODEV)). SYNTH: only the calls EvdevSource makes (fd, capabilities(absinfo=True), read(), absinfo(), name, info, path)."""

    def __init__(self, profile):
        import types
        self.profile = profile
        self.types = types
        self.devices = {}                       # path -> FakeDev
        m = types.ModuleType("evdev")
        e = types.ModuleType("evdev.ecodes")
        for k, v in ABS.items():
            setattr(e, k, v)
        e.EV_SYN, e.EV_KEY, e.EV_ABS, e.SYN_REPORT, e.SYN_DROPPED = EV_SYN, EV_KEY, EV_ABS, SYN_REPORT, SYN_DROPPED
        m.ecodes = e
        owner = self

        class AbsInfo(tuple):
            def __new__(cls, value, min, max, fuzz, flat, resolution=0):
                t = tuple.__new__(cls, (value, min, max, fuzz, flat, resolution))
                return t
            value = property(lambda s: s[0])
            min = property(lambda s: s[1])
            max = property(lambda s: s[2])
            fuzz = property(lambda s: s[3])
            flat = property(lambda s: s[4])

        class InputEvent:
            def __init__(self, sec, usec, type, code, value):
                self.sec, self.usec, self.type, self.code, self.value = sec, usec, type, code, value

        class DeviceInfo:
            def __init__(self, bustype, vendor, product, version):
                self.bustype, self.vendor, self.product, self.version = bustype, vendor, product, version

        class InputDevice:
            def __init__(self, path, readonly=False):
                fd = owner.devices.get(path)
                if fd is None:
                    raise FileNotFoundError(errno.ENOENT, "No such file or directory", path)
                if fd.denied:
                    raise PermissionError(errno.EACCES, "Permission denied", path)
                self._d = fd
                self.path, self.fd = path, fd.rfd
                p = fd.profile
                self.name = p["name"]
                self.info = DeviceInfo(p.get("bustype", BUS_USB), p["vendor"], p["product"], p.get("version", 0))

            def capabilities(self, verbose=False, absinfo=True):
                return {e.EV_ABS: [(ABS[n], AbsInfo(self._d.state[ABS[n]], a["min"], a["max"], a["fuzz"], a["flat"]))
                                   for n, a in self._d.profile["axes"].items()]}

            def absinfo(self, code):
                a = [x for n, x in self._d.profile["axes"].items() if ABS[n] == code][0]
                return AbsInfo(self._d.state[code], a["min"], a["max"], a["fuzz"], a["flat"])

            def close(self):
                self._d.closed_by_reader = True

            def read(self):
                if self._d.removed:
                    raise OSError(errno.ENODEV, "No such device")
                if self._d.eagain > 0:                       # poll() said readable, read() finds nothing: EAGAIN (python-evdev raises BlockingIOError)
                    self._d.eagain -= 1
                    os.read(self.fd, EVENT_SIZE * 4096)
                    raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
                if self._d.fail is not None:
                    raise self._d.fail
                try:
                    blob = os.read(self.fd, EVENT_SIZE * 4096)
                except BlockingIOError:
                    raise
                if not blob:                                            # writer closed = unplugged
                    raise OSError(errno.ENODEV, "No such device")
                n = len(blob) // EVENT_SIZE
                return iter([InputEvent(*struct.unpack_from(EVENT_FMT, blob, i * EVENT_SIZE)) for i in range(n)])

        m.InputDevice, m.AbsInfo, m.InputEvent, m.DeviceInfo = InputDevice, AbsInfo, InputEvent, DeviceInfo
        m.list_devices = lambda input_device_dir="/dev/input", writable=True: sorted(owner.devices)
        self.module = m

    def plug(self, path, profile=None):
        d = _FakeDev(profile or self.profile)
        self.devices[path] = d
        return d


class _FakeDev:
    def __init__(self, profile):
        self.profile = profile
        self.rfd, self.wfd = os.pipe()
        os.set_blocking(self.rfd, False)
        os.set_blocking(self.wfd, True)
        self.state = {ABS[n]: a["value"] for n, a in profile["axes"].items()}
        self.removed = False
        self.denied = False
        self.closed_by_reader = False
        self.eagain = 0
        self.fail = None

    def set(self, **axes):
        ev = [(EV_ABS, ABS[k], int(v)) for k, v in axes.items()]
        for k, v in axes.items():
            self.state[ABS[k]] = int(v)
        os.write(self.wfd, packet(ev))

    def raw(self, events):
        for t, c, v in events:
            if t == EV_ABS:
                self.state[c] = v
        os.write(self.wfd, _pack_events(events))

    def make_eagain(self, n=1):
        self.eagain = n
        os.write(self.wfd, packet([]))

    def silently_change(self, **axes):          # state changes whose events were dropped by the kernel (SYN_DROPPED case)
        for k, v in axes.items():
            self.state[ABS[k]] = int(v)

    def destroy(self):
        self.removed = True
        os.close(self.wfd)


def _cli(argv):
    cmd = argv[0] if argv else "show"
    prof = load_profile(argv[argv.index("--profile") + 1] if "--profile" in argv else None)
    if cmd == "show":
        print(json.dumps(prof, indent=1))
        for b in ("uinput", "uhid"):
            print(b, *available(b))
        return 0
    if cmd == "serve":
        be = argv[argv.index("--backend") + 1] if "--backend" in argv else "uinput"
        dev = create(be, prof)
        print("node", dev.path, flush=True)
        for line in sys.stdin:
            t = line.split()
            if not t:
                continue
            if t[0] == "set":
                dev.set(**{t[1]: int(t[2])})
            elif t[0] == "storm":
                dev.burst(int(t[1]))
            elif t[0] == "destroy":
                dev.destroy()
            elif t[0] == "quit":
                break
        try:
            dev.destroy()
        except OSError:
            pass
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
