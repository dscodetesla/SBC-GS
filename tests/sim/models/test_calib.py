#!/usr/bin/env python3
"""Tests of the bench-day bridge calib.py (sheet / template / what-if routing of overlay keys)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB = os.path.join(HERE, "calib.py")


def calib(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MODEL_", "DEGRADE_"))}
    return subprocess.run([sys.executable, CALIB, *args], capture_output=True, text=True, env=env)


class TestCalib(unittest.TestCase):
    def test_sheet_ranks_and_names_measurement(self):
        p = calib("sheet", "--top", "5")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = [l for l in p.stdout.splitlines() if l and l[0].isdigit()]
        self.assertEqual(len(rows), 5)
        # the first place is NOT stable between model versions (docs/SIM-VALIDATION.md: Morris ranks beyond a ~6-parameter group are noise)
        self.assertTrue(rows[0].split(",")[1].count(".") == 1)
        for r in rows:  # every ranked parameter has a tool and a how-to (calibration step found)
            c = r.split(",")
            self.assertTrue(c[6] and c[7], r)
        shares = [float(r.split(",")[5]) for r in rows]
        self.assertEqual(shares, sorted(shares, reverse=True))

    def test_template_is_all_null(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "t.json")
            self.assertEqual(calib("template", f, "--top", "4").returncode, 0)
            doc = json.load(open(f))
            self.assertEqual(len(doc), 4)
            self.assertTrue(all(v is None for v in doc.values()))

    def test_whatif_rejects_empty_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "t.json")
            json.dump({"power.tx_peak_factor": None}, open(f, "w"))
            p = calib("whatif", f)
            self.assertNotEqual(p.returncode, 0)

    def test_whatif_routes_both_files_and_moves_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "m.json")
            # one key of params.json (MODEL_MEASURED) and one of params.degrade.json (DEGRADE_MEASURED)
            json.dump({"power.devices.rtl8812_tx_a": {"value": 0.4, "source": "test"},
                       "hw.ant_null_mean_db": {"value": 3.0, "source": "test"}}, open(f, "w"))
            p = calib("whatif", f, "--n", "12")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn("WARNING", p.stdout)
            res = [l.split(",") for l in p.stdout.splitlines() if l.startswith("residual,")][0]
            self.assertNotEqual(res[5], res[6])  # p95 residual before vs after must differ: overlay took effect

    def test_whatif_ignores_null_entries(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "m.json")
            json.dump({"power.tx_peak_factor": None, "rf.misc_loss_db": {"value": 2.0, "source": "t"}}, open(f, "w"))
            p = calib("whatif", f, "--n", "6")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("measured keys: 1", p.stdout)

    def test_whatif_flags_overlay_without_source(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "m.json")
            json.dump({"power.devices.rtl8812_tx_a": 0.4}, open(f, "w"))
            p = calib("whatif", f, "--n", "6")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("WARNING: no 'source'", p.stdout)


if __name__ == "__main__":
    unittest.main()
