#!/usr/bin/env python3
"""Mutation check on a COPY of this directory (never touches the originals): every mutant must make test_bio.py fail.
Exit 0 = all mutants killed; 1 = a mutant survived (test gap)."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.dirname(HERE)
MUTANTS = [
    ("contour_graph.py", 'res["safe_failsafe"] = al["fc"] and not masked', 'res["safe_failsafe"] = al["fc"]', "stuck writer no longer masks failsafe"),
    ("contour_graph.py", 'ok = n not in dead and all(any(alive(m) for m in grp)', 'ok = n not in dead and any(any(alive(m) for m in grp)', "needs groups: all -> any"),
    ("contour_graph.py", 'res["control"] = res["rc_primary"] or res["rc_backup"]', 'res["control"] = res["rc_primary"] and res["rc_backup"]', "control: OR -> AND"),
    ("contour_graph.py", "if a[0] == b[0]:", "if False:", "pairs on the same node allowed"),
    ("failsafe_des.py", "if self.armed and not self.receiver_present and", "if self.armed and", "radio failsafe ignores the receiver"),
    ("failsafe_des.py", "due = t + self.timeout\n", "due = t + self.timeout + 0.5\n", "unit deadline shifted"),
    ("failsafe_des.py", "if ms - t_rel >= hold:", "if ms - t_rel > hold:", "release hold off by one tick"),
    ("failsafe_des.py", "if sysid == self.sysid:                 # only", "if True:                 # only", "any sysid counts as GCS heartbeat"),
    ("failsafe_des.py", "self.none_u.kick(t)", "pass", "override frames do not restart the chain"),
    ("params.json", '"value": 1.0, "tag": "SRC", "source": "docs/MAVLINK-ROUTER.md / apm_model.py default"', '"value": 0.5, "tag": "SRC", "source": "docs/MAVLINK-ROUTER.md / apm_model.py default"', "RC_FS_TIMEOUT changed"),
]


def main():
    survived = 0
    for fname, old, new, why in MUTANTS:
        tmp = tempfile.mkdtemp(prefix="bio-mut-")
        try:
            dst = os.path.join(tmp, "sim")
            os.makedirs(dst)
            shutil.copy(os.path.join(SIM, "apm_model.py"), dst)
            shutil.copytree(HERE, os.path.join(dst, "bio"), ignore=shutil.ignore_patterns("__pycache__"))
            f = os.path.join(dst, "bio", fname)
            with open(f) as fh:
                src = fh.read()
            if src.count(old) != 1:
                print("BAD-MUTANT %s: pattern found %d times (%s)" % (fname, src.count(old), why))
                survived += 1
                continue
            with open(f, "w") as fh:
                fh.write(src.replace(old, new))
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            env.pop("UPDATE_GOLDEN", None)
            r = subprocess.run([sys.executable, os.path.join(dst, "bio", "test_bio.py")], capture_output=True, text=True, env=env)
            killed = r.returncode != 0
            print("%-8s %-17s %s" % ("killed" if killed else "SURVIVED", fname, why))
            survived += 0 if killed else 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("mutants: %d, survived: %d" % (len(MUTANTS), survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
