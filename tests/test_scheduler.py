import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agent_body.scheduler import CronScheduler, next_run, parse_schedule


class _Clock:
    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        return self.times[0]

    def advance_to(self, t):
        self.times[0] = t


def _epoch(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


class ParseTest(unittest.TestCase):
    def test_every(self):
        spec = parse_schedule("@every 30s")
        self.assertEqual(spec["type"], "every")
        self.assertEqual(spec["interval"], 30)
        spec = parse_schedule("@every 2h")
        self.assertEqual(spec["interval"], 7200)

    def test_every_bad(self):
        with self.assertRaises(ValueError):
            parse_schedule("@every abc")

    def test_cron_5field(self):
        spec = parse_schedule("30 14 * * 1")
        self.assertEqual(spec["type"], "cron")
        self.assertEqual(spec["hour"], 14)
        self.assertEqual(spec["dow"], 1)

    def test_cron_bad_len(self):
        with self.assertRaises(ValueError):
            parse_schedule("* * *")

    def test_oneshot(self):
        spec = parse_schedule("@2026-09-18T09:00:00")
        self.assertEqual(spec["type"], "one-shot")
        self.assertAlmostEqual(spec["at"], _epoch(2026, 9, 18, 9), delta=2)


class NextRunTest(unittest.TestCase):
    def test_every_advances_by_interval(self):
        spec = parse_schedule("@every 60s")
        self.assertAlmostEqual(next_run(spec, 1000.0), 1060.0, delta=1e-6)

    def test_cron_advances_to_next_match(self):
        # 14:15 起，找下一个 "30 14 * * *"
        spec = parse_schedule("30 14 * * *")
        nxt = next_run(spec, _epoch(2026, 9, 18, 14, 15))
        self.assertAlmostEqual(nxt, _epoch(2026, 9, 18, 14, 30), delta=1)

    def test_cron_rolls_to_next_day(self):
        # 14:45 起，今天的 14:30 已过 → 明天 14:30
        spec = parse_schedule("30 14 * * *")
        nxt = next_run(spec, _epoch(2026, 9, 18, 14, 45))
        self.assertAlmostEqual(nxt, _epoch(2026, 9, 19, 14, 30), delta=1)

    def test_oneshot_fixed(self):
        spec = parse_schedule("@2026-09-18T09:00:00")
        self.assertAlmostEqual(next_run(spec, 0), spec["at"], delta=1e-6)


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = _Clock([_epoch(2026, 9, 18, 10, 0)])
        self.s = CronScheduler(Path(self.tmp), clock=self.clock)
        self.ran = []

    def runner(self, job):
        self.ran.append(job["id"])

    def test_add_and_due(self):
        self.s.add("j", "@every 1h")
        self.assertFalse(self.s.due(_epoch(2026, 9, 18, 10, 0)))
        self.assertTrue(self.s.due(_epoch(2026, 9, 18, 11, 0)))
        self.assertTrue(self.s.due(_epoch(2026, 9, 18, 11, 30)))

    def test_run_due_fires_and_advances(self):
        self.s.add("j", "@every 1h", runner=self.runner)
        self.assertEqual(self.s.run_due(_epoch(2026, 9, 18, 11, 0),
                                        runner=self.runner), ["j"])
        self.assertEqual(self.ran, ["j"])
        # next_run 已推进到 12:00，11:00 不再触发
        self.assertEqual(self.s.run_due(_epoch(2026, 9, 18, 11, 0),
                                        runner=self.runner), [])

    def test_oneshot_runs_once_then_removed(self):
        spec = "@2026-09-18T10:05:00"
        self.s.add("once", spec, runner=self.runner)
        self.assertIn("once", self.s.jobs)
        self.assertEqual(self.s.run_due(_epoch(2026, 9, 18, 10, 6),
                                        runner=self.runner), ["once"])
        self.assertNotIn("once", self.s.jobs)  # 一次性跑完移除
        self.assertEqual(self.ran, ["once"])

    def test_remove(self):
        self.s.add("j", "@every 1h")
        self.assertTrue(self.s.remove("j"))
        self.assertFalse(self.s.remove("j"))
        self.assertNotIn("j", self.s.jobs)

    def test_persistence_reload(self):
        self.s.add("j", "@every 30m")
        self.s.add("o", "@2026-09-18T10:05:00")
        s2 = CronScheduler(Path(self.tmp), clock=self.clock)
        self.assertIn("j", s2.jobs)
        self.assertIn("o", s2.jobs)
        self.assertEqual(s2.jobs["j"]["spec"], "@every 30m")

    def test_missing_dir_created(self):
        import os
        s = CronScheduler(os.path.join(self.tmp, "sub", "dir"))
        s.add("j", "@every 1h")
        self.assertTrue(Path(self.tmp, "sub", "dir", "scheduler.json").exists())


if __name__ == "__main__":
    unittest.main()
