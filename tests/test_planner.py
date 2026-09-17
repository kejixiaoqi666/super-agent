import unittest

from agent_body.planner import Step, execute_plan, make_plan


class MakePlanTest(unittest.TestCase):
    def test_default_decompose_splits_sentences(self):
        p = make_plan("先查节点。再重启。最后验证")
        self.assertEqual(p.total, 3)
        self.assertEqual([s.desc for s in p.steps],
                         ["先查节点", "再重启", "最后验证"])

    def test_custom_decompose(self):
        p = make_plan("A|B|C", decompose=lambda g: g.split("|"))
        self.assertEqual(p.total, 3)
        self.assertEqual(p.goal, "A|B|C")

    def test_empty_goal_falls_back_to_single_step(self):
        p = make_plan("", decompose=lambda g: [])
        self.assertEqual(p.total, 1)
        self.assertEqual(p.steps[0].desc, "")

    def test_progress(self):
        p = make_plan("a。b。c", decompose=lambda g: g.split("。"))
        p.steps[0].status = "done"
        self.assertEqual(p.progress(), "1/3")
        self.assertEqual(p.done, 1)


class ExecutePlanTest(unittest.TestCase):
    def _plan(self):
        return make_plan("x", decompose=lambda g: ["步1", "步2", "步3"])

    def _runner(self):
        results = []

        def run(desc, i):
            results.append((i, desc))
            return f"结果{i}"
        return run, results

    def test_executes_all_in_order(self):
        p = self._plan()
        run, results = self._runner()
        out = execute_plan(p, run, stop_on_error=False)
        self.assertEqual(results, [(0, "步1"), (1, "步2"), (2, "步3")])
        self.assertEqual(out["done"], 3)
        self.assertEqual(out["failed"], 0)
        self.assertEqual(out["progress"], "3/3")
        self.assertEqual([s.status for s in p.steps],
                         ["done"] * 3)

    def test_stop_on_error(self):
        p = self._plan()

        def run(desc, i):
            if i == 1:
                raise RuntimeError("boom")
            return "ok"
        out = execute_plan(p, run, stop_on_error=True)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["done"], 1)
        # 步2失败后停止，步3未执行(pending)
        self.assertEqual(p.steps[2].status, "pending")

    def test_continue_after_error(self):
        p = self._plan()

        def run(desc, i):
            if i == 1:
                raise RuntimeError("boom")
            return "ok"
        out = execute_plan(p, run, stop_on_error=False)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["done"], 2)  # 步1、步3 done
        self.assertEqual(p.steps[1].status, "failed")
        self.assertIn("boom", p.steps[1].result)

    def test_skips_already_done_steps(self):
        p = self._plan()
        p.steps[0].status = "done"
        p.steps[0].result = "已有"
        run, results = self._runner()
        out = execute_plan(p, run)
        self.assertEqual(results, [(1, "步2"), (2, "步3")])  # 步1已done跳过
        self.assertEqual(out["done"], 3)

    def test_plan_dataclass(self):
        s = Step(0, "d", "pending", "")
        self.assertEqual(s.desc, "d")


if __name__ == "__main__":
    unittest.main()