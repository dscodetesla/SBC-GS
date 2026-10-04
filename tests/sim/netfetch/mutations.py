#!/usr/bin/env python3
"""Mutation check of the netfetch tests on a COPY of the project (never in place): every mutation of build/lib/fetch.sh must make
the named test classes fail. Usage: mutate.sh [ID ...]   (no ID: all). Exit 0 only if every mutation was killed (rc 1 from the tests;
a timeout or a pattern that does not apply counts as an error, not as a kill)."""
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.realpath(os.path.join(HERE, "..", "..", ".."))
LIB = os.path.join("build", "lib", "fetch.sh")

# (id, description, old text (must occur exactly once), new text, test classes)
MUTS = [
    ("N01", "TLS verification disabled: an --insecure flag is added to the curl command", "curl -q --fail --location", "curl -q --insecure --fail --location", ["TestTls"]),
    ("N02", "-q removed: ~/.curlrc (insecure) is honoured again", "curl -q --fail", "curl --fail", ["TestTls"]),
    ("N03", "URL passed as a bare argument again (an argument starting with '-' is parsed as an option)", '--output "$tmp" --url "$url"', '--output "$tmp" "$url"', ["TestBasics"]),
    ("N04", "sha256 comparison bypassed", '\tif [ "$got" != "$want" ]; then\n\t\trm -f -- "$tmp" "$dest"', '\tif false; then\n\t\trm -f -- "$tmp" "$dest"', ["TestBasics"]),
    ("N05", "no TERM handler", "\ttrap '_gs_fetch_on_signal TERM' TERM", "\t:", ["TestSignals"]),
    ("N06", "download in the foreground again: a signal sent to the shell alone waits for curl (D13 boundary)",
     '\t"$@" &\n\t_GS_FETCH_PID=$!\n\twait "$_GS_FETCH_PID" || rc=$?', '\t"$@" || rc=$?', ["TestSignals"]),
    ("N07", "the signal handler does not stop the download (curl orphaned, handler waits for it)", '\t\tkill -s TERM "$_GS_FETCH_PID" 2>/dev/null\n', "\t\t:\n", ["TestSignals"]),
    ("N08", "the signal handler does not remove the partial file", '\t[ -z "$_GS_FETCH_TMP" ] || rm -f -- "$_GS_FETCH_TMP"\n\t_gs_fetch_untrap\n\tkill', "\t_gs_fetch_untrap\n\tkill", ["TestSignals"]),
    ("N09", "the signal is swallowed instead of re-raised", '\tkill -s "$1" "${BASHPID:-$$}"', "\t:", ["TestSignals"]),
    # NB: --proto-redir alone is redundant with --proto in curl 8.5 (libcurl re-checks the allowed protocols on every hop): a mutant that
    # loosens only --proto-redir is equivalent and cannot be killed; N10/N11 loosen both (the realistic regression), N12 only --proto
    ("N10", "file:// allowed, also as a redirect target", "--proto '=https' --proto-redir '=https' --tlsv1.2", "--proto '=https,file' --proto-redir '=https,file' --tlsv1.2", ["TestRedirects"]),
    ("N11", "http allowed, also as a redirect target (https -> http downgrade)", "--proto '=https' --proto-redir '=https' --tlsv1.2", "--proto '=https,http' --proto-redir '=https,http' --tlsv1.2", ["TestRedirects"]),
    ("N12", "http:// URLs allowed directly", "--proto '=https' --proto-redir '=https' --tlsv1.2", "--proto '=https,http' --proto-redir '=https' --tlsv1.2", ["TestRedirects"]),
    ("N13", "--tlsv1.2 (minimum TLS version) removed", "--proto-redir '=https' --tlsv1.2 \\", "--proto-redir '=https' \\", ["TestTls"]),
    ("N14", "--fail removed (an HTTP error page is accepted as the file)", "curl -q --fail --location", "curl -q --location", ["TestUnpinned"]),
    ("N15", "a wrong existing file is not removed before the download", '\t\trm -f -- "$dest"   # whatever follows', "\t\t:   # whatever follows", ["TestIdempotence"]),
    ("N16", "an existing file with the pinned hash is downloaded again", '\t\tif [ "$got" = "$want" ]; then\n\t\t\techo "fetch: ok $dest sha256=$got (already present)"',
     '\t\tif false; then\n\t\t\techo "fetch: ok $dest sha256=$got (already present)"', ["TestIdempotence"]),
    ("N17", "a connection dropped mid-body is not retried", "\t\t\t18|52|56)", "\t\t\t99)", ["TestTransferFaults"]),
    ("N18", "no low-speed abort: a stalled server hangs the build", '\t\t\t--speed-limit 1 --speed-time "$stall" \\\n', "", ["TestStall"]),
    ("N19", "destination that is not a regular file is no longer refused", '\tif [ -e "$dest" ] && [ ! -f "$dest" ]; then', "\tif false; then", ["TestBasics"]),
    ("N20", "hash taken from `sha256sum FILE` (names with backslash/newline break it)", """got="$(sha256sum < "$tmp" | awk '{print $1}')\"""", """got="$(sha256sum "$tmp" | awk '{print $1}')\"""", ["TestBasics"]),
    ("N21", "mv without `--` (a destination starting with '-' becomes an option)",
     '\tmv -f -- "$tmp" "$dest" || { rm -f -- "$tmp"; _gs_err "cannot move the download to $dest"; return 1; }\n\techo "fetch: ok',
     '\tmv -f "$tmp" "$dest" || { rm -f -- "$tmp"; _gs_err "cannot move the download to $dest"; return 1; }\n\techo "fetch: ok', ["TestBasics"]),
    ("N22", "proxy variables bypassed (--noproxy '*')", "\t\t\t--proto '=https' --proto-redir", "\t\t\t--noproxy '*' --proto '=https' --proto-redir", ["TestTls"]),
    ("N23", "GS_FETCH_RETRY ignored", '--retry "$retries"', "--retry 3", ["TestHttpErrors"]),
    ("N24", "a download command that succeeds without producing a file is accepted", '\tif [ ! -f "$tmp" ]; then', "\tif false; then", ["TestBasics"]),
    ("N25", "the temp name uses $$ again (parallel subshells share it)", '_GS_FETCH_TMP="${2:-}.part.${BASHPID:-$$}"', '_GS_FETCH_TMP="${2:-}.part.$$"', ["TestSignals"]),
    ("N26", "the failed-download cleanup of the partial file is gone", '\tif ! _gs_download "$url" "$tmp"; then\n\t\trm -f -- "$tmp"', '\tif ! _gs_download "$url" "$tmp"; then\n\t\t:', ["TestTransferFaults"]),
    ("N27", "knob values are not validated (option injection through GS_FETCH_RETRY)", '\tif ! [[ "${GS_FETCH_RETRY:-3}" =~ ^[0-9]+$ ]] ||', "\tif false ||", ["TestStall"]),
]


