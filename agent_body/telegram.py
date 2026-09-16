"""Private Telegram chats, polling, explicit sender allowlist; no tokens in files."""
import json
import logging
import os
import time
import urllib.request


def serve(body):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed = {int(x.strip()) for x in os.environ["TELEGRAM_ALLOWED_USERS"].split(",") if x.strip()}
    if not allowed:
        raise ValueError("TELEGRAM_ALLOWED_USERS must not be empty")
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
                if len(text) > 16000:
                    raise ValueError("message too long")
                reply = body.chat("telegram:" + str(chat["id"]), text, str(sender))["reply"]
                for start in range(0, len(reply), 1800):
                    api("sendMessage", chat_id=chat["id"], text=reply[start:start + 1800])
            except Exception:
                logging.warning("Telegram turn failed; update will not be replayed automatically")
                try:
                    api("sendMessage", chat_id=chat["id"], text="本轮处理或发送失败，未自动重试工具动作。请检查任务结果。")
                except Exception:
                    logging.warning("Telegram error notification failed")
