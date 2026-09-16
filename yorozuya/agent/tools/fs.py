# -*- coding: utf-8 -*-
"""只读工具：read_file / list_dir / glob / grep

grep 用「发现式复用 ripgrep」：优先项目内 vendor/rg.exe → 环境变量 AGENT_RG → PATH →
本机常见开发工具位置（IDE 自带）。找不到就退回纯 Python —— 功能一样，只是慢。
（不把 5MB 二进制塞进仓库：升级维护麻烦；想自带就把 rg.exe 放进 agent-service 同级 vendor/。）
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess

from .. import common
from .base import ToolResult, read_tool

MAX_MATCH = 200

_RG_CACHE: list = []
# 扫描时只进这些目录名（避免在用户目录里乱翻；带时间预算兜底）
_RG_DIR_HINTS = {"resources", "app.asar.unpacked", "cli", "vendor", "ripgrep", "x64-win32",
                 "bin", "@vscode", "node_modules", "Programs", "Application"}


def _scan_rg(root: str, budget: float = 3.0) -> str:
    """在 root 下有界地找 rg.exe（深度 ≤ 8，只进白名单目录名，超预算就放弃）。"""
    import time as _t
    t0 = _t.time()
    for dirpath, dirnames, filenames in os.walk(root):
        if _t.time() - t0 > budget:
            return ""
        depth = dirpath[len(root):].count(os.sep)
        if depth >= 8:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d in _RG_DIR_HINTS]
        for fn in filenames:
            if fn.lower() == "rg.exe":
                return os.path.join(dirpath, fn)
    return ""


def find_rg() -> str:
    """找一个可用的 rg.exe（缓存结果）。发现式复用，不往仓库里塞二进制。"""
    if _RG_CACHE:
        return _RG_CACHE[0]
    cands = []
    env = os.environ.get("AGENT_RG")
    if env:
        cands.append(env)
    cands.append(str(common.BASE_DIR / "vendor" / ("rg.exe" if os.name == "nt" else "rg")))
    which = shutil.which("rg")
    if which:
        cands.append(which)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        # ① 已知的相对位置（IDE / 编辑器 / 工具自带的）
        for base in (local, r"C:\Program Files", r"C:\Program Files (x86)"):
            if base:
                cands.append(os.path.join(base, "Programs", "Microsoft VS Code", "resources", "app",
                                          "node_modules", "@vscode", "ripgrep", "bin", "rg.exe"))
                cands.append(os.path.join(base, "Microsoft VS Code", "resources", "app",
                                          "node_modules", "@vscode", "ripgrep", "bin", "rg.exe"))
        # ② 有界扫描：常见宿主目录（含本项目所属的工具链目录）
        for root in (r"D:\workbuddy", os.path.join(local, "Programs"), r"C:\Program Files"):
            if os.path.isdir(root):
                hit = _scan_rg(root)
                if hit:
                    cands.insert(0, hit)
    for c in cands:
        try:
            if c and os.path.isfile(c):
                _RG_CACHE.append(c)
                return c
        except Exception:
            continue
    _RG_CACHE.append("")
    return ""


def backend_name() -> str:
    rg = find_rg()
    return f"ripgrep（{rg}）" if rg else "python（未发现 ripgrep，把 rg.exe 放进 zhiban-py/vendor/ 可提速）"


def _glob_match(rel: str, pattern: str) -> bool:
    if not pattern or pattern == "*":
        return True
    rel = rel.replace("\\", "/")
    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern):
        return True
    if "**" in pattern:
        rx = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
        rx = rx.replace(r"\*", "[^/]*").replace(r"\?", ".")
        return re.fullmatch(rx, rel) is not None
    return False


def build_read_tools(ws, output_limit: int):
    def read_file(args: dict) -> ToolResult:
        p = ws.resolve_inside(args["path"])
        if not p.exists():
            return ToolResult(False, error=f"文件不存在：{args['path']}")
        if p.is_dir():
            return ToolResult(False, error=f"{args['path']} 是目录，请用 list_dir")
        if p.stat().st_size > 2_000_000:
            return ToolResult(False, error=f"文件过大（{p.stat().st_size} 字节），请用 grep 定位后再读片段")
        text = common.read_text(p)
        lines = text.splitlines()
        start = max(1, int(args.get("start_line") or 1))
        end = int(args.get("end_line") or 0) or len(lines)
        end = min(end, len(lines))
        seg = lines[start - 1:end]
        body = "\n".join(f"{i + start:>5}| {ln}" for i, ln in enumerate(seg))
        body, truncated = common.clip(body, output_limit)
        return ToolResult(True, summary=f"{ws.rel(p)} 第 {start}-{end} 行（共 {len(lines)} 行）",
                          output=body, data={"path": ws.rel(p), "lines": len(lines)},
                          truncated=truncated)

    def list_dir(args: dict) -> ToolResult:
        base = ws.resolve_inside(args.get("path") or ".")
        depth = max(1, min(int(args.get("depth") or 2), 4))
        rows = []
        for dirpath, dirnames, filenames in os.walk(base):
            d = Path_rel(base, dirpath, ws)
            level = 0 if dirpath == str(base) else d.count("/") + (1 if base != ws.root else 0)
            if level >= depth:
                dirnames[:] = []
            dirnames[:] = [x for x in dirnames if x not in common.PROTECTED_DIRS]
            indent = "  " * level
            rows.append(f"{indent}{os.path.basename(dirpath)}/")
            for fn in sorted(filenames)[:60]:
                rows.append(f"{indent}  {fn}")
            if len(rows) > 400:
                break
        body, truncated = common.clip("\n".join(rows) or "（空目录）", output_limit)
        return ToolResult(True, summary=f"{ws.rel(base)} 下列出 {len(rows)} 项",
                          output=body, data={"path": ws.rel(base), "count": len(rows)},
                          truncated=truncated)

    def glob_files(args: dict) -> ToolResult:
        pattern = args["pattern"]
        limit = min(int(args.get("limit") or 200), 2000)
        hits = []
        for p in ws.iter_files():
            rel = ws.rel(p)
            if _glob_match(rel, pattern):
                hits.append(rel)
                if len(hits) >= limit:
                    break
        body, truncated = common.clip("\n".join(hits) or "（没有匹配）", output_limit)
        return ToolResult(True, summary=f"匹配 {len(hits)} 个文件", output=body,
                          data={"count": len(hits), "files": hits[:200]}, truncated=truncated)

    def grep(args: dict) -> ToolResult:
        pattern = args["pattern"]
        file_glob = args.get("file_glob") or "*"
        ignore_case = bool(args.get("ignore_case", False))
        ctxn = max(0, min(int(args.get("context_lines") or 0), 3))
        limit = min(int(args.get("limit") or MAX_MATCH), 400)
        rg = find_rg()
        if rg:
            cmd = [rg, "--line-number", "--no-heading", "--color", "never", "--max-columns", "300",
                   "--max-count", str(limit), "-e", pattern]
            if ignore_case:
                cmd.append("-i")
            if ctxn:
                cmd += ["-C", str(ctxn)]
            if file_glob and file_glob != "*":
                cmd += ["--glob", file_glob]
            cmd += ["."]
            try:
                r = subprocess.run(cmd, cwd=str(ws.root), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60)
                out = (r.stdout or "").strip()
                body, truncated = common.clip(out or "（没有匹配）", output_limit)
                n = len([x for x in out.splitlines() if x.strip()]) if out else 0
                return ToolResult(True, summary=f"命中 {n} 行（ripgrep）", output=body,
                                  data={"count": n, "backend": "ripgrep"}, truncated=truncated)
            except Exception:
                pass                                   # rg 出问题就退回 python，不让任务中断
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            return ToolResult(False, error=f"正则不合法：{e}")
        out, count = [], 0
        for p in ws.iter_files():
            rel = ws.rel(p)
            if not _glob_match(rel, file_glob):
                continue
            try:
                lines = common.read_text(p).splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lines):
                if rx.search(ln):
                    count += 1
                    lo, hi = max(0, i - ctxn), min(len(lines), i + ctxn + 1)
                    for j in range(lo, hi):
                        mark = ":" if j == i else "-"
                        out.append(f"{rel}{mark}{j + 1}{mark} {ln.strip()}")
                    if count >= limit:
                        break
            if count >= limit:
                break
        body, truncated = common.clip("\n".join(out) or "（没有匹配）", output_limit)
        return ToolResult(True, summary=f"命中 {count} 处（python 后端）", output=body,
                          data={"count": count, "backend": "python"}, truncated=truncated)

    return [
        read_tool("read_file", "读取工作区内的文本文件（带行号，可指定行范围）。",
                  {"type": "object", "required": ["path"], "properties": {
                      "path": {"type": "string", "description": "相对工作区根的路径"},
                      "start_line": {"type": "integer", "description": "起始行（含），默认 1"},
                      "end_line": {"type": "integer", "description": "结束行（含），默认到文件末尾"}}},
                  read_file),
        read_tool("list_dir", "列目录（树状，最多 4 层）。",
                  {"type": "object", "properties": {
                      "path": {"type": "string", "description": "相对路径，默认 ."},
                      "depth": {"type": "integer", "minimum": 1, "maximum": 4}}},
                  list_dir),
        read_tool("glob", "按文件名通配查找（支持 **）。",
                  {"type": "object", "required": ["pattern"], "properties": {
                      "pattern": {"type": "string", "description": "如 **/*.py"},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 2000}}},
                  glob_files),
        read_tool("grep", "按内容搜索（正则），返回文件:行号:内容。",
                  {"type": "object", "required": ["pattern"], "properties": {
                      "pattern": {"type": "string", "description": "正则表达式"},
                      "file_glob": {"type": "string", "description": "限定文件名，如 *.py"},
                      "ignore_case": {"type": "boolean"},
                      "context_lines": {"type": "integer", "minimum": 0, "maximum": 3},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 400}}},
                  grep),
    ]


def Path_rel(base, dirpath, ws) -> str:                     # noqa: N802（局部小工具）
    try:
        return os.path.relpath(dirpath, str(ws.root)).replace("\\", "/")
    except Exception:
        return "."
