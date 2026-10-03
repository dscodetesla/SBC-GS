#!/usr/bin/env python3
"""Mutation check of the fuzz layer, run on a COPY of the project (never in place). Driven by mutate.sh.

  mutations.py <copy-root> [ID ...]

Each mutation is an exact, single-occurrence text replacement in ONE file of the copy that models a realistic regression
(parser accepts "$(", safety bound widened, shell/python diverge, trap removed in the bridge, ...). The fuzz tests of the
named classes must FAIL (= mutation killed); a passing run means the mutation SURVIVED (a hole in the tests).
Output: one line per mutation and a summary; exit 0 only if every mutation was killed.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

# id, description, file, old text, new text, test classes expected to catch it
MUTS = [
    ("M1", "shell parser accepts '$' inside double quotes (data would reach the shell)", "config/load.sh",
     "${dq}([^${dq}\\$${bt}\\\\\\\\]*)${dq}", "${dq}([^${dq}${bt}\\\\\\\\]*)${dq}", ["TestConfigDifferential"]),
    ("M2", "python parser accepts ';' in bare values (shell and python diverge)", "config/load.py",
     "([A-Za-z0-9._:/@%+,-]*))([ \\t]+#.*)?", "([A-Za-z0-9._:/@%+,;-]*))([ \\t]+#.*)?", ["TestConfigDifferential"]),
    ("M3", "registry: TX12_DEADMAN_MS hard bound widened 1000 -> 100000", "config/registry.tsv",
     "TX12_DEADMAN_MS\t300\tint\t50\t1000", "TX12_DEADMAN_MS\t300\tint\t50\t100000", ["TestConfigDifferential"]),
    ("M4", "python loader: SAFETY bounds are no longer enforced (i_know branch always taken)", "config/load.py",
     "    if i_know:\n        print(", "    if True:\n        print(", ["TestConfigDifferential", "TestConfigResolveDifferential"]),
    ("M5", "shell float validator accepts '5.' (diverges from python)", "config/load.sh",
     "[[ \"$v\" =~ ^-?[0-9]+(\\.[0-9]+)?$ ]] && [ \"${#v}\" -le 20 ]", "[[ \"$v\" =~ ^-?[0-9]+(\\.[0-9]*)?$ ]] && [ \"${#v}\" -le 20 ]", ["TestConfigDifferential"]),
    ("M6", "shell loader evaluates values (eval instead of printf -v)", "config/load.sh",
     "printf -v \"$k\" '%s' \"${SBC_V[$k]}\"", "eval \"$k=${SBC_V[$k]}\"", ["TestConfigInjection"]),
    ("M7", "gs-mavlink: port 0 accepted", "gs/mavlink/gs-mavlink.sh",
     "[ \"$2\" -ge 1 ] && [ \"$2\" -le 65535 ]", "[ \"$2\" -ge 0 ] && [ \"$2\" -le 65535 ]", ["TestGsMavlink"]),
    ("M8", "gs-mavlink: HB_SYSID upper bound 254 -> 255 (the GCS sysid)", "gs/mavlink/gs-mavlink.sh",
     "[ \"$HB_SYSID\" -le 254 ]", "[ \"$HB_SYSID\" -le 255 ]", ["TestGsMavlink"]),
    ("M9", "gs-mavlink: duplicate listening ports are no longer rejected", "gs/mavlink/gs-mavlink.sh",
     "die \"port $p is used twice (upstream, GCS UDP and TCP ports must differ)\"", ":", ["TestGsMavlink"]),
    ("M10", "bridge: exit burst (throttle failsafe + release) removed (trap/finally gutted)", "bench/tx12_bridge.py",
     "if target is not None and state in (\"ACTIVE\", \"RELEASE\"):", "if False:", ["TestBridgeFaults"]),
    ("M11", "bridge: SIGTERM/SIGINT handler removed (process dies without releasing)", "bench/tx12_bridge.py",
     "        signal.signal(s, lambda *_: stop.append(1))", "        pass", ["TestBridgeFaults"]),
    ("M12", "bridge: dead-man never fires (stale input stays 'fresh')", "bench/tx12_bridge.py",
     "now - ts <= a.deadman_ms / 1000.0", "True", ["TestBridgeFaults"]),
    ("M13", "bridge: absurd samples are clamped instead of dropped", "bench/tx12_bridge.py",
     "        elif SANE_MIN <= f <= SANE_MAX:", "        elif True:", ["TestBridgeInputValidation"]),
    ("M14", "bridge: clamp_us no longer clamps", "bench/tx12_bridge.py",
     "    return max(LO, min(HI, int(round(v))))", "    return int(round(v))", ["TestBridgeInputValidation"]),
    ("M15", "fetch.sh: sha256 comparison bypassed", "build/lib/fetch.sh",
     "if ! printf '%s  %s\\n' \"$want\" \"$tmp\" | sha256sum -c --status - 2>/dev/null; then", "if false; then", ["TestFetch"]),
    ("M16", "fetch.sh: partial download is not removed after a failed fetch", "build/lib/fetch.sh",
     "\tif ! \"${GS_FETCH_CMD:-_gs_curl_fetch}\" \"$url\" \"$tmp\"; then\n\t\trm -f \"$tmp\"", "\tif ! \"${GS_FETCH_CMD:-_gs_curl_fetch}\" \"$url\" \"$tmp\"; then\n\t\t:", ["TestFetch"]),
    ("M17", "board_conf.py: whitespace inside quoted values is stripped", "gs/lib/board_conf.py",
     "                    val = m.group(3)\n", "                    val = m.group(3).strip()\n", ["TestBoard"]),
    ("M18", "render-udev.sh: unresolved placeholders no longer fail", "gs/boards/render-udev.sh",
     "if [[ \"$content\" =~ @[A-Z][A-Z0-9_]*@ ]]; then", "if false; then", ["TestUdevRender"]),
    ("M19", "rf_model: received power grows with path loss (sign flip)", "tests/sim/models/rf_model.py",
     "- path_loss_db(P, d_m) - P.get(\"rf.misc_loss_db\"))", "+ path_loss_db(P, d_m) - P.get(\"rf.misc_loss_db\"))", ["TestModelProperties"]),
    ("M20", "power_model: cable drop has the wrong sign (more current -> higher voltage)", "tests/sim/models/power_model.py",
     "v = P.get(\"power.psu_nominal_v\") - total * P.get(\"power.cable_resistance_ohm\")", "v = P.get(\"power.psu_nominal_v\") + total * P.get(\"power.cable_resistance_ohm\")", ["TestModelProperties"]),
    ("M21", "degrade_model: EVM ceiling removed from the SNR combination", "tests/sim/models/degrade_model.py",
     "return -10.0 * math.log10(10.0 ** (-snr_db / 10.0) + 10.0 ** (evm / 10.0))", "return snr_db", ["TestModelProperties"]),
    ("M22", "priors.Rng: the seed is ignored (draws are not reproducible)", "tests/sim/models/priors.py",
     "        self.r = random.Random(seed)", "        self.r = random.Random()", ["TestModelProperties"]),
    ("M23", "gs-applyconf.sh FIX applied (custom.conf merge neutralised): the pinned defect D14 must then FAIL loudly", "gs/gs-applyconf.sh",
     "\t\tsed -i \"s/^${ckey}=.*/${ckey}=${cvalue}/\" $(readlink -f /etc/gs.conf)", "\t\t:", ["TestApplyconfFaults"]),
]

NEED = ["config", "gs", "build", "bench", os.path.join("tests", "lib"), os.path.join("tests", "shims"), os.path.join("tests", "sim", "models"),
        os.path.join("tests", "sim", "fuzz")]


def make_copy(src, dst):
    for rel in NEED:
        s = os.path.join(src, rel)
        if os.path.exists(s):
            shutil.copytree(s, os.path.join(dst, rel), symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def run_tests(root, classes, timeout=240):
    env = dict(os.environ, FUZZ_ITERS="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("FUZZ_REPO", None)
    env.pop("FUZZ_STRICT_DEFECTS", None)
    try:
        p = subprocess.run([sys.executable, os.path.join(root, "tests", "sim", "fuzz", "test_fuzz.py")] + classes, env=env, cwd=root,
                           capture_output=True, timeout=timeout)
        return p.returncode, p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def main():
    src = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
    want = set(sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="fz-mut-")
    killed = survived = errors = 0
    try:
        base = os.path.join(tmp, "base")
        make_copy(src, base)
        rc, err = run_tests(base, [])
        if rc != 0:
            print("ERROR baseline: the unmutated copy does not pass\n" + err[-1500:])
            return 1
        print("baseline: unmutated copy passes")
        for mid, desc, rel, old, new, classes in MUTS:
            if want and mid not in want:
                continue
            c = os.path.join(tmp, mid)
            make_copy(src, c)
            p = os.path.join(c, rel)
            with open(p, encoding="utf-8") as f:
                text = f.read()
            if text.count(old) != 1:
                print(f"ERROR    {mid}: pattern occurs {text.count(old)} times in {rel} ({desc})")
                errors += 1
                continue
            with open(p, "w", encoding="utf-8") as f:
                f.write(text.replace(old, new))
            rc, err = run_tests(c, classes)
            shutil.rmtree(c, True)
            if rc == 0:
                survived += 1
                print(f"SURVIVED {mid}: {desc}")
            else:
                killed += 1
                first = next((ln for ln in err.splitlines() if ln.startswith(("FAIL:", "ERROR:"))), "")
                print(f"KILLED   {mid}: {desc}  [{first[:90]}]")
        print(f"mutation check: {killed} killed, {survived} survived, {errors} not applicable (of {killed + survived + errors})")
        return 0 if survived == 0 and errors == 0 else 1
    finally:
        shutil.rmtree(tmp, True)


if __name__ == "__main__":
    sys.exit(main())
