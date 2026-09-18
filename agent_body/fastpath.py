"""轻量快通道：简单消息不经过超脑重型认知，用多信号启发式直接处理。

设计（用户定调 2026-09）："简单的东西不需要经过大脑，直接输入就好";
且要"能判断语义/复杂度，不是死命中"。

不引入本地 LLM（2 核机器上既慢又占内存，违背<1GB/几毫秒目标）。
改用**多信号启发式分类**（零额外内存、微秒级、语义化）：
  1) 分明信号 → 秒回(问候/道谢/算术/时间/非常短闲聊)
  2) 无法确判 → None → 交给超脑完整管线(安全，不错答)

复杂消息(IP/部署/查资料/分析/代码/长句…)默认走超脑——这里只兜"明确简单"。
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

# ---- 明确简单（规则秒回，零模型）----
_GREET = {"你好", "您好", "hi", "hello", "hey", "哈喽", "嗨", "在吗",
          "在么", "早上好", "中午好", "下午好", "晚上好", "你好啊", "好"}
_THANK = {"谢谢", "谢谢啦", "多谢", "辛苦", "辛苦了", "没事", "好的",
          "好哒", "明白了", "ok", "okay", "收到", "👌", "👍"}
_ACK = {"哦", "嗯", "啊", "好的好的", "没问题"}

_ARITH = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([+\-*/x×])\s*(\d+(?:\.\d+)?)\s*=\s*$")
_SHORT_PUNCT = re.compile(r"^[\s!?。，,.~～—、]{1,6}$")


def _calc(m: re.Match) -> str:
    a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
    r = {"+": a + b, "-": a - b, "*": a * b, "x": a * b, "×": a * b,
         "/": (a / b) if b else float("nan")}[op]
    return f"= {r:g}"


def fast_reply(text: str) -> Optional[str]:
    """返回命中的"明确简单"秒回文本；未命中返回 None（交给超脑）。"""
    t = (text or "").strip()
    lo = t.lower().replace(" ", "")
    if not t:                       # 空消息不回复（交给上层/不处理）
        return None

    # 问候
    if lo in _GREET:
        return ("你好！我是超脑。直接说需求即可。\n简单的我秒回、不经过大脑；"
                "复杂的事我才认真想（/help 看命令）。")
    # 道谢/确认
    if lo in _THANK or lo in _ACK:
        return "不客气！有需要随时说。"
    # 纯算术（如 "1+1="）
    m = _ARITH.match(lo)
    if m:
        try:
            return _calc(m)
        except Exception:
            pass
    # 当前时间/日期（本地即时，零模型）
    if lo in ("现在几点", "现在几点了", "几点了", "时间", "现在时间", "今天几号", "今天星期几", "什么时间"):
        now = datetime.datetime.now()
        if "号" in lo or "星期" in lo:
            return now.strftime("今天是 %Y-%m-%d（%A）")
        return now.strftime("现在 %H:%M:%S")
    # 非常短的无信息闲聊 / 纯符号
    if len(lo) <= 2 or _SHORT_PUNCT.match(lo):
        return "嗯，我在。直接说需要我做什么吧。"
    # 其余 → 无法确判简单，交给超脑（安全）
    return None