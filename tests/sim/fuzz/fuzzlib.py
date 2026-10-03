"""Shared helpers of the SBC-GS fuzz / property / fault-injection layer (stdlib only).

Env (all optional):
  FUZZ_SEED     base seed (default 20261003); every test derives its own stream from it, so a run is reproducible
  FUZZ_ITERS    iteration scale (default 1.0 = the --check budget; run.sh --long sets 8). Counts are `n(base)`.
  FUZZ_REPO     repository root to test (default: three levels above this file); mutate.sh points it at a COPY
  FUZZ_PY       python with pymavlink for the bridge fault tests (default /opt/sbcvenv/bin/python, else python3)
  FUZZ_STRICT_DEFECTS=1   pinned REPO defects are asserted as FIXED (fails today); default = assert they still reproduce
"""
import atexit
import hashlib
import os
import random
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True   # the tests import project modules: never leave __pycache__ in the repository
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.realpath(os.environ.get("FUZZ_REPO") or os.path.join(HERE, "..", "..", ".."))
SEED = int(os.environ.get("FUZZ_SEED", "20261003"))
SCALE = float(os.environ.get("FUZZ_ITERS", "1"))
STRICT = os.environ.get("FUZZ_STRICT_DEFECTS", "0") == "1"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def n(base, lo=1):
    """Iteration count: base scaled by FUZZ_ITERS (at least lo)."""
    return max(lo, int(round(base * SCALE)))


def rng(name):
    """Independent deterministic stream per test name (stable across Python runs: no hash())."""
    h = hashlib.sha256(f"{SEED}:{name}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def path(*p):
    return os.path.join(REPO, *p)


_TMPS = []


def tmpdir(prefix="fz-"):
    """Temp dir that is removed when the test process exits (the tests also run as `nobody`, TMPDIR is honoured)."""
    d = tempfile.mkdtemp(prefix=prefix)
    _TMPS.append(d)
    return d


def _cleanup():
    for d in _TMPS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def run(cmd, env=None, timeout=20, cwd=None, input=None):
    """subprocess.run with captured text output; a timeout is reported as rc 124 (never raises)."""
    e = dict(os.environ)
    e.update(env or {})
    try:
        p = subprocess.run(cmd, env=e, cwd=cwd, input=input, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as ex:
        return 124, (ex.stdout or b"").decode("utf-8", "replace"), (ex.stderr or b"").decode("utf-8", "replace")


def fuzz_python():
    """Interpreter that can import pymavlink (bridge tests), or None."""
    for c in (os.environ.get("FUZZ_PY"), "/opt/sbcvenv/bin/python", sys.executable):
        if not c or not os.path.exists(c):
            continue
        if subprocess.run([c, "-c", "import pymavlink"], capture_output=True).returncode == 0:
            return c
    return None


class Defects:
    """Registry of pinned REPO defects found by this layer (ids D1.. are described in docs/SIM-FUZZ.md)."""
    seen = {}


def pinned(testcase, did, defect_observed, detail):
    """A defect of the REAL code is pinned: by default the test asserts that it STILL reproduces (so a fix makes the
    test fail loudly and docs/SIM-FUZZ.md has to be updated); FUZZ_STRICT_DEFECTS=1 asserts the correct behaviour."""
    Defects.seen[did] = bool(defect_observed)
    if STRICT:
        testcase.assertFalse(defect_observed, f"{did} present: {detail}")
    else:
        testcase.assertTrue(defect_observed, f"{did} no longer reproduces (fixed?). Flip the pin and update docs/SIM-FUZZ.md. {detail}")


MEASURED = {}


def measure(key, value):
    """Record a measured observation (exit code, delay, leftover file ...); written to $FUZZ_REPORT as JSON by test_fuzz.py when set."""
    MEASURED[key] = value
