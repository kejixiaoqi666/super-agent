"""TUI —— 好看简单的交互界面（基于 rich，无额外重依赖）。

主菜单：💬 对话 · 📋 任务 · ⚙️ 设置 · 📊 用量
设置页可交互填入：API、机器人 token、授权用户 —— 全部走 config.py 存 .env，
不硬编码、不被 git 跟踪。

用法：python -m agent_body.tui [--workspace DIR] [--data DIR] [--mode MODE]
"""
from __future__ import annotations

import argparse
import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box

from . import config as cfg
from .runtime import Body


def _menu() -> str:
    c = Console()
    c.print(Panel.fit(
        "[bold cyan]🧠 Super-Agent[/bold cyan]\n"
        "[dim]大脑思考 · 身体执行 · 自主任务[/dim]\n\n"
        "[1] 💬 对话      [2] 📋 自主任务\n"
        "[3] ⚙️ 配置      [4] 📊 用量统计\n"
        "[5] 🚀 启动对话  [6] 🔧 工具箱\n"
        "[0] 退出",
        border_style="cyan"))
    return Prompt.ask("选择", choices=["1", "2", "3", "4", "5", "6", "0"], default="1")


def _chat(body, console):
    console.print("[bold]💬 对话（输入 /quit 返回）[/bold]")
    while True:
        msg = Prompt.ask("你")
        if msg.strip() in ("/quit", "/q", "/exit"):
            break
        if not msg.strip():
            continue
        if msg.strip() == "/tick":
            console.print(json.dumps(body.tick("tui"), ensure_ascii=False, default=str))
            continue
        r = body.chat("tui", msg)
        console.print(Panel(r["reply"], title=f"[dim]{r['elapsed_seconds']}s[/dim]",
                             border_style="green", box=box.ROUNDED))


def _tasks(body, console):
    console.print("[bold]📋 自主任务[/bold]")
    goal = Prompt.ask("目标（输入空返回）")
    if not goal.strip():
        return
    console.print("[dim]执行中…[/dim]")
    s = body.run_task(goal, session="tui")
    console.print(Panel(json.dumps(s, ensure_ascii=False, indent=2, default=str),
                        title=f"任务 {s.get('task_id','')}",
                        border_style="cyan", box=box.ROUNDED))


def _settings(console):
    console.print("[bold]⚙️ 配置（跳过=保留原值）[/bold]")
    cfg.configure(console)
    report = cfg.status_report()
    t = Table(title="当前配置", box=box.SIMPLE_HEAVY)
    t.add_column("项"); t.add_column("状态"); t.add_column("值", style="cyan")
    for k, info in report.items():
        if k == "llm_key_ready":
            continue
        t.add_row(k, "✅" if info["set"] else "⬜", info["value"])
    t.add_row("LLM Key 就绪", "✅" if report.get("llm_key_ready") else "⬜", "-")
    console.print(t)


def _usage(body, console):
    s = body.stats.summary()
    t = Table(title=f"📊 用量统计（近 {s['days']} 天）", box=box.SIMPLE_HEAVY)
    t.add_column("指标"); t.add_column("值", style="cyan")
    t.add_row("对话次数", str(s["conversations"]))
    t.add_row("总 token", f"{s['total_tokens']:,}")
    t.add_row("输入 token", f"{s['prompt_tokens']:,}")
    t.add_row("输出 token", f"{s['completion_tokens']:,}")
    t.add_row("平均每轮 token", str(s["average_per_turn"]))
    t.add_row("估算成本(元)", f"¥{s['cost_cny']:.4f}")
    console.print(t)
    if s["by_day"]:
        td = Table(title="按日", box=box.SIMPLE)
        td.add_column("日期"); td.add_column("次数"); td.add_column("token"); td.add_column("成本")
        for day, d in sorted(s["by_day"].items()):
            td.add_row(day, str(d["count"]), str(d["prompt"] + d["completion"]),
                       f"¥{d['cost']:.4f}")
        console.print(td)


def _cron(body, console):
    console.print("[dim]定时任务[/dim]")
    while True:
        op = Prompt.ask("操作", choices=["list", "add", "rm", "run", "back"],
                        default="list")
        if op == "back":
            break
        if op == "list":
            for j in body.cron_list():
                console.print(f"- {j['id']} 规格={j['spec']} "
                              f"下次={j['next_run']} 已跑{j['count']}次")
        elif op == "add":
            jid = Prompt.ask("任务id")
            spec = Prompt.ask("规格(@every 30m | 五段cron | @ISO)")
            prompt = Prompt.ask("提示词(可空)")
            try:
                job = body.cron_add(jid, spec, payload={"prompt": prompt})
                console.print(f"[green]✅ {jid} -> 下次 {job['next_run']}[/green]")
            except ValueError as e:
                console.print(f"[red]规格无效: {e}[/red]")
        elif op == "rm":
            jid = Prompt.ask("任务id")
            console.print("已删除" if body.cron_rm(jid) else "不存在")
        elif op == "run":
            ran = body.cron_run_due()
            console.print("(无到期任务)" if not ran else f"已执行: {ran}")


def _tools(body, console):
    console.print("[bold]🔧 工具箱[/bold]（会话检索 / 委派 / 定时任务 / 技能）")
    while True:
        act = Prompt.ask("功能", choices=["search", "delegate", "cron", "skills",
                                        "back"], default="back")
        if act == "back":
            break
        if act == "search":
            q = Prompt.ask("检索关键词")
            hits = body.search_sessions(q)
            if not hits:
                console.print("(无命中)")
            for h in hits[:10]:
                if "error" in h:
                    console.print(f"[red]{h['error']}[/red]")
                else:
                    console.print(f"[{h['session']}] {h['snip']}")
        elif act == "delegate":
            goal = Prompt.ask("委派目标")
            r = body.delegate(goal)
            console.print(Panel(r.get("summary") or "(空)", title="子代理结果",
                                border_style="magenta", box=box.ROUNDED))
        elif act == "cron":
            _cron(body, console)
        elif act == "skills":
            for s in body.scan_skills():
                console.print(f"- {s['name']} v{s['version']} {s['description']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Super-Agent TUI")
    ap.add_argument("--workspace", default=".", help="工作目录")
    ap.add_argument("--data", default=".body-data", help="数据目录")
    ap.add_argument("--mode", choices=["read-only", "workspace", "unrestricted"],
                    default="workspace")
    args = ap.parse_args(argv)

    c = Console()
    body = Body(args.data, args.workspace, args.mode)
    try:
        while True:
            choice = _menu()
            if choice == "0":
                break
            if choice == "1":
                _chat(body, c)
            elif choice == "2":
                _tasks(body, c)
            elif choice == "3":
                _settings(c)
            elif choice == "4":
                _usage(body, c)
            elif choice == "5":
                _chat(body, c)  # 启动对话
            elif choice == "6":
                _tools(body, c)
    finally:
        body.close()
    c.print("[dim]再见 👋[/dim]")


if __name__ == "__main__":
    main()