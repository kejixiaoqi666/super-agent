"""Autopilot —— 自动操作统一抽象（核心：能拿布局的绝不截图）。

Driver 是对「平台界面布局」的统一访问层：describe() 拿当前可操作元素（含坐标），
act() 注入动作。后端惰性加载，不用不占内存：
  - browser  : Playwright/CDP 挖 DOM（点赞/填表/点击，无截图）
  - desktop  : OS 可访问性树(AX/UIA/AT-SPI)（阶段③）
  - android  : ADB + UIAutomator 控件树（阶段③）

统一接口让智能体以同一套 describe/act 操作任意平台，后端按目标惰性换。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Element:
    """当前界面一个可操作元素（来自布局/可访问性树，非截图推断）。"""
    index: int
    tag: str = ""            # button/link/input/text/region...
    text: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    kind: str = "click"      # click | input | region | scroll
    attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"index": self.index, "tag": self.tag, "text": self.text,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "kind": self.kind}


class Driver(ABC):
    """平台界面驱动契约。describe 拿布局，act 注入动作。"""

    @abstractmethod
    def describe(self, text: str = "") -> List[Element]:
        """返回当前可操作元素快照（含坐标）。text 非空时只留含该文本的元素。"""

    @abstractmethod
    def act(self, action: dict) -> dict:
        """注入动作。action: {op: click|type|scroll|hover|back, ...}"""

    def find(self, text: str) -> Optional[Element]:
        """按文本精确定位一个可操作元素（无则 None，不猜）。"""
        for el in self.describe():
            if text and text.lower() in (el.text or "").lower():
                return el
        return None

    def close(self) -> None:
        pass


class Autopilot:
    """驱动调度：按目标平台惰性实例化后端。统一入口给智能体/多agent。"""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir
        self._drivers: Dict[str, Driver] = {}
        self._last: Optional[str] = None

    def driver(self, target: str = "browser") -> Driver:
        """取/建某平台驱动（惰性；同一进程内按 target 复用）。"""
        if target not in self._drivers:
            from .browser import BrowserDriver
            if target == "browser":
                self._drivers[target] = BrowserDriver(
                    headless=True, data_dir=self.data_dir)
            else:
                raise ValueError(f"未知自动操作后端: {target!r}")
        self._last = target
        return self._drivers[target]

    def describe(self, target: str = "browser", text: str = "") -> List[dict]:
        return [e.to_dict() for e in self.driver(target).describe(text)]

    def act(self, target: str, action: dict) -> dict:
        return self.driver(target).act(action)

    def click_text(self, target: str, text: str) -> dict:
        """按文本点一个元素（网页点赞/按钮等简单操作）。找不到返回 error 不猜。"""
        d = self.driver(target)
        el = d.find(text)
        if el is None:
            return {"op": "click", "ok": False, "error": f"未找到含『{text}』的元素"}
        return d.act({"op": "click", "index": el.index, "x": el.x, "y": el.y})

    def close(self) -> None:
        for d in self._drivers.values():
            try:
                d.close()
            except Exception:
                pass
        self._drivers.clear()
