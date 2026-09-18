"""Telegram bot：私有私聊轮询 + 显式白名单 + 斜杠命令路由 + 真·流式回复。

token/授权从 .env 读取（不硬编码）。命令带中文注释，路由到 Body 能力面。
普通对话走 superbrain 真·流式（首个 token 秒显），非"干等全量"假流式。
"""
import json
import logging
import os
import time
import urllib.request

from . import config as cfg

_HELP = """可用命令（快速操作，都是调 Body 能力面）:
/start /help   本帮助
/new            新建会话(跨会话重建, 自动带前文; 新会话号 telegram:<id>#N)
/state          看超脑内核状态
/tick           超脑认知推进(需求/情绪/自主想法)
/task <目标>     提交并运行一个自主任务
/tasks          任务列表
/pending        未完成任务(可续跑)
/resume [id]     续跑任务(缺省全部)
/plugins        列插件
/continuity     自动续接状态(当轮真实输入/建议)
/next           预动性建议
/selftest       跑测试自检(找缺陷)
/ops            自运维诊断(健康/待办)
/gc             回收过期缓存
平时直接发消息即可(真·流式回复)。"""


def _route_command(body, cmd, session, chat_id, sess_map, sfile) -> str:
    """斜杠命令路由：把命令路由到 Body 能力面，返回要发给用户的文本。"""
    parts = cmd.strip().split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    try:
        if name in ("/start", "/help"):
            return _HELP
        if name == "/new":                       # 新建会话：递增会话号并持久化
            sess_map[str(chat_id)] = sess_map.get(str(chat_id), 1) + 1
            sfile.write_text(json.dumps(sess_map, ensure_ascii=False))
            n = sess_map[str(chat_id)]
            return (f"已新建会话：你现在在第 {n} 个会话。"
                    f"下次消息用 telegram:{chat_id}#{n}，超脑自动带上前文。")
        if name == "/state":
            return json.dumps(body.brain(session).state(), ensure_ascii=False,
                              default=str)[:1000]
        if name == "/tick":
            return str(body.tick(session))[:1000]
        if name == "/task":
            if not arg:
                return "用法: /task <目标>"
            return json.dumps(body.run_task(arg, session=session),
                              ensure_ascii=False, default=str)[:1000]
        if name == "/tasks":
            return str(body.task_status())[:1000]
        if name == "/pending":
            pend = body.pending_tasks()
            return ("（无未完成任务）" if not pend else
                    "\n".join(f"- {p['task_id']} [{p['status']}] {p['goal']}"
                              for p in pend[:20]))
        if name == "/resume":
            return json.dumps(body.resume_tasks(arg or None, session),
                              ensure_ascii=False, default=str)[:1000]
        if name == "/plugins":
            return "\n".join(body.scan_plugins()) or "（无插件）"
        if name == "/continuity":
            c = body.continuity_status(session)
            return (f"当轮输入 {c.get('last_input', 0):,}/{c.get('window', '?')} "
                    f"({c.get('input_pct', 0) * 100:.1f}%) 级别={c.get('level', 'ok')}")
        if name == "/next":
            actions = body.next_actions()
            return ("\n".join(f"- {a['title']} -> {a['command']}" for a in actions)
                    or "（暂无预动性建议）")
        if name == "/selftest":
            return json.dumps(body.selftest(), ensure_ascii=False,
                              default=str)[:1000]
        if name == "/ops":
            return json.dumps(body.ops_diagnose(), ensure_ascii=False,
                              default=str)[:1500]
        if name == "/gc":
            return str(body.gc())[:500]
        return f"未知命令 {name}，/help 看可用命令。"
    except Exception as e:                        # 命令出错不影响轮询(不崩 bot)
        return f"命令执行失败: {type(e).__name__}: {e}"


