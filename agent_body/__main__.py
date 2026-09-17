import argparse
import json
import sys
from pathlib import Path


def _vault_cmd(body, arg: str, master_password: str = ""):
    """处理 /vault 子命令。master_password 为空则全部操作需显式传入主密码。"""
    if not arg:
        print("用法: /vault set <name> <value> [tier] | /vault get <name> "
              "| /vault list | /vault rm <name>")
        return
    parts = arg.split()
    cmd = parts[0]
    mp = master_password
    if not mp:
        print("未配置主密码。请以 --vault-master <主密码> 启动，或先 set 时提供。")
        return
    try:
        vault = body.open_vault(mp)
    except Exception as e:
        print(f"密码本不可用: {e}")
        return
    if cmd == "set":
        if len(parts) < 3:
            print("用法: /vault set <name> <value> [tier]")
            return
        name = parts[1]
        value = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2]
        tier = parts[-1].lower() if len(parts) > 3 and parts[-1].lower() in (
            "normal", "high", "payment") else "normal"
        vault.set(name, value, tier=tier)
        print(f"已存: {name} [tier={tier}]")
    elif cmd == "get":
        if len(parts) < 2:
            print("用法: /vault get <name>")
            return
        try:
            v = vault.get(parts[1], explicit=True)
            print(v)
        except KeyError:
            print(f"不存在: {parts[1]}")
        except PermissionError as e:
            print(f"已拦截: {e}")
    elif cmd == "list":
        for m in vault.list_meta():
            print(f"- {m['name']} [{m['tier']}] {m['notes']}")
        if not vault.list_meta():
            print("（空）")
    elif cmd == "rm":
        print("已删除" if vault.delete(parts[1]) else f"不存在: {parts[1]}")
    else:
        print(f"未知子命令: {cmd}")


def main():
    parser = argparse.ArgumentParser(description="AgentWorkbench body with SuperBrain 2.0")
    parser.add_argument("--kernel", type=Path, default=Path(__file__).resolve().parents[2] / "superbrain-2.0" / "python")
    parser.add_argument("--data", default=".body-data")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--mode", choices=["read-only", "workspace", "unrestricted"], default="workspace")
    parser.add_argument("--session", default="local")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--vault-master", default=None,
                        help="密码本主密码（不落盘，仅用于派生加密密钥）；缺省则 vault 命令不可用")
    args = parser.parse_args()
    sys.path.insert(0, str(args.kernel.resolve()))
    from .runtime import Body
    body = Body(args.data, args.workspace, args.mode)
    try:
        if args.telegram:
            from .telegram import serve
            serve(body)
        else:
            print("AgentWorkbench / SuperBrain 2.0. /quit /state /tick")
            while True:
                try:
                    message = input("> ").strip()
                except EOFError:
                    break
                if message == "/quit":
                    break
                if not message:
                    continue
                if message == "/state":
                    print(json.dumps(body.brain(args.session).state(), ensure_ascii=False, default=str))
                elif message == "/tick":
                    print(json.dumps(body.tick(args.session), ensure_ascii=False, default=str))
                elif message.startswith("/task "):
                    # /task <目标> —— 提交并运行自主任务
                    print(json.dumps(body.run_task(message[6:].strip(), session=args.session),
                                     ensure_ascii=False, default=str))
                elif message == "/tasks":
                    print(json.dumps(body.task_status(), ensure_ascii=False, default=str))
                elif message.startswith("/task-cancel "):
                    print(json.dumps(body.task_cancel(message[13:].strip()),
                                     ensure_ascii=False, default=str))
                elif message == "/pending":
                    # 未完成清单（FAILED/CANCELED 可续跑）
                    pending = body.pending_tasks()
                    if not pending:
                        print("（无未完成任务）")
                    for p in pending:
                        print(f"- {p['task_id']} [{p['status']}] {p['goal']} "
                              f"(step {p['next_step']}/{p['plan_len']})")
                elif message.startswith("/resume"):
                    # /resume            → 续跑全部
                    # /resume <task_id>  → 续跑指定
                    parts = message.split()
                    tid = parts[1] if len(parts) > 1 else None
                    print(json.dumps(body.resume_tasks(tid, args.session),
                                     ensure_ascii=False, default=str))
                elif message == "/plugins":
                    # 发现并列出插件
                    plugins = body.scan_plugins()
                    if not plugins:
                        print("（无插件）")
                    for p in plugins:
                        print(f"- {p.id} v{p.version} caps={p.capabilities}")
                elif message.startswith("/plugins-enable "):
                    cap = message[len("/plugins-enable "):].strip()
                    res = body.enable_capability(cap)
                    print(f"启用 {len(res)} 个插件: {[r['id'] for r in res]}")
                elif message.startswith("/trace"):
                    # /trace           → 最近一次 trace
                    # /trace <trace_id> → 指定 trace
                    parts = message.split()
                    tid = parts[1] if len(parts) > 1 else body.tracer.trace_id()
                    events = body.tracer.read_trace(tid)
                    print(f"trace {tid}: {len(events)} 条")
                    for e in events:
                        print(f"  {e.get('ts', '')} {e.get('event')} "
                              f"{e.get('name', '')} "
                              f"{e.get('error', '')}{e.get('elapsed_ms', '')}")
                elif message == "/storage":
                    # 存储占用 + 回收过期资产
                    print(json.dumps(body.storage_report(), ensure_ascii=False, default=str))
                elif message == "/gc":
                    r = body.gc()
                    print(f"回收: 扫描{r['scanned']} 删除{r['removed']} "
                          f"释放{r['freed_bytes']}bytes")
                elif message.startswith("/image "):
                    # /image <路径> [session] —— 压缩入库
                    parts = message[7:].strip().split()
                    src = parts[0]
                    session = parts[1] if len(parts) > 1 else "default"
                    res = body.images.ingest(src, session=session)
                    print(res.summary())
                elif message == "/vault":
                    print("密码本命令: /vault set <name> <value> [tier] | "
                          "/vault get <name> | /vault list | /vault rm <name>")
                elif message.startswith("/vault "):
                    _vault_cmd(body, message[7:].strip(), args.vault_master)
                else:
                    print(body.chat(args.session, message)["reply"])
    finally:
        body.close()


if __name__ == "__main__":
    main()