def run_one(mid, desc, old, new, classes, base):
    c = os.path.join(base, mid)
    shutil.copytree(os.path.join(SRC, "tests", "sim", "netfetch"), os.path.join(c, "tests", "sim", "netfetch"), ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(c, "build", "lib"))
    shutil.copy(os.path.join(SRC, LIB), os.path.join(c, LIB))
    p = os.path.join(c, LIB)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    if text.count(old) != 1:
        return mid, "ERROR", f"pattern occurs {text.count(old)} times ({desc})"
    with open(p, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new))
    env = dict(os.environ, NETFETCH_REPO=c, PYTHONDONTWRITEBYTECODE="1", NETFETCH_LONG="0")
    try:
        r = subprocess.run([sys.executable, os.path.join(c, "tests", "sim", "netfetch", "test_netfetch.py")] + classes, env=env, cwd=c,
                           capture_output=True, timeout=240)
    except subprocess.TimeoutExpired:
        return mid, "ERROR", f"timeout ({desc})"
    finally:
        shutil.rmtree(c, True)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode == 0:
        return mid, "SURVIVED", desc
    first = next((ln for ln in err.splitlines() if ln.startswith(("FAIL:", "ERROR:"))), "")
    return mid, "KILLED", f"{desc}  [{first[:80]}]"


def main():
    want = set(sys.argv[1:])
    tmp = tempfile.mkdtemp(prefix="nf-mut-")
    try:
        # the unmutated copy must pass first, otherwise "killed" means nothing
        base = os.path.join(tmp, "base")
        shutil.copytree(os.path.join(SRC, "tests", "sim", "netfetch"), os.path.join(base, "tests", "sim", "netfetch"), ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(base, "build", "lib"))
        shutil.copy(os.path.join(SRC, LIB), os.path.join(base, LIB))
        classes = sorted({c for m in MUTS for c in m[4]})
        r = subprocess.run([sys.executable, os.path.join(base, "tests", "sim", "netfetch", "test_netfetch.py")] + classes, cwd=base, capture_output=True,
                           env=dict(os.environ, NETFETCH_REPO=base, PYTHONDONTWRITEBYTECODE="1", NETFETCH_LONG="0"), timeout=600)
        if r.returncode != 0:
            print("ERROR baseline: the unmutated copy does not pass\n" + r.stderr.decode("utf-8", "replace")[-1500:])
            return 1
        print("baseline: unmutated copy passes")
        sel = [m for m in MUTS if not want or m[0] in want]
        killed = survived = errors = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("NETFETCH_MUT_JOBS", "4"))) as ex:
            futs = [ex.submit(run_one, *m, tmp) for m in sel]
            for f in futs:
                mid, status, msg = f.result()
                print(f"{status:<8} {mid}: {msg}")
                killed += status == "KILLED"
                survived += status == "SURVIVED"
                errors += status == "ERROR"
        print(f"mutation check: {killed} killed, {survived} survived, {errors} not applicable/error (of {len(sel)})")
        return 0 if survived == 0 and errors == 0 else 1
    finally:
        shutil.rmtree(tmp, True)


if __name__ == "__main__":
    sys.exit(main())
