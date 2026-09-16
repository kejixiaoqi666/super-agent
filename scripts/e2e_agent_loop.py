"""真实模型端到端：AgentLoop 用真实大脑 + 真实身体工具跑一次自主任务。

验证：提交任务 → 大脑规划 → 身体工具执行(写文件) → 验证门禁(文件存在) → 记忆记录。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_body.runtime import Body
from agent_body.loop import AgentLoop, VerificationGate


def main():
    # 临时工作目录：任务完成后验证 agent 确实用它写了文件
    tmp = tempfile.mkdtemp(prefix="sa-e2e-")
    work = Path(tmp) / "work"; work.mkdir()
    data = Path(tmp) / "data"

    body = Body(str(data), str(work), mode="unrestricted")
    loop = AgentLoop(str(data), str(work), body.brain)
    try:
        brain = body.brain("e2e")
        # 提交任务：让 agent 用工具写一个 hello.txt
        task = loop.submit("在工作目录用 write_file 工具创建文件 hello.txt，内容为 'autonomous'", owner="e2e")
        gate = VerificationGate(work)
        gate.add("hello.txt 已创建", "file_exists", path="hello.txt")
        gate.add("内容正确", "content_contains", path="hello.txt", content="autonomous")
        s = loop.run(task.task_id, brain, verifier=gate)
        print("=== 任务结果 ===")
        print(json.dumps(s, ensure_ascii=False, indent=2))
        print("=== 文件验证 ===")
        p = work / "hello.txt"
        print(f"文件存在: {p.exists()}")
        if p.exists():
            print(f"内容: {p.read_text()!r}")
        print(f"大脑记忆条数: {brain.memory_count()}")
    finally:
        body.close()


if __name__ == "__main__":
    main()
