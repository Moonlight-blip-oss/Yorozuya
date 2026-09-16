# -*- coding: utf-8 -*-
"""联网工具：web_fetch（抓一个网页/接口，转成可读文本）+ web_search（搜索引擎查资料）

★ 风险等级 = **exec（要审批）**：它把请求发到本机之外，属于"外部动作"。
  只读本地文件的工具免审，但出网这一步必须让人知道（一次批准 = 本会话允许该工具）。

边界（写在工具描述里，让模型知道做不到什么）：
· 只支持 http/https；只做 GET；不执行 JS（拿不到 SPA 渲染后的内容）。
· 有大小上限与超时；HTML 走**极简**转文本（去掉 script/style，压掉多余空白）。
· `web_search` 走的是**公开搜索结果页**（不需要 API Key）—— 代价是可能被限流、
  以及对方改版后解析会失效；两种情况都**明确报错**，绝不给一个空结果假装"没搜到"。
"""
from __future__ import annotations

import gzip
import html
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import ToolResult, exec_tool

TIMEOUT = 20
MAX_BYTES = 3 * 1024 * 1024
# 搜索结果的源站。★ 用 bing 的国内站点：免 Key、本机实测可达（DuckDuckGo 在这里是被墙的，
#   所以"看起来更中立"的 ddg 反而不可用 —— 选源要以**实测可达**为准，不是以名气为准）。
SEARCH_URL = "https://cn.bing.com/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 YorozuyaAgent/1.0")