def serve(body):
    # token/授权从 .env 读取（config 层），环境变量优先，绝不硬编码进代码。
    conf = cfg.load()
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or conf.get("TELEGRAM_BOT_TOKEN", "")
    raw_allowed = (os.environ.get("TELEGRAM_ALLOWED_USERS")
                   or conf.get("TELEGRAM_ALLOWED_USERS", ""))
    if not token:
        raise SystemExit(
            "未配置 TELEGRAM_BOT_TOKEN。请运行 `python -m agent_body.config` 或 TUI "
            "的「配置」填入机器人 token（@BotFather 获取）。")
    allowed = {int(x.strip()) for x in raw_allowed.split(",") if x.strip()}
    if not allowed:
        raise SystemExit(
            "未配置 TELEGRAM_ALLOWED_USERS。请在配置里填入你的 Telegram 数字 ID（逗号分隔）。")
    checkpoint = body.data_dir / "telegram-offset.json"
    offset = json.loads(checkpoint.read_text()) if checkpoint.exists() else 0

    def api(method, **payload):
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/" + method,
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=40) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError("Telegram request failed")
        return result["result"]

    while True:
        try:
            updates = api("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
        except Exception:
            # Exception strings can contain the URL (and thus the token).
            logging.warning("Telegram polling failed; retrying")
            time.sleep(3)
            continue
        for update in updates:
            # Reserve before execution. No automatic replay of side effects after crashes.
            offset = update["update_id"] + 1
            tmp = checkpoint.with_suffix(".tmp")
            tmp.write_text(json.dumps(offset), encoding="utf-8")
            tmp.replace(checkpoint)
            message = update.get("message", {})
            sender = message.get("from", {}).get("id")
            chat = message.get("chat", {})
            text = message.get("text")
            if sender not in allowed or chat.get("type") != "private" or not text:
                continue
            # ---- 会话号：/new 时递增，形成新会话(telegram:<id>#N)，跨会话重建自动带前文 ----
            sfile = body.data_dir / "telegram-session.json"
            try:
                sess_map = json.loads(sfile.read_text()) if sfile.exists() else {}
            except Exception:
                sess_map = {}
            n = sess_map.get(str(chat["id"]), 1)
            session = f"telegram:{chat['id']}" if n == 1 else f"telegram:{chat['id']}#{n}"

            # ---- ① 斜杠命令路由（带中文注释，供维护）----
            if text.strip().startswith("/"):
                reply = _route_command(body, text, session, chat["id"], sess_map, sfile)
                api("sendMessage", chat_id=chat["id"], text=reply)
                continue

            # ---- ② 轻量快通道：简单消息不经过大脑，纯规则秒回 ----
            from .fastpath import fast_reply
            quick = fast_reply(text)
            if quick is not None:
                api("sendMessage", chat_id=chat["id"], text=quick)
                continue

            # ---- ③ 普通对话：真·流式回复（首个 token 秒显，同 Hermes 打字效果）----
            try:
                brain = body.brain(session)
                mid = api("sendMessage", chat_id=chat["id"], text="⏳")["message_id"]
                acc = ""
                for kind, data in brain.chat_stream(text):
                    if kind == "text":
                        acc += data
                        api("editMessageText", chat_id=chat["id"],
                            message_id=mid, text=acc)
                    elif kind == "error":
                        acc += f"\n[流式错误] {data}"
                        api("editMessageText", chat_id=chat["id"],
                            message_id=mid, text=acc)
                        break
                # 超长分片兜底（Telegram 单消息 4096 上限）
                if len(acc) > 4000:
                    try:
                        api("deleteMessage", chat_id=chat["id"], message_id=mid)
                    except Exception:
                        pass
                    for i in range(0, len(acc), 4000):
                        api("sendMessage", chat_id=chat["id"],
                            text=acc[i:i + 4000])
            except Exception:
                logging.warning("Telegram turn failed", exc_info=True)
                try:
                    api("sendMessage", chat_id=chat["id"],
                        text="本轮处理失败，未自动重试工具动作。")
                except Exception:
                    logging.warning("Telegram error notification failed")