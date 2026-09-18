"""Headless daemon 守护冒烟测试（真正运行验证见服务/systemd）。"""

import unittest


class DaemonSmokeTest(unittest.TestCase):
    def test_daemon_module_exports_serve(self):
        from agent_body import daemon
        self.assertTrue(callable(daemon.serve))

    def test_daemon_serve_runs_ticks_on_fake_body(self):
        import threading
        import time
        from agent_body import daemon

        calls = []
        class FakeBody:
            def brain(self, session): calls.append(("brain", session)); return None
            def tick(self, session): calls.append(("tick", session))
            def ops_health(self): calls.append(("ops",))
            def pending_tasks(self): return []
            def resume_tasks(self, tid, session): pass
            def close(self): calls.append(("close",))

        original = daemon.signal.signal
        daemon.signal.signal = lambda *a, **k: None   # 测试环境不装信号处理
        try:
            t = threading.Thread(target=daemon.serve,
                                 args=(FakeBody(),), kwargs={"interval": 0.01},
                                 daemon=True)
            t.start()
            time.sleep(0.2)   # 跑若干 tick
            # serve 用 signal 停止较难在单测触发; 这里只断言它内循环有推进且不崩
            self.assertTrue(any(c[0] == "brain" for c in calls))
        finally:
            daemon.signal.signal = original
        # 恢复真实 signal(此处不再阻塞; 服务模式由 systemd 管)

    def test_main_accepts_daemon_flag(self):
        import subprocess
        r = subprocess.run(
            ["/root/projects/super-agent/.venv/bin/sa", "--help"],
            capture_output=True, text=True)
        self.assertIn("--daemon", r.stdout)


if __name__ == "__main__":
    unittest.main()