def html_to_text(raw: str) -> str:
    """极简 HTML → 文本：去脚本/样式/注释，块级标签转换行，反转义实体，压空白。"""
    s = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)</(p|div|li|tr|h[1-6]|section|article|header|footer|ul|ol|table)>", "\n", s)
    s = re.sub(r"(?is)<li[^>]*>", "- ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def pick_title(raw: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw or "")
    if not m:
        return ""
    # 标题里常有 <b> 之类的内联标签：先剥标签再压空白（否则会带进工具输出里）
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", m.group(1))).strip())


def check_url(url: str) -> tuple:
    """返回 (ok, 说明)。只放行 http/https，其它一律拒（file:// 这类是本地读取，不该从这儿走）。"""
    u = (url or "").strip()
    if not u:
        return False, "url 不能为空"
    if not re.match(r"(?i)^https?://", u):
        return False, "只支持 http:// 或 https:// 开头的地址"
    try:
        host = urllib.parse.urlsplit(u).hostname or ""
    except Exception:
        return False, "url 解析不了"
    if not host:
        return False, "url 里没有主机名"
    return True, ""


def fetch(url: str) -> tuple:
    """抓取并解码。返回 (ok, 文本, 元信息)。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:      # noqa: S310（只放行 http/https）
            ctype = (r.headers.get("Content-Type") or "").lower()
            raw = r.read(MAX_BYTES + 1)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            text = raw[:MAX_BYTES].decode("utf-8", "replace")
            meta = {"status": getattr(r, "status", 200), "content_type": ctype,
                    "bytes": len(raw), "truncated": len(raw) > MAX_BYTES,
                    "final_url": r.geturl()}
            return True, text, meta
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(200_000).decode("utf-8", "replace")
        except Exception:
            pass
        return False, f"HTTP {e.code} {e.reason}\n{html_to_text(body)[:600]}", {"status": e.code}
    except urllib.error.URLError as e:
        return False, (f"连不上：{e.reason}\n"
                       f"（本机网络/代理设置可能导致——如果是内网地址请先确认可达）"), {}
    except Exception as e:                                            # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", {}


def parse_results(page: str, limit: int = 5) -> list:
    """从搜索结果页里抠出 [{title, url, snippet}]。

    ★ 只做**保守**匹配（认结果块的容器 + h2 里的链接）：认不出来就返回空，
      由调用方明确报"没解析到结果（可能被限流或改版）"——
      宁可说"搜不到"，也不要给一堆乱七八糟的文本假装搜到了。
    """
    out = []
    for block in re.findall(r'(?is)<li class="b_algo".*?</li>', page or ""):
        m = re.search(r'(?is)<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not m:
            continue
        url = html.unescape(m.group(1)).strip()
        title = html.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(2))).strip()
        snip = ""
        sm = re.search(r'(?is)<p[^>]*>(.*?)</p>', block)
        if sm:
            snip = html.unescape(re.sub(r"(?s)<[^>]+>", " ", sm.group(1))).strip()
            snip = re.sub(r"\s+", " ", snip)
        if url and title:
            out.append({"title": title, "url": url, "snippet": snip[:300]})
        if len(out) >= limit:
            break
    return out


def search(query: str, count: int = 5) -> tuple:
    """搜一下。返回 (ok, 结果列表, 说明)。**失败必带原因**（这条纪律是踩过才写下的）。"""
    q = (query or "").strip()
    if not q:
        return False, [], "query 不能为空"
    url = SEARCH_URL + "?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:      # noqa: S310（只走固定源站）
            raw = r.read(MAX_BYTES + 1)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            page = raw[:MAX_BYTES].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, [], (f"搜索源返回 HTTP {e.code} {e.reason}"
                           f"（多半是被限流了，过一会儿再试；也可以先用 web_fetch 直接抓已知网址）")
    except urllib.error.URLError as e:
        return False, [], (f"连不上搜索源（{e.reason}）——本机网络或代理不通时就是这样。"
                           f"如果只是抽风，过一会儿再试一次。")
    except Exception as e:                                            # noqa: BLE001
        return False, [], f"{type(e).__name__}: {e}"

    results = parse_results(page, count)
    if not results:
        return False, [], ("搜索源没有返回可解析的结果（可能被限流，或它的页面结构变了）。"
                           "这不是「没有相关结果」，是**这次没拿到**——别当成事实用。")
    return True, results, ""


def build_web_tools(ws, output_limit: int):
    def web_fetch(args: dict) -> ToolResult:
        url = str(args.get("url") or "").strip()
        ok, why = check_url(url)
        if not ok:
            return ToolResult(False, error=why)
        ok2, text, meta = fetch(url)
        if not ok2:
            return ToolResult(False, error=text, data=meta)
        ctype = meta.get("content_type") or ""
        if "html" in ctype or text.lstrip()[:1] == "<":
            title = pick_title(text)
            body = html_to_text(text)
            head = f"# {title}\n" if title else ""
            body = head + body
        else:
            body = text                       # JSON / 纯文本原样给
        body, truncated = _clip(body, output_limit)
        return ToolResult(True,
                          summary=f"{meta.get('status')} {meta.get('final_url') or url}"
                                  f"（{meta.get('bytes')} 字节）",
                          output=body, truncated=truncated, data=meta)

    def web_search(args: dict) -> ToolResult:
        q = str(args.get("query") or "").strip()
        try:
            count = int(args.get("count") or 5)
        except Exception:
            return ToolResult(False, error="count 要是个整数（1–10）")
        count = max(1, min(count, 10))
        ok, rows, why = search(q, count)
        if not ok:
            return ToolResult(False, error=why,
                              data={"query": q, "source": SEARCH_URL})
        lines = [f"搜索：{q}（{len(rows)} 条）"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}" + (f"\n   {r['snippet']}" if r["snippet"] else ""))
        lines.append("要看全文就用 web_fetch 抓上面某条的链接。")
        body, truncated = _clip("\n".join(lines), output_limit)
        return ToolResult(True, summary=f"「{q}」{len(rows)} 条结果",
                          output=body, truncated=truncated,
                          data={"query": q, "count": len(rows), "results": rows,
                                "source": SEARCH_URL})

    return [
        exec_tool("web_fetch",
                  "抓一个网页/接口并转成可读文本（GET；不执行 JS；不搜索）。"
                  f"只支持 http/https；上限 {MAX_BYTES // 1024 // 1024}MB、{TIMEOUT}s；"
                  "HTML 会去掉脚本样式压成文本，JSON 原样返回。",
                  {"type": "object", "required": ["url"], "properties": {
                      "url": {"type": "string", "description": "完整地址（含 http:// 或 https://）"}}},
                  web_fetch, preview_keys=("url",)),
        exec_tool("web_search",
                  "用搜索引擎查资料（**免 Key**，走公开搜索结果页）。返回标题 / 链接 / 摘要，"
                  "想读全文再用 web_fetch 抓其中一条。"
                  "注意：结果来自搜索引擎的抓取，不保证时效与权威；被限流时会明确报错，"
                  "**不会**给你一个空结果假装「没有相关结果」。",
                  {"type": "object", "required": ["query"], "properties": {
                      "query": {"type": "string", "description": "搜索词（自然语言即可）"},
                      "count": {"type": "integer", "minimum": 1, "maximum": 10,
                                "description": "返回条数，默认 5"}}},
                  web_search, preview_keys=("query",)),
    ]


def _clip(s: str, limit: int) -> tuple:
    """头 + 尾，中间留省略标记（与 common.clip 同策略，这里不依赖 common 以免工具间耦合）。"""
    if len(s) <= limit:
        return s, False
    head = s[: int(limit * 0.7)]
    tail = s[-int(limit * 0.25):]
    return f"{head}\n…（中间省略 {len(s) - len(head) - len(tail)} 字符）…\n{tail}", True

