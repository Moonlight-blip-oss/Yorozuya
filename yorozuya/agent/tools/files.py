# -*- coding: utf-8 -*-
"""文件管理工具：copy_path / move_path / delete_path / make_dir / read_many / apply_patch

为什么要有这一组：原来只有 write_file + edit_file，于是「把 a.py 挪到 src/」这种事模型只能
**绕道 run_command**（`move` / `xcopy`）—— 那既需要审批、又会因 shell 方言（cmd 不认分号、
`move` 与 `mv` 参数不同）出各种怪问题，还绕过了「改动清单/写前快照」这条链：
内核的快照只认 `path` 参数，命令式移动**拍不到快照**，回滚就回不来。

三条纪律（与既有工具一致）：
· 所有路径过 `ws.resolve_inside`（路径 jail），越界直接抛 PermissionError。
· **删除一律移入回收站**（`zhiban-data/agent/trash/`），不做真删除 —— 既安全、又不依赖删除权限。
· 每一个写操作都回报 `paths` + `diffs`，内核据此记改动清单并拍快照（见 tools/base.py 的 paths()）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import common
from .base import ToolResult, read_tool, write_tool
from .edit import _unified

MAX_READ_MANY = 20
MAX_COPY_BYTES = 40 * 1024 * 1024          # 单次复制上限，避免把仓库塞成几十 G


# 「移入回收站」只有一份实现（在 snapshot.py 里）—— 这里直接引用，
# 免得两处各写一遍、行为慢慢分叉。
from ..snapshot import move_to_trash  # noqa: E402


def _size_of(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def build_file_tools(ws, output_limit: int):
    def _guard(rel: str) -> Path:
        p = ws.resolve_inside(rel)
        if ws.is_protected(ws.rel(p)):
            raise PermissionError(f"{rel} 在受保护目录内（.git / node_modules / .yorozuya…），拒绝操作")
        return p

    # ---------------- copy / move ----------------

    def copy_path(args: dict) -> ToolResult:
        src = _guard(args["src"])
        if not src.exists():
            return ToolResult(False, error=f"源不存在：{args['src']}")
        dst = ws.resolve_inside(args["dst"])
        if _is_inside(dst, src):
            return ToolResult(False, error="目标在源目录内部，会无限递归 —— 拒绝")
        if dst.exists():
            if not args.get("overwrite"):
                return ToolResult(False, error=f"目标已存在：{args['dst']}（要覆盖请传 overwrite=true）")
            if dst.is_dir():
                return ToolResult(False, error=f"目标是目录：{args['dst']}，不覆盖目录")
            dst.unlink()
        n = _size_of(src)
        if n > MAX_COPY_BYTES:
            return ToolResult(False, error=f"太大（{n} 字节 > {MAX_COPY_BYTES}），请用 run_command 处理")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        return ToolResult(True, summary=f"复制 {ws.rel(src)} → {ws.rel(dst)}",
                          output=f"{ws.rel(src)} → {ws.rel(dst)}"
                                 + ("（目录）" if src.is_dir() else f"（{n} 字节）"),
                          data={"paths": [ws.rel(dst)], "changed": True, "created": True})

    def move_path(args: dict) -> ToolResult:
        src = _guard(args["src"])
        if not src.exists():
            return ToolResult(False, error=f"源不存在：{args['src']}")
        dst = ws.resolve_inside(args["dst"])
        if _is_inside(dst, src):
            return ToolResult(False, error="目标在源目录内部 —— 拒绝")
        if dst.exists() and not args.get("overwrite"):
            return ToolResult(False, error=f"目标已存在：{args['dst']}（要覆盖请传 overwrite=true）")
        dst.parent.mkdir(parents=True, exist_ok=True)
        before = common.read_text(src) if (src.is_file() and src.stat().st_size < 400_000) else ""
        shutil.move(str(src), str(dst))
        # 移动 = 源没了 + 目标有了：两个路径都要进改动清单（回滚时才收得回来）
        diffs = [{"path": ws.rel(src), "diff": f"（已移走：{ws.rel(src)} → {ws.rel(dst)}）",
                  "created": False}]
        if before:
            diffs.append({"path": ws.rel(dst), "diff": _unified("", before, ws.rel(dst)),
                          "created": True})
        return ToolResult(True, summary=f"移动 {ws.rel(src)} → {ws.rel(dst)}",
                          output=f"{ws.rel(src)} → {ws.rel(dst)}",
                          data={"paths": [ws.rel(src), ws.rel(dst)], "changed": True, "diffs": diffs})

    # ---------------- delete ----------------

    def delete_path(args: dict) -> ToolResult:
        p = _guard(args["path"])
        if not p.exists():
            return ToolResult(False, error=f"不存在：{args['path']}")
        if p.resolve() == ws.root:
            return ToolResult(False, error="不能删除工作区根目录")
        n = _size_of(p)
        where = move_to_trash(p)
        if where is None:
            return ToolResult(False, error="移入回收站失败（未做真删除，文件还在原处）")
        return ToolResult(True,
                          summary=f"删除 {ws.rel(p)}（{n} 字节，已移入回收站）",
                          output=f"{ws.rel(p)} → 回收站 {where}\n"
                                 f"（本项目删除一律进回收站，可人工捞回；不做真删除）",
                          data={"paths": [ws.rel(p)], "changed": True, "trashed": str(where)})

    # ---------------- make_dir ----------------

    def make_dir(args: dict) -> ToolResult:
        p = _guard(args["path"])
        if p.exists():
            if p.is_dir():
                return ToolResult(True, summary=f"{ws.rel(p)} 已存在",
                                  output="（目录已存在，未改动）",
                                  data={"paths": [], "changed": False})
            return ToolResult(False, error=f"{args['path']} 已存在且是文件")
        p.mkdir(parents=True, exist_ok=True)
        return ToolResult(True, summary=f"新建目录 {ws.rel(p)}", output=ws.rel(p),
                          data={"paths": [ws.rel(p)], "changed": True, "created": True,
                                "diff": f"（新建目录 {ws.rel(p)}）"})

    # ---------------- read_many ----------------

    def read_many(args: dict) -> ToolResult:
        rels = [str(x) for x in (args.get("paths") or [])][:MAX_READ_MANY]
        if not rels:
            return ToolResult(False, error="paths 不能为空")
        per = int(args.get("max_chars_each") or 4000)
        per = max(200, min(per, 20000))
        blocks, missing, total = [], [], 0
        for rel in rels:
            try:
                p = ws.resolve_inside(rel)
            except PermissionError as e:
                blocks.append(f"### {rel}\n（跳过：{e}）")
                continue
            if not p.exists() or p.is_dir():
                missing.append(rel)
                continue
            text = common.read_text(p)
            seg = text[:per]
            total += len(seg)
            blocks.append(f"### {rel}（{len(text)} 字符"
                          + ("，已截断" if len(text) > per else "") + "）\n"
                          + "\n".join(f"{i + 1:>5}| {ln}" for i, ln in enumerate(seg.splitlines())))
        body, truncated = common.clip("\n\n".join(blocks) or "（没有读到任何内容）", output_limit)
        summary = f"读了 {len(rels) - len(missing)} 个文件（{total} 字符）"
        if missing:
            summary += f"，{len(missing)} 个不存在"
        return ToolResult(True, summary=summary, output=body,
                          data={"count": len(rels) - len(missing), "missing": missing},
                          truncated=truncated)

    # ---------------- apply_patch（一次多处修改，原子） ----------------

    def apply_patch(args: dict) -> ToolResult:
        """一次提交多个精确串替换；**要么全成，要么一个都不动**。

        为什么值得单独一个工具：连续调 4 次 edit_file 会在中途被预算/审批打断，
        留下"改了一半"的代码；而且每改一次就重新读一遍上下文，白烧 token。
        这里先把所有片段都校验一遍（存在性 + 唯一性），全过了才落盘。
        """
        edits = args.get("edits") or []
        if not isinstance(edits, list) or not edits:
            return ToolResult(False, error="edits 不能为空")
        if len(edits) > 40:
            return ToolResult(False, error="一次最多 40 个片段（多了不如拆成几次，出问题也好定位）")

        # ① 先全部校验（不落盘）
        plan: dict[str, str] = {}
        problems = []
        for i, e in enumerate(edits, 1):
            rel = str(e.get("path") or "")
            old = e.get("old_string") or ""
            new = e.get("new_string") or ""
            if not rel or not old:
                problems.append(f"第 {i} 段：path / old_string 不能为空")
                continue
            try:
                p = _guard(rel)
            except PermissionError as ex:
                problems.append(f"第 {i} 段：{ex}")
                continue
            if not p.is_file():
                problems.append(f"第 {i} 段：文件不存在 {rel}")
                continue
            cur = plan.get(ws.rel(p))
            if cur is None:
                cur = common.read_text(p)
            cnt = cur.count(old)
            if cnt == 0:
                problems.append(f"第 {i} 段：在 {rel} 里找不到 old_string"
                                f"（如果前一段刚改过它，注意要按**改后**的内容写）")
                continue
            if cnt > 1 and not e.get("replace_all"):
                problems.append(f"第 {i} 段：old_string 在 {rel} 中出现 {cnt} 次，不唯一")
                continue
            plan[ws.rel(p)] = cur.replace(old, new) if e.get("replace_all") else \
                cur.replace(old, new, 1)
        if problems:
            return ToolResult(False, error="没有落盘（先修好这些）：\n- " + "\n- ".join(problems[:10]))

        # ② 全过 → 落盘（逐文件只写一次）
        paths, diffs = [], []
        for rel, after in plan.items():
            p = ws.resolve_inside(rel)
            before = common.read_text(p)
            if before == after:
                continue
            p.write_text(after, encoding="utf-8", newline="\n")
            paths.append(rel)
            diffs.append({"path": rel, "diff": _unified(before, after, rel), "created": False})
        if not paths:
            return ToolResult(True, summary="内容没有变化（片段都是等价的）",
                              output="（所有片段应用后内容一致，未写盘）",
                              data={"paths": [], "changed": False})
        body, truncated = common.clip("\n\n".join(d["diff"] for d in diffs), output_limit)
        return ToolResult(True,
                          summary=f"改了 {len(paths)} 个文件（{len(edits)} 个片段，全部落盘）",
                          output=body,
                          data={"paths": paths, "changed": True, "diffs": diffs},
                          truncated=truncated)

    return [
        write_tool("copy_path", "复制文件或目录（目录会整棵复制）。",
                   {"type": "object", "required": ["src", "dst"], "properties": {
                       "src": {"type": "string", "description": "源路径（相对工作区根）"},
                       "dst": {"type": "string", "description": "目标路径"},
                       "overwrite": {"type": "boolean", "description": "目标已存在时是否覆盖（默认 false）"}}},
                   copy_path, preview_keys=("src", "dst"),
                   # src 也要进 paths()：它是**路径 jail** 与被复制内容的来源，
                   # 只看 dst 会让「把工作区外的文件复制进来」这种越界读漏过去。
                   paths_fn=lambda a: [a.get("src"), a.get("dst")]),
        write_tool("move_path", "移动 / 重命名文件或目录。",
                   {"type": "object", "required": ["src", "dst"], "properties": {
                       "src": {"type": "string"},
                       "dst": {"type": "string"},
                       "overwrite": {"type": "boolean"}}},
                   move_path, preview_keys=("src", "dst"),
                   paths_fn=lambda a: [a.get("src"), a.get("dst")]),
        write_tool("delete_path", "删除文件或目录（**移入回收站**，可人工捞回，不做真删除）。",
                   {"type": "object", "required": ["path"], "properties": {
                       "path": {"type": "string"}}},
                   delete_path, preview_keys=("path",)),
        write_tool("make_dir", "新建目录（父目录自动创建）。",
                   {"type": "object", "required": ["path"], "properties": {
                       "path": {"type": "string"}}},
                   make_dir, preview_keys=("path",)),
        read_tool("read_many", f"一次读多个文件（最多 {MAX_READ_MANY} 个，带行号）。"
                               f"想同时看几个相关文件时用它，比逐个 read_file 省一轮往返。",
                  {"type": "object", "required": ["paths"], "properties": {
                      "paths": {"type": "array", "items": {"type": "string"},
                                "description": "要读的相对路径列表"},
                      "max_chars_each": {"type": "integer", "minimum": 200, "maximum": 20000,
                                         "description": "每个文件最多读多少字符，默认 4000"}}},
                  read_many),
        write_tool("apply_patch", "一次多处精确修改（多个 old→new 片段）：**要么全成，要么一个都不动**。"
                                  "适合「同一个改动要落在好几个文件/好几处」的时候，比连续调 edit_file 更稳。",
                   {"type": "object", "required": ["edits"], "properties": {
                       "edits": {"type": "array", "description": "片段列表（最多 40 个）", "items": {
                           "type": "object", "required": ["path", "old_string", "new_string"],
                           "properties": {
                               "path": {"type": "string"},
                               "old_string": {"type": "string", "description": "原样（含缩进）"},
                               "new_string": {"type": "string"},
                               "replace_all": {"type": "boolean"}}}}}},
                   apply_patch, preview_keys=("edits",),
                   paths_fn=lambda a: [e.get("path") for e in (a.get("edits") or [])
                                       if isinstance(e, dict)]),
    ]
