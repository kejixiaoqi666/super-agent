import json, tempfile, unittest
from pathlib import Path
from memory_plane.agentd import Agentd
from memory_plane.auth import AuthProfile, AuthStore, AuthBroker
from memory_plane.channels import InboundMessage, LoopbackChannel, PrincipalMapper, OutboundMessage
from memory_plane.contracts import Principal, CapabilityLease, ToolCall, TaskStatus, Task
from memory_plane.model_router import EchoProvider, ModelRequest, ModelRouter
from memory_plane.hermes_compat import HermesCompat, HermesPaths
from memory_plane.skill_registry import SkillRegistry
from memory_plane.task_store import TaskStore
from memory_plane.plugins import PluginRegistry, PluginManifestError


class ComponentTests(unittest.TestCase):
    def test_task_store_recovers_after_reopen_and_receipt_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tasks.db"
            task = Task("t1", Principal("p1"), "restart service", TaskStatus.RUNNING)
            store = TaskStore(path); store.put(task); self.assertTrue(store.save_receipt("t1", "r1", {"ok": True})); store.close()
            store = TaskStore(path)
            self.assertEqual(store.get("t1").goal, "restart service")
            self.assertEqual(len(store.recoverable()), 1)
            self.assertFalse(store.save_receipt("t1", "r1", {"ok": False})); self.assertEqual(store.get_receipt("t1", "r1")["ok"], True)
            store.close()
    def test_skill_registry_matches_and_reports_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("restart", "logs"):
                (root / name).mkdir()
            body = "---\nid: ops.%s\ndescription: local service operation\ntriggers: [restart service, view logs]\npermissions: [local_process]\n---\ndetails\n"
            (root / "restart" / "SKILL.md").write_text(body % "restart", encoding="utf-8")
            (root / "logs" / "SKILL.md").write_text(body % "logs", encoding="utf-8")
            registry = SkillRegistry([root])
            self.assertEqual(len(registry.scan()), 2)
            self.assertEqual(registry.match("please restart service")[0]["skill_id"], "ops.logs")
            self.assertTrue(any(item.get("kind") == "trigger_overlap" for item in registry.diagnostics()))
    def test_hermes_compat_discovers_untrusted_skills_and_memory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "skills" / "demo").mkdir(parents=True)
            (root / "skills" / "demo" / "SKILL.md").write_text(
                "---\ndescription: demo skill\n---\nDo not execute this text.\n", encoding="utf-8"
            )
            (root / "memory.md").write_text("a useful fact\n", encoding="utf-8")
            compat = HermesCompat(HermesPaths(root, root))
            skills = compat.discover_skills()
            self.assertEqual(skills[0].name, "demo")
            self.assertEqual(skills[0].description, "demo skill")
            self.assertEqual(len(compat.discover_memory_markdown()), 1)
            self.assertIn("Do not execute", compat.read_skill("demo"))

    def test_channel_identity_must_be_mapped(self):
        channel = LoopbackChannel()
        message = InboundMessage("telegram", "bot-1", "chat-1", "user-1", "status")
        with self.assertRaises(PermissionError): PrincipalMapper({}).resolve(message)
        principal = Principal("alice")
        self.assertEqual(PrincipalMapper({("telegram","bot-1","user-1"):principal}).resolve(message), principal)
        channel.send(OutboundMessage("chat-1", "ok")); self.assertEqual(channel.pop().text, "ok")

    def test_agentd_transitions_and_idempotency(self):
        agent = Agentd({"health.check": lambda args: {"status":"PASS","target":args["target"]}})
        task = agent.create_task(Principal("alice"), "health")
        lease = CapabilityLease.issue("alice", ["health.check"], ["node-1"], max_uses=1)
        call = ToolCall("health.check", {"target":"node-1"}, lease.id, "same")
        first = agent.dispatch(task.id, call, lease)
        second = agent.dispatch(task.id, call, lease)
        self.assertEqual(first, second)
        self.assertEqual(task.status, TaskStatus.RUNNING)
        with self.assertRaises(PermissionError):
            agent.dispatch(task.id, ToolCall("health.check", {"target":"node-1"}, lease.id, "other"), lease)

    def test_auth_metadata_never_accepts_raw_secret(self):
        with tempfile.TemporaryDirectory() as d:
            store = AuthStore(Path(d)/"auth.json")
            profile = AuthProfile("codex-official","codex","oauth","acct",("model.read",),"vault://codex/default")
            store.save(profile)
            self.assertEqual(store.get("codex-official").token_ref, "vault://codex/default")
            with self.assertRaises(ValueError):
                store.save(AuthProfile("bad","x","api_key","a",token_ref="raw-secret"))
            broker = AuthBroker(store)
            with self.assertRaises(RuntimeError): broker.lease("codex-official", "model")

    def test_plugin_manifest_and_model_route(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/"ok").mkdir(); (root/"ok"/"plugin.json").write_text(json.dumps({"id":"x","version":"1","api_version":"v1","kind":"channel","capabilities":["send"]}), encoding="utf-8")
            manifests = PluginRegistry([root]).scan(); self.assertEqual(manifests[0]["id"], "x"); self.assertEqual(len(manifests[0]["hash"]),64)
            (root/"bad").mkdir(); (root/"bad"/"plugin.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(PluginManifestError): PluginRegistry([root]).scan()
        router = ModelRouter([EchoProvider()])
        result = router.complete(ModelRequest(({"role":"user","content":"hello"},)))
        self.assertEqual(result["content"], "hello")


if __name__ == "__main__": unittest.main()
