import tempfile
import unittest
from pathlib import Path

from memory_plane.core import Store, Conflict, MemoryError, BudgetExceeded


def memory(title, content, priority="P2", importance=.5, confidence=.8, entities=None):
    return {"title": title, "content": content, "priority": priority, "kind": "semantic",
            "importance": importance, "confidence": confidence, "entities": entities or [],
            "source_ref": "task://verified-1"}


class MemoryPlaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, clock=lambda: 1_700_000_000)
    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def accept(self, item):
        return self.store.accept("alice", item["id"], item["current_revision"])

    def test_candidate_is_not_retrievable_until_accepted(self):
        item = self.store.create("alice", memory("Hermes restart", "Use supervisor for Hermes", "P1"), "ops")
        result = self.store.retrieve("alice", "Hermes restart", "ops", task_context="check")
        self.assertEqual(result["manifest"]["selected"], [])
        self.accept(item)
        result = self.store.retrieve("alice", "Hermes restart", "ops", task_context="check")
        self.assertEqual(len(result["manifest"]["selected"]), 1)

    def test_feedback_is_attributed_and_duplicate_is_idempotent(self):
        item = self.accept(self.store.create("alice", memory("Deploy", "Canary then health check", "P1"), "ops"))
        self.assertEqual(self.store.feedback("alice", item["id"], 1, "task-a", "success", "artifact://a")["recorded"], True)
        self.assertEqual(self.store.feedback("alice", item["id"], 1, "task-a", "success", "artifact://a")["recorded"], False)
        with self.assertRaises(Conflict):
            self.store.feedback("alice", item["id"], 1, "task-a", "failure", "artifact://b")

    def test_scope_and_project_override_happen_before_ranking(self):
        global_item = self.store.create("alice", memory("Port", "Hermes port is 9001", "P1"), None, "hermes.port")
        project_item = self.store.create("alice", memory("Port", "Hermes port is 9010", "P2"), "ops", "hermes.port")
        self.accept(global_item); self.accept(project_item)
        result = self.store.retrieve("alice", "Hermes port", "ops", task_context="find port")
        ids = [x["id"] for x in result["manifest"]["selected"]]
        self.assertIn(project_item["id"], ids)
        self.assertNotIn(global_item["id"], ids)

    def test_stale_and_secret_data_are_blocked(self):
        with self.assertRaises(MemoryError):
            self.store.create("alice", memory("key", "api_key=super-secret"), "ops")
        item = self.store.create("alice", dict(memory("Old", "Old fact", "P1"), valid_until=1_600_000_000), "ops")
        self.accept(item)
        result = self.store.retrieve("alice", "Old fact", "ops", task_context="x")
        self.assertEqual(result["manifest"]["selected"], [])
        self.assertTrue(any(x["reason"] == "expired" for x in result["manifest"]["excluded"]))

    def test_p0_cannot_be_silently_dropped_by_budget(self):
        item = self.store.create("alice", memory("Rule", "必须先备份" * 100, "P0"), "ops")
        self.accept(item)
        with self.assertRaises(BudgetExceeded):
            self.store.retrieve("alice", "Rule", "ops", budget=128, task_context="x")

    def test_revision_cas_and_forget(self):
        item = self.store.create("alice", memory("Rule", "one", "P1"), "ops")
        with self.assertRaises(Conflict):
            self.store.accept("alice", item["id"], 2)
        self.accept(item)
        revised = self.store.revise("alice", item["id"], 1, memory("Rule", "two", "P1"))
        self.assertEqual(revised["status"], "candidate")
        self.store.accept("alice", item["id"], 2)
        self.store.forget("alice", item["id"], 2)
        self.assertEqual(self.store.list("alice", "ops")[0]["status"], "forgotten")

    def test_checkpoint_is_structured_and_versioned(self):
        state = {"goal":"fix","constraints":[],"decisions":[],"completed":[],"pending":["check"],"evidence_refs":[],"next_action":"check"}
        self.assertEqual(self.store.checkpoint("alice", "s1", state)["revision"], 1)
        with self.assertRaises(Conflict):
            self.store.checkpoint("alice", "s1", state, 0)
        self.assertEqual(self.store.read_checkpoint("alice", "s1")["payload"]["goal"], "fix")


if __name__ == "__main__":
    unittest.main()
