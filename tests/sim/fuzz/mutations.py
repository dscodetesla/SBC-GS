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
     "\tif [ \"$got\" != \"$want\" ]; then\n\t\trm -f -- \"$tmp\" \"$dest\"", "\tif false; then\n\t\trm -f -- \"$tmp\" \"$dest\"", ["TestFetch"]),
    ("M16", "fetch.sh: partial download is not removed after a failed fetch", "build/lib/fetch.sh",
     "\tif ! _gs_download \"$url\" \"$tmp\"; then\n\t\trm -f -- \"$tmp\"", "\tif ! _gs_download \"$url\" \"$tmp\"; then\n\t\t:", ["TestFetch"]),
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
    ("M23", "gs-applyconf.sh: the old sed merge is restored (D14 regression)", "gs/gs-applyconf.sh",
     "\t\tgs_conf_merge_line \"$ckey\" \"$cvalue\" || true", "\t\tsed -i \"s/^${ckey}=.*/${ckey}=${cvalue}/\" $(readlink -f /etc/gs.conf)", ["TestApplyconfFaults"]),
    ("M24", "fan.sh: the unreadable-temperature guard is removed (D17 regression)", "gs/fan.sh",
     "if ! [[ \"$temp_cpu\" =~ ^[0-9]{4,}$ ]]; then", "if false; then", ["TestFanFaults"]),
    ("M25", "gsconf.sh (custom.conf merge): values are written unquoted again (D14 regression)", "gs/lib/gsconf.sh",
     "\t\tval=\"'${val}'\"", "\t\t:", ["TestApplyconfFaults"]),
    ("M26", "bridge: load_map no longer checks that an axis entry is an object (D18 regression)", "bench/tx12_bridge.py",
     "        if not isinstance(cfg, dict):\n", "        if False:\n", ["TestBridgeInputValidation"]),
    ("M27", "bridge: load_map no longer checks that the document is an object (D18 regression)", "bench/tx12_bridge.py",
     "    if not isinstance(d, dict):\n", "    if False:\n", ["TestBridgeInputValidation"]),
    ("M28", "bridge: a short stdin line with a value below LO is clamped up again (D19 regression)", "bench/tx12_bridge.py",
     "    if len(vals) < NCH and any(", "    if False and any(", ["TestBridgeInputValidation"]),
    ("M29", "bridge: an unterminated last stdin line is trusted again (D19 regression)", "bench/tx12_bridge.py",
     "            truncated = not raw.endswith(\"\\n\")", "            truncated = False", ["TestBridgeInputValidation"]),
    ("M30", "bridge: map_axis divides by zero again when center == max (D20 regression)", "bench/tx12_bridge.py",
     "n = (raw - c) / span if span > 0 else 0.0", "n = (raw - c) / span", ["TestBridgeInputValidation"]),
    ("M31", "power_model: NaN/inf/non-positive psu_a accepted again (D21 regression)", "tests/sim/models/power_model.py",
     "    if psu_a is not None and (isinstance(psu_a, bool)", "    if False and (isinstance(psu_a, bool)", ["TestModelProperties"]),
    ("M32", "power_model: negative adapter count accepted again (D21 regression)", "tests/sim/models/power_model.py",
     "    if isinstance(adapters, bool) or not isinstance(adapters, int) or adapters < 0:", "    if False:", ["TestModelProperties"]),
    ("M33", "params.json: rtl8812 idle range overlaps rx again (D22 regression)", "tests/sim/models/params.json",
     "\"max\": 0.3,\n      \"note\": \"placeholder, range is INF. adapter associated", "\"max\": 0.5,\n      \"note\": \"placeholder, range is INF. adapter associated", ["TestModelProperties"]),
    # M40..M55: regressions of D1..D13, D16 (fixed 2026-10-03); each restores the old behaviour on the copy
    ("M40", "config/load.sh: NUL bytes are no longer rejected (D1 regression)", "config/load.sh",
     "\ttr -d '\\000' <\"$file\" | cmp -s - \"$file\" || sbc_die \"$file: NUL byte in file (control character)\"", "\t:", ["TestConfigPinnedDefects"]),
    ("M41", "config/load.sh: the parser follows the caller's locale again (D2 regression)", "config/load.sh",
     "\tlocal LC_ALL=C   # byte semantics", "\t: # byte semantics", ["TestConfigPinnedDefects"]),
    ("M42", "config/load.sh: enum check matches the joined 'a|b' again (D3 regression)", "config/load.sh",
     "for alt in \"${alts[@]}\"; do [ \"$v\" = \"$alt\" ] && return 0; done", "case \"|${t#enum:}|\" in *\"|$v|\"*) return 0 ;; esac", ["TestConfigPinnedDefects"]),
    ("M43", "gs-mavlink: IPv4 regex is no longer anchored at the end (D4 regression)", "gs/mavlink/gs-mavlink.sh",
     "\\.([0-9]{1,3})\\.([0-9]{1,3})\\.([0-9]{1,3})$ ]] || die \"$name is not", "\\.([0-9]{1,3})\\.([0-9]{1,3})\\.([0-9]{1,3}) ]] || die \"$name is not", ["TestGsMavlink"]),
    ("M44", "gs-mavlink: GCS ports are compared as strings again (D5 regression)", "gs/mavlink/gs-mavlink.sh",
     "\tpn=$((10#$p))", "\tpn=$p", ["TestGsMavlink"]),
    ("M45", "gs-mavlink: '..' in SERIAL_DEV is accepted again (D6 regression)", "gs/mavlink/gs-mavlink.sh",
     "\tcase \"$SERIAL_DEV\" in */../*|*/..) die", "\tcase \"$SERIAL_DEV\" in */../x/*|*/..x) die", ["TestGsMavlink"]),
    ("M46", "gs-mavlink: '..' in DUMP_PATH is accepted again (D6 regression)", "gs/mavlink/gs-mavlink.sh",
     "case \"$DUMP_PATH\" in */../*|*/..) die", "case \"$DUMP_PATH\" in */../x/*|*/..x) die", ["TestGsMavlink"]),
    ("M47", "validate.sh: a glued '#' is accepted again (D7 regression)", "gs/boards/validate.sh",
     "*)([[:space:]]+#.*)?[[:space:]]*$'", "*)([[:space:]]*#.*)?[[:space:]]*$'", ["TestBoard"]),
    ("M48", "validate.sh: a backslash inside double quotes is accepted again (D8 regression)", "gs/boards/validate.sh",
     "|\"[^\"$`\\\\]*\"|", "|\"[^\"$`]*\"|", ["TestBoard"]),
    ("M49", "board.sh: BOARD is no longer validated as an identifier (D9 regression)", "gs/lib/board.sh",
     "[[ \"$BOARD\" =~ ^[a-z0-9][a-z0-9-]*$ ]]", "[[ \"$BOARD\" =~ ^.*$ ]]", ["TestBoard"]),
    ("M50", "board.sh: board_get key is no longer validated (D10 regression)", "gs/lib/board.sh",
     "[[ \"$key\" =~ ^[A-Z][A-Z0-9_]*$ ]]", "[[ \"$key\" =~ ^.*$ ]]", ["TestBoard"]),
    ("M51", "render-udev.sh: values are no longer checked for udev syntax characters (D11 regression)", "gs/boards/render-udev.sh",
     "[[ \"$v\" =~ ^[A-Za-z0-9_.:-]+$ ]] || {", "true || {", ["TestUdevRender"]),
    ("M52", "fetch.sh: sha256 format check is line-based (grep) again (D12 regression)", "build/lib/fetch.sh",
     "elif ! [[ \"$want\" =~ ^[0-9a-f]{64}$ ]]; then", "elif ! printf '%s' \"$want\" | grep -Eq '^[0-9a-f]{64}$'; then", ["TestFetch"]),
    ("M53", "fetch.sh: git ref format check is line-based (grep) again (D12 regression)", "build/lib/fetch.sh",
     "if ! [[ \"$ref\" =~ ^[0-9a-f]{40}$ ]]; then", "if ! printf '%s' \"$ref\" | grep -Eq '^[0-9a-f]{40}$'; then", ["TestFetch"]),
    ("M54", "fetch.sh: no TERM handler, the partial download stays after SIGTERM (D13 regression)", "build/lib/fetch.sh",
     "\ttrap '_gs_fetch_on_signal TERM' TERM", "\t:", ["TestFetch"]),
    ("M55", "gs-applyconf.sh: the empty/truncated gs.conf check is skipped (D16 regression)", "gs/gs-applyconf.sh",
     "if ! gsconf_check /etc/gs.conf; then", "if false && gsconf_check /etc/gs.conf; then", ["TestApplyconfFaults"]),
    # gs/lib/gsconf.sh: atomic gs.conf writes (D16 second half); each mutation restores one lost guarantee
    ("MG1", "gsconf.sh: the new content is written into gs.conf itself, not a temp file + rename (non-atomic write restored)", "gs/lib/gsconf.sh",
     "\"$conf\" >\"$_gsconf_tmp\" ||", "\"$conf\" >\"$conf\" ||", ["TestGsconfAtomic"]),
    ("MG2", "gsconf.sh: an empty/truncated gs.conf is no longer refused before the update", "gs/lib/gsconf.sh",
     "_gsconf_check \"$conf\" 0 || {", "true || {", ["TestGsconfAtomic"]),
    ("MG3", "gsconf.sh: no rollback after a bad post-rename check", "gs/lib/gsconf.sh",
     "if mv -f -- \"$_gsconf_bak\" \"$conf\"; then", "if false; then", ["TestGsconfAtomic"]),
    ("MG4", "gsconf.sh: an unsafe right-hand side is written as is (it is even sourced by the verification)", "gs/lib/gsconf.sh",
     "[[ \"$v\" =~ ^[A-Za-z0-9_.,:/@%+=-]*$ ]] || {", "true || {", ["TestGsconfAtomic"]),
    ("MG5", "gsconf.sh: the new file is not given the mode of the old one", "gs/lib/gsconf.sh",
     "\tchmod \"$mode\" -- \"$_gsconf_tmp\" 2>/dev/null\n", "\t:\n", ["TestGsconfAtomic"]),
    ("MG6", "gsconf.sh: no lock between concurrent writers (lost update)", "gs/lib/gsconf.sh",
     "\t\tflock -w \"${GSCONF_LOCK_WAIT:-20}\" -E 75 \"$lockfd\"\n", "\t\ttrue\n", ["TestGsconfAtomic"]),
    ("MG7", "gsconf.sh: leftovers of a dead writer are not swept", "gs/lib/gsconf.sh",
     "\t\tkill -0 \"$p\" 2>/dev/null || rm -f -- \"$f\"\n", "\t\t:\n", ["TestGsconfAtomic"]),
    ("MG8", "gsconf.sh: the verification of the new content (required keys, read-back) is skipped", "gs/lib/gsconf.sh",
     "_gsconf_check \"$_gsconf_tmp\" 1 || {", "true || {", ["TestGsconfAtomic"]),
    ("MG9", "gsmenu.sh: rec_fps is written by sed again (hostile value breaks gs.conf)", "gs/gsmenu.sh",
     "gsconf_set_quoted /etc/gs.conf rec_fps \"$5\" || exit 1", "sed -i \"s/^rec_fps=.*/rec_fps='$5'/\" \"$(readlink -f /etc/gs.conf)\"", ["TestGsconfAtomic"]),
    ("MG10", "gs-applyconf.sh: custom.conf is consumed although gs.conf cannot be written", "gs/gs-applyconf.sh",
     "\tgsconf_can_write /etc/gs.conf || {", "\ttrue || {", ["TestGsconfAtomic"]),
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
