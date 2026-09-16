# -*- coding: utf-8 -*-
"""写工具：write_file / edit_file

★ 只做「唯一匹配的精确串替换」：
  本项目曾用偏移量批量改 HTML，把文件改坏过 —— 行号/偏移式补丁是同一类风险。
  宁可「改不动、让模型重试」，也不要「改错地方」。
"""
from __future__ import annotations

import difflib

from .. import common
from .base import ToolResult, write_tool


def _unified(before: str, after: str, rel: str) -> str:
    if before == after:
        return ""
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                fromfile="a/" + rel, tofile="b/" + rel, lineterm="", n=3)
    return "\n".join(list(diff)[:400])


def build_write_tools(ws, output_limit: int, on_verify=None):
    def write_file(args: dict) -> ToolResult:
        rel = args["path"]
        p = ws.resolve_inside(rel)
        if p.exists() and p.is_dir():
            return ToolResult(False, error=f"{rel} 是目录，不能写入")
        if ws.is_protected(ws.rel(p)):
            return ToolResult(False, error=f"{rel} 位于受保护目录（如 .git / node_modules），拒绝写入")
        content = args.get("content") or ""
        existed = p.exists()
        before = common.read_text(p) if existed else ""
        if existed and before == content:
            return ToolResult(True, summary=f"{rel} 内容未变化", output="（内容完全相同，已跳过）",
                              data={"path": ws.rel(p), "changed": False})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        diff = _unified(before, content, ws.rel(p))
        body, truncated = common.clip(diff or f"（新建 {ws.rel(p)}，{len(content)} 字符）", output_limit)
        return ToolResult(True,
                          summary=("新建" if not existed else "覆盖") + f" {ws.rel(p)}（{len(content)} 字符）",
                          output=body,
                          data={"path": ws.rel(p), "changed": True, "created": not existed,
                                "diff": diff, "hash": common.sha256_file(p)}, truncated=truncated)

    def edit_file(args: dict) -> ToolResult:
        rel = args["path"]
        p = ws.resolve_inside(rel)
        if not p.exists() or not p.is_file():
            return ToolResult(False, error=f"文件不存在：{rel}")
        if ws.is_protected(ws.rel(p)):
            return ToolResult(False, error=f"{rel} 位于受保护目录，拒绝修改")
        old, new = args["old_string"], args.get("new_string") or ""
        if old == new:
            return ToolResult(False, error="old_string 与 new_string 相同，没有意义")
        before = common.read_text(p)
        cnt = before.count(old)
        if cnt == 0:
            return ToolResult(False, error=f"在 {rel} 里找不到 old_string（要注意缩进与换行必须完全一致）")
        if cnt > 1 and not args.get("replace_all"):
            return ToolResult(False, error=f"old_string 在 {rel} 中出现 {cnt} 次，不唯一。"
                                           f"请带上更多上下文让它唯一，或显式传 replace_all=true")
        after = before.replace(old, new) if args.get("replace_all") else before.replace(old, new, 1)
        p.write_text(after, encoding="utf-8", newline="\n")
        diff = _unified(before, after, ws.rel(p))
        body, truncated = common.clip(diff, output_limit)
        return ToolResult(True, summary=f"修改 {ws.rel(p)}（{cnt if args.get('replace_all') else 1} 处）",
                          output=body,
                          data={"path": ws.rel(p), "changed": True, "diff": diff,
                                "hash": common.sha256_file(p)}, truncated=truncated)

    return [
        write_tool("write_file", "新建或整体覆盖一个文件（目录会自动创建）。",
                   {"type": "object", "required": ["path", "content"], "properties": {
                       "path": {"type": "string", "description": "相对工作区根的路径"},
                       "content": {"type": "string", "description": "完整文件内容"}}},
                   write_file),
        write_tool("edit_file", "对文件做**精确串替换**（old_string 必须唯一匹配）。",
                   {"type": "object", "required": ["path", "old_string", "new_string"], "properties": {
                       "path": {"type": "string"},
                       "old_string": {"type": "string", "description": "要被替换的原文（含缩进，必须唯一）"},
                       "new_string": {"type": "string", "description": "替换成什么"},
                       "replace_all": {"type": "boolean", "description": "是否替换全部（默认 false）"}}},
                   edit_file),
    ]
