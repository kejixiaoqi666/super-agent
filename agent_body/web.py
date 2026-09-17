"""Web 工具 —— 真实可用的 web_search / web_extract（stdlib urllib，无重依赖）。

补齐 runtime 里"只声明没实现"的 web_search/web_extract 缺口：
  - web_extract(url)：抓取网页，抽正文文本（HTMLParser 剥标签）。
  - web_search(query)：DuckDuckGo Lite 免 key 搜索，返回 title/url/snippet。
两者都用 urllib，超时/错误清晰抛 WebError，绝不让工具静默失败。
"""
from __future__ import annotations

import html as _html
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Dict, List

_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_TIMEOUT = 20.0


class WebError(RuntimeError):
    """网页/搜索失败（清晰报错，供上层转告）。"""


_MAX_BYTES = 4 * 1024 * 1024  # 单页下载上限 4MB，防超大/恶意页吃内存


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    charset = None
    data = b""
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            try:
                charset = r.headers.get_content_charset()
            except Exception:
                charset = None
            chunk = r.read(1 << 16)  # 分块读，超上限即中止
            while chunk:
                data += chunk
                if len(data) > _MAX_BYTES:
                    raise WebError(f"页面超过上限 {_MAX_BYTES // 1024}KB")
                chunk = r.read(1 << 16)
    except WebError:
        raise
    except Exception as e:
        raise WebError(f"抓取失败 {url}: {e}") from None
    # 尽力按 charset 解码；失败则 errors=replace
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    """剥 HTML 标签，只留可见文本。"""
    _IGNORE = {"script", "style", "head", "noscript", "svg", "iframe"}

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._IGNORE:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._IGNORE and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.parts.append(text)


def web_extract(url: str, max_chars: int = 8000) -> str:
    """抓取 URL 并抽取可见文本（去 script/style/标签，折叠空白）。"""
    raw = _get(url)
    p = _TextExtractor()
    try:
        p.feed(raw)
    except Exception:
        pass
    text = "\n".join(p.parts)
    if not text.strip():
        raise WebError(f"{url} 无可抽取文本（可能是图片/JS 页面）")
    return text[:max_chars]


def _parse_search_results(raw: str, limit: int = 5) -> List[Dict]:
    """解析 DuckDuckGo Lite HTML 结果块 → [{title, url, snippet}]。"""
    blocks = re.split(r'class="result results_links[^"]*"', raw)
    parsed: List[Dict] = []
    for b in blocks[1:]:
        a = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not a:
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        url = _html.unescape(a.group(1))
        # DuckDuckGo 结果 href 是跳转链接，含真实 URL 在 uddg= 参数
        if "uddg=" in url:
            url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
        title = re.sub(r"<[^>]+>", "", a.group(2)).strip()
        snippet = ""
        if sn:
            snippet = re.sub(r"<[^>]+>", "", sn.group(1)).strip()
        parsed.append({"title": _html.unescape(title), "url": url,
                       "snippet": _html.unescape(snippet)})
        if len(parsed) >= limit:
            break
    return parsed


def web_search(query: str, limit: int = 5) -> List[Dict]:
    """DuckDuckGo Lite 免 key 搜索。返回 [{title, url, snippet}]。"""
    q = urllib.parse.quote(query)
    raw = _get(f"https://html.duckduckgo.com/html/?q={q}")
    parsed = _parse_search_results(raw, limit)
    if not parsed:
        raise WebError("搜索无结果（可能被反爬或查询无匹配）")
    return parsed
