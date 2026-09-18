"""Browser 自动操作后端 —— Playwright/CDP 挖 DOM 布局，不靠截图。

describe() 用一次 JS 求值遍历 DOM，返回可点击/可输入元素(含视口坐标)。
act() 用坐标/定位注入点击、输入、滚动。惰性 import playwright：不操作浏览器时零占用。
"""

from __future__ import annotations

from typing import List, Optional

from . import Driver, Element

# 挑选可交互元素的选择器（按钮/链接/输入/可点击角色）
_SELECTOR = ('a,button,input,textarea,select,'
             '[role="button"],[role="link"],[role="tab"],[role="checkbox"],'
             '[onclick],[contenteditable="true"]')

_SNAPSHOT_JS = f"""
() => {{
  const out = [];
  document.querySelectorAll('{_SELECTOR}').forEach(el => {{
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;             // 不可见/零尺寸跳过
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value ||
                  el.getAttribute('aria-label') || '').trim().slice(0, 80);
    const kind = ['input','textarea','select'].includes(tag) ? 'input' : 'click';
    out.push({{ tag, text,
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height), kind }});
  }});
  return out;
}}
"""


class BrowserDriver(Driver):
    """Playwright 驱动真实浏览器：describe 挖 DOM 布局，act 注入点击/输入。"""

    def __init__(self, headless: bool = True, data_dir: Optional[str] = None,
                 launch_args: Optional[List[str]] = None):
        self.headless = headless
        self.data_dir = data_dir
        self.launch_args = launch_args or ["--no-sandbox", "--disable-dev-shm-usage"]
        self._browser = None
        self._page = None
        self._pw = None      # playwright 模块（惰性 import）

    # ---- 惰性启动 ----
    def _ensure(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "playwright 未安装：pip install playwright && "
                "python -m playwright install chromium") from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless, args=self.launch_args)
        self._page = self._browser.new_page()
        return self._page

    def goto(self, url: str) -> dict:
        """打开页面。返回 {ok, title, url}。"""
        page = self._ensure()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"ok": True, "title": page.title(), "url": page.url}

    # ---- describe：挖布局 ----
    def describe(self, text: str = "") -> List[Element]:
        page = self._ensure()
        rows = page.evaluate(_SNAPSHOT_JS)
        els: List[Element] = []
        for i, r in enumerate(rows):
            el = Element(index=i, tag=r.get("tag", ""), text=r.get("text", ""),
                         x=r.get("x", 0), y=r.get("y", 0),
                         w=r.get("w", 0), h=r.get("h", 0),
                         kind=r.get("kind", "click"))
            if text and text.lower() not in (el.text or "").lower():
                continue
            els.append(el)
        return els

    # ---- act：注入动作 ----
    def act(self, action: dict) -> dict:
        page = self._ensure()
        op = action.get("op", "click")
        try:
            if op == "click":
                return self._click(page, action)
            if op == "type":
                return self._type(page, action)
            if op == "scroll":
                page.mouse.wheel(0, int(action.get("dy", 300)))
                return {"op": "scroll", "ok": True}
            if op == "hover":
                self._goto_element(page, action.get("index"))
                return {"op": "hover", "ok": True}
            if op == "back":
                page.go_back()
                return {"op": "back", "ok": True, "url": page.url}
            return {"op": op, "ok": False, "error": f"未知动作: {op}"}
        except Exception as e:
            return {"op": op, "ok": False,
                    "error": f"{type(e).__name__}: {e}"}

    def _bbox(self, page, index: int):
        """从活页面重新取第 index 个可交互元素的视口中心（页面可能已变）。"""
        rows = page.evaluate(_SNAPSHOT_JS)
        if index < 0 or index >= len(rows):
            return None
        r = rows[index]
        return int(r["x"] + r["w"] / 2), int(r["y"] + r["h"] / 2)

    def _goto_element(self, page, index: int):
        rows = page.evaluate(_SNAPSHOT_JS)
        if 0 <= index < len(rows):
            page.evaluate(
                "i => { const els=document.querySelectorAll(arguments[1]);"
                " const el=els[i]; if(el) el.scrollIntoView({block:'center'});}",
                index)

    def _click(self, page, action: dict) -> dict:
        index = action.get("index")
        if index is not None:
            cx, cy = self._bbox(page, index) or (None, None)
        else:
            cx, cy = action.get("x"), action.get("y")
        if cx is None or cy is None:
            return {"op": "click", "ok": False, "error": "无可用坐标/索引"}
        page.mouse.click(int(cx), int(cy))
        return {"op": "click", "ok": True, "x": int(cx), "y": int(cy)}

    def _type(self, page, action: dict) -> dict:
        text = action.get("text", "")
        index = action.get("index")
        if index is not None:
            self._click(page, {"index": index})   # 聚焦输入框
        page.keyboard.type(text)
        return {"op": "type", "ok": True, "chars": len(text)}

    # ---- 关闭 ----
    def close(self) -> None:
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
