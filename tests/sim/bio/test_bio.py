#!/usr/bin/env python3
"""Tests for the biomimetic-inspired prototypes: unit, property, cross-check and golden. stdlib unittest, < 2 s.
UPDATE_GOLDEN=1 rewrites golden/*.txt (only after an INTENDED model change)."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import contour_graph as cg  # noqa: E402
import failsafe_des as fd  # noqa: E402
from apm_model import ApmModel  # noqa: E402

P = cg.load()
T = fd.load_timers()


def golden(name, text):
    path = os.path.join(HERE, "golden", name)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        with open(path, "w") as f:
            f.write(text)
    with open(path) as f:
        return f.read()


class GraphTests(unittest.TestCase):
    def test_no_fault_all_ok(self):
        self.assertTrue(all(cg.evaluate(P, []).values()))

    def test_power_cascade(self):
        r = cg.evaluate(P, [("pwr_air", "dead")])
        self.assertFalse(any(r.values()))                   # everything on the AIR side depends on the rails
        r = cg.evaluate(P, [("pwr_gs", "dead")])
        self.assertTrue(r["rc_primary"] and not r["rc_backup"] and not r["video"] and r["safe_failsafe"])

    def test_common_cause_of_both_rc_paths(self):
        spof = {n for (n, m), r, _w, _u in cg.single_sweep(P) if m == "dead" and not r["control"]}
        self.assertEqual(spof, {"pwr_air", "fc", "tx12"})

    def test_stuck_writer_masks_failsafe(self):
        self.assertFalse(cg.evaluate(P, [("rc_bridge", "stuck")])["safe_failsafe"])
        # a stuck writer that is itself cut off from the FC does not mask
        self.assertTrue(cg.evaluate(P, [("rc_bridge", "stuck"), ("pi5", "dead")])["safe_failsafe"])

    def test_dead_is_monotone(self):
        # property: adding a dead fault never restores a function
        nodes = sorted(P["nodes"])
        for a in nodes:
            ra = cg.evaluate(P, [(a, "dead")])
            for b in nodes:
                rb = cg.evaluate(P, [(a, "dead"), (b, "dead")])
                for f in cg.FUNCS:
                    self.assertTrue(not rb[f] or ra[f], (a, b, f))

    def test_needs_acyclic_and_nodes_in_paths_exist(self):
        cg.alive_map(P, set())
        for fn in P["functions"].values():
            for path in fn["paths"]:
                for n in path:
                    self.assertIn(n, P["nodes"])

    def test_spearman_sanity(self):
        a = {"x": 1, "y": 2, "z": 3}
        self.assertAlmostEqual(cg.spearman(a, a), 1.0)
        self.assertAlmostEqual(cg.spearman(a, {"x": 3, "y": 2, "z": 1}), -1.0)

    def test_golden(self):
        self.assertEqual(cg.report_single(P), golden("lesion_single.txt", cg.report_single(P)))
        self.assertEqual(cg.report_pairs(P), golden("lesion_pairs.txt", cg.report_pairs(P)))
        self.assertEqual(cg.report_rank(P), golden("lesion_rank.txt", cg.report_rank(P)))


class DesTests(unittest.TestCase):
    def test_timer_values_match_apm_model_defaults(self):
        m = ApmModel()
        self.assertEqual((T["rc_override_time_s"], T["rc_fs_timeout_s"], T["fs_gcs_timeout_s"]),
                         (m.rc_override_time, m.rc_fs_timeout, m.fs_gcs_timeout))

    def test_unit_fires_clears_refractory(self):
        eng = fd.Engine()
        log = []
        u = fd.Unit(eng, "u", 1.0, lambda t: log.append(("fire", t)), lambda t: log.append(("clear", t)), refractory=5.0)
        eng.at(0.0, u.kick)
        eng.at(3.0, u.kick)          # clears, rearms: due 4.0 but refractory holds it to 1.0+5.0 = 6.0
        eng.run(20)
        self.assertEqual(log, [("fire", 1.0), ("clear", 3.0), ("fire", 6.0)])

    def test_bridge_facts(self):
        f = fd.bridge_facts(T)
        self.assertEqual((f["throttle_fs_frames"], f["release_frames"]), (3, 17))
        self.assertAlmostEqual(f["first_frame_after_stop_s"], 10.05)
        self.assertAlmostEqual(f["last_frame_s"], 11.3)         # dead-man 300 ms + hold 1.0 s after the last fresh tick
        self.assertGreater(f["heartbeats_after_stop"], 0)

    def test_expected_timelines(self):
        S = fd.scenarios(T)

        def ev(name):
            e, u, kw = S[name]
            return [(round(t, 2), s) for t, s in fd.run_des(T, e, u, **kw).events]
        self.assertEqual(ev("A_stop_noRX"), [(15.3, "Radio Failsafe")])       # heartbeat keeps GCS failsafe quiet (GAPS S3)
        self.assertEqual(ev("B_stop_RX"), [])
        self.assertEqual(ev("C_kill_noRX")[0], (14.0, "Radio Failsafe"))
        self.assertEqual(ev("D_kill_RX"), [(15.0, "GCS Failsafe")])

    def test_cross_check_all_agree(self):
        rows, ok = fd.cross_check(T)
        self.assertTrue(ok, [r[0] for r in rows if not r[1]])
        self.assertGreaterEqual(len(rows), 9)

    def test_cross_check_detects_disagreement(self):
        T2 = dict(T)
        e, u, kw = fd.scenarios(T)["C_kill_noRX"]
        d = fd.run_des(dict(T2, rc_fs_timeout_s=0.5), e, u, **kw)       # DES with a different timer than apm_model
        a = fd.run_apm(T, e, u, **kw)
        self.assertNotEqual([round(t, 1) for t, _ in d.events], [round(t, 1) for t, _ in a.events])

    def test_time_to_failsafe(self):
        t = fd.time_to_failsafe_table(T)
        self.assertEqual(t["bridge_killed_noRX_radio_fs"], 4.0)
        self.assertEqual(t["bridge_killed_GCS_fs"], 5.0)
        self.assertFalse(t["sticks_stop_GCS_fs_fires"])
        self.assertEqual(t["alink_fallback_after_gs_silence_s"], 1.0)

    def test_golden(self):
        self.assertEqual(fd.report(T), golden("failsafe_des.txt", fd.report(T)))

    def test_params_json_provenance(self):
        with open(os.path.join(HERE, "params.json")) as fh:
            d = json.load(fh)
        for sect in ("timers", "nodes", "functions"):
            for k, v in d[sect].items():
                self.assertIn(v["tag"] if "tag" in v else None, ("SRC", "REPO", "INF", "UNVERIFIED", "SYNTH"), (sect, k))
                self.assertTrue(v.get("source"), (sect, k))


if __name__ == "__main__":
    unittest.main(verbosity=1)
