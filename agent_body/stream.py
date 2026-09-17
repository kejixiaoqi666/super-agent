"""流式输出 Stream —— 分块打字效果（Telegram）+ 生成器友好接口。

成熟 agent 普遍支持流式输出。Telegram 打字效果用「先发空消息，再逐步
editMessageText 追加」实现。本模块把流式逻辑做成纯函数/生成器，可测试，
TG/CLI 复用。
"""
from __future__ import annotations

import time
from typing import Callable, Generator, List


def chunk_text(text: str, size: int = 200) -> Generator[str, None, None]:
    """把长文本按 size 切成流式块（尊重边界，不切词中间）。"""
    if not text:
        yield ""
        return
    n = len(text)
    i = 0
    while i < n:
        j = min(i + size, n)
        # 尽量在标点/空格边界断句，避免切碎
        if j < n:
            for break_at in range(j, i, -1):
                if text[break_at - 1] in " \n，。！？；、,.!?;:）)】]":
                    j = break_at
                    break
        yield text[i:j]
        i = j


def stream_telegram(reply: str, send: Callable[[str], int],
                    edit: Callable[[int, str], None],
                    chunk: int = 200, delay: float = 0.0,
                    min_delta: int = 30) -> int:
    """流式发送一条回复（TG 打字效果）。

    send(text)  -> 返回 message_id（用于 edit）。
    edit(mid, text)  -> 覆盖消息内容。
    返回最终 message_id。delay 为块间暂停（秒）；min_delta 为触发 edit 的
    最小累计增量（避免每 200 字符都发请求，攒够再刷）。
    """
    message_id = send("…")
    buffer = ""
    last_edited = 0
    # 先缓存所有块，按 min_delta 攒批刷新
    for piece in chunk_text(reply, chunk):
        buffer += piece
        if len(buffer) - last_edited >= min_delta:
            edit(message_id, buffer)
            last_edited = len(buffer)
            if delay:
                time.sleep(delay)
    # 收尾：确保全量刷上去（最后一段可能不足 min_delta）
    if buffer and len(buffer) != last_edited:
        edit(message_id, buffer)
    return message_id


class StreamBuffer:
    """累积器：接收增量片段，按需产出「该发给模型的累计文本」。

    供 CLI / 其它流式场景用：每次 append 一个 chunk，返回当前累计缓冲。
    """

    def __init__(self, max_len: int = 2000):
        self._buf: List[str] = []
        self._max = max_len

    def append(self, chunk: str) -> str:
        self._buf.append(chunk)
        # 简单保留：超出 max_len 时丢弃最旧（防止无界增长）
        while self._buf and len("".join(self._buf)) > self._max:
            self._buf.pop(0)
        return self.text

    @property
    def text(self) -> str:
        return "".join(self._buf)

    def clear(self) -> None:
        self._buf.clear()
