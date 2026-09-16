"""真实验证：Phase 2 项目感知精挑输入 + 上下文预算。

用真实模型 + 真实大脑，验证：
  ① 设定项目上下文后，对话走精挑（project 注入、记忆过滤、预算记账）
  ② 返回里带 dropped_memory（滤掉多少无关记忆）
  ③ 预算文件真实记账（token 统计落盘）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_body.runtime import Body

ROOT = Path(__file__).resolve().parents[1]


def main():
    tmp = tempfile.mkdtemp(prefix="sa-p2-")
    body = Body(f"{tmp}/data", f"{tmp}/work")
    session = "e2e-p2"

    print("=== ① 设定项目上下文 ===")
    r = body.set_project("网关服务项目", "检查18830端口路由规则",
                         tags=["gateway", "18830", "routing"])
    print("set:", r["set"], "| tags:", r["tags"])

    print("\n=== ② 真实对话（走精挑输入 + 预算记账）===")
    out = body.chat(session, "帮我看看这个网关路由怎么配")
    print("回复:", out["reply"][:80], "...")
    print("滤掉无关记忆:", out.get("dropped_memory", 0), "条")

    print("\n=== ③ 项目/预算状态 ===")
    ctx = body.context_status()
    print("项目上下文:", ctx["project"])
    print("预算:", json.dumps(ctx["budget"], ensure_ascii=False))
    print("工具数:", ctx["tools"])

    # 预算落盘验证
    budget_file = Path(tmp) / "data" / "budget.json"
    if budget_file.exists():
        b = json.loads(budget_file.read_text(encoding="utf-8"))
        s = b["sessions"].get(session, {})
        print(f"\n=== ④ 预算落盘: {session} prompt={s.get('prompt')} completion={s.get('completion')} messages={s.get('messages')} ===")
    else:
        print("\n⚠️ budget.json 未生成")

    body.close()
    return out


if __name__ == "__main__":
    main()
