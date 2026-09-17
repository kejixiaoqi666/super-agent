"""Private Telegram chats, polling, explicit sender allowlist; tokens come from .env (not code)."""
import json
import logging
import os
import time
import urllib.request

from . import config as cfg


def serve(body):
    # 正常流程：token/授权从 .env 读取（config 层），环境变量优先，绝不硬编码进代码。
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
            try:
                if not text:
                    continue
                reply = body.chat("telegram:" + str(chat["id"]), text, str(sender))["reply"]
                # 流式输出：打字效果（先发空消息，再 editMessageText 逐步追加；
                # 超 4096 上限自动分片成多条消息）
                from .stream import stream_telegram
                stream_telegram(
                    reply,
                    send=lambda t: api("sendMessage", chat_id=chat["id"],
                                       text=t)["message_id"],
                    edit=lambda mid, t: api("editMessageText",
                                            chat_id=chat["id"], message_id=mid,
                                            text=t),
                    min_delta=300)
            except Exception:
                logging.warning("Telegram turn failed; update will not be replayed automatically")
                try:
                    api("sendMessage", chat_id=chat["id"], text="本轮处理或发送失败，未自动重试工具动作。请检查任务结果。")
                except Exception:
                    logging.warning("Telegram error notification failed")
