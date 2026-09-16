# -*- coding: utf-8 -*-
"""Git 工具：git_status / git_diff / git_log（只读）+ git_commit（写，需审批）

为什么要有这一组：内核**永远不会 push**（黑名单里挡着），但"看懂工作区现在什么状态"是干活的前提 ——
原来模型只能跑 `git status`，那条路要过命令方言（cmd 不认分号、`2>&1` 位置不同），
而且输出要靠它自己解析。这一个工具组把三件事做成结构化的：

· 只读三个：status（分支 / 脏文件 / 暂存 / 未跟踪）、diff（含 --stat）、log（短哈希 + 主题）
· 写一个：commit —— **必须显式给出要提交的文件**（或显式 all=true），绝不 `git add -A` 式地一把梭；
  永不 push；commit message 必填且不能是空话。

实现细节：一律用 `subprocess.run([...], cwd=工作区)` 传参数列表（**不经 shell**），
所以不受 cmd/PowerShell 方言影响，也没有注入面。
"""
from __future__ import annotations

import subprocess

from .base import ToolResult, read_tool, write_tool

TIMEOUT = 30
MAX_OUT = 4000


def _run(ws, args: list) -> tuple:
    """返回 (ok, 输出文本, 退出码)。git 不在 / 不是仓库都当"正常失败"如实回报，不抛异常。"""
    try:
        r = subprocess.run(["git"] + args, cwd=str(ws.root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT,
                           creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW")
                                          else 0))
    except FileNotFoundError:
        return False, "找不到 git（这台机器没装 git 或不在 PATH 上）", -1
    except subprocess.TimeoutExpired:
        return False, f"git 超过 {TIMEOUT}s 没返回，已放弃", -1
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", -1
    # ⚠️ 不要把空输出替换成占位文本：调用方会拿返回值做判断（例如"暂存区是不是空的"），
    #   占位文本会让空判断失效，最后表现为"莫名其妙提交失败"。要显示的地方由调用方自己补。
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, out, r.returncode


def _is_repo(ws) -> tuple:
    ok, out, _ = _run(ws, ["rev-parse", "--is-inside-work-tree"])
    return ok, out


def build_git_tools(ws):
    def git_status(args: dict) -> ToolResult:
        ok, out = _is_repo(ws)
        if not ok:
            return ToolResult(True, summary="不是 git 仓库", output=out,
                              data={"isRepo": False})
        ok2, out2, _ = _run(ws, ["status", "--porcelain=v1", "-b"])
        if not ok2:
            return ToolResult(False, error=out2)
        lines = out2.splitlines()
        branch = lines[0][3:].split("...")[0] if lines and lines[0].startswith("## ") else ""
        staged = [l for l in lines[1:] if l[:1] not in (" ", "?")]
        dirty = [l for l in lines[1:] if l[1:2] not in (" ",)]
        untracked = [l for l in lines[1:] if l.startswith("??")]
        # 只读的「摘要」不进改动清单：这个工具不会碰任何文件
        return ToolResult(True,
                          summary=f"分支 {branch or '?'}：改动 {len(dirty)}｜暂存 {len(staged)}"
                                  f"｜未跟踪 {len(untracked)}",
                          output=out2[:MAX_OUT],
                          data={"isRepo": True, "branch": branch, "dirty": len(dirty),
                                "staged": len(staged), "untracked": len(untracked)})

    def git_diff(args: dict) -> ToolResult:
        ok, out = _is_repo(ws)
        if not ok:
            return ToolResult(True, summary="不是 git 仓库", output=out, data={"isRepo": False})
        cmd = ["diff", "--no-color"]
        if args.get("staged"):
            cmd.append("--cached")
        if args.get("stat"):
            cmd.append("--stat")
        path = str(args.get("path") or "").strip()
        if path:
            try:
                path = ws.rel(ws.resolve_inside(path))
            except Exception as e:                            # noqa: BLE001
                return ToolResult(False, error=str(e))
            cmd += ["--", path]
        ok2, out2, _ = _run(ws, cmd)
        if not ok2:
            return ToolResult(False, error=out2)
        body = out2[:MAX_OUT * 2] or "（没有改动）"
        if args.get("stat"):
            n_files = len([x for x in body.splitlines() if "|" in x])
            return ToolResult(True, summary=f"改动统计：{n_files} 个文件"
                                             + (f"（{path}）" if path else ""),
                              output=body, truncated=len(out2) > len(body),
                              data={"files": n_files, "path": path})
        n = body.count("\n+") + body.count("\n-")
        return ToolResult(True, summary=f"diff {n} 处增删行" + (f"（{path}）" if path else ""),
                          output=body, truncated=len(out2) > len(body),
                          data={"lines": n, "path": path})

    def git_log(args: dict) -> ToolResult:
        ok, out = _is_repo(ws)
        if not ok:
            return ToolResult(True, summary="不是 git 仓库", output=out, data={"isRepo": False})
        n = int(args.get("limit") or 10)
        n = max(1, min(n, 50))
        ok2, out2, _ = _run(ws, ["log", f"-{n}", "--pretty=format:%h %ad %an: %s",
                                 "--date=format:%Y-%m-%d"])
        if not ok2:
            # 空仓库（还没有 commit）不算错
            if "does not have any commits" in out2 or "unknown revision" in out2:
                return ToolResult(True, summary="还没有任何提交", output=out2[:MAX_OUT],
                                  data={"commits": 0})
            return ToolResult(False, error=out2)
        rows = out2.splitlines()
        return ToolResult(True, summary=f"最近 {len(rows)} 次提交", output=out2[:MAX_OUT],
                          data={"commits": len(rows)})

    def git_commit(args: dict) -> ToolResult:
        msg = str(args.get("message") or "").strip()
        if len(msg) < 3:
            return ToolResult(False, error="commit message 太短（说清这次改了什么，至少几个字）")
        ok, out = _is_repo(ws)
        if not ok:
            return ToolResult(False, error=f"不是 git 仓库，无法提交：{out}")
        files = [str(x) for x in (args.get("files") or []) if str(x).strip()]
        add_all = bool(args.get("all"))
        if not files and not add_all:
            return ToolResult(False, error="要说清提交哪些文件（files 列表），或显式 all=true 提交全部改动。"
                                           "内核不会替你 `git add -A` 一把梭。")
        # ① 先 add（只 add 你点名的那些）
        if add_all:
            ok1, out1, _ = _run(ws, ["add", "-A"])
        else:
            ok1, out1, _ = _run(ws, ["add", "--"] + files)
        if not ok1:
            return ToolResult(False, error=f"git add 失败：{out1}")
        # ② 看看暂存区到底有没有东西（避免"空提交"这种假成功）
        ok2, staged, _ = _run(ws, ["diff", "--cached", "--name-only"])
        if ok2 and not staged.strip():
            return ToolResult(False, error="暂存区是空的（这些文件没有实际改动），没有可提交的内容")
        # ③ 提交：**照常跑项目的 pre-commit hooks**（钩子是项目自己的质量闸门，
        #    agent 不该用 --no-verify 绕过去；钩子失败就如实把报错交回模型）。
        ok3, out3, _ = _run(ws, ["commit", "-m", msg])
        if not ok3:
            return ToolResult(False, error=f"git commit 失败：{out3[:1200]}")
        _, head, _ = _run(ws, ["log", "-1", "--pretty=format:%h %s"])
        return ToolResult(True, summary=f"已提交：{head[:80]}",
                          output=f"{out3[:800]}\n\nHEAD: {head}",
                          data={"paths": [], "changed": False, "head": head,
                                "files": staged.splitlines()})

    return [
        read_tool("git_status", "看工作区的 git 状态（分支 / 改动 / 暂存 / 未跟踪）。不是仓库会如实说明。",
                  {"type": "object", "properties": {}}, git_status),
        read_tool("git_diff", "看未提交的改动（默认工作树 vs 暂存区；可只看某个文件、可只给 --stat）。",
                  {"type": "object", "properties": {
                      "path": {"type": "string", "description": "只看这个文件/目录（可选）"},
                      "staged": {"type": "boolean", "description": "看已暂存的改动（--cached）"},
                      "stat": {"type": "boolean", "description": "只给统计（--stat），不看正文"}}},
                  git_diff),
        read_tool("git_log", "看最近的提交（短哈希 + 日期 + 作者 + 主题）。",
                  {"type": "object", "properties": {
                      "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
                  git_log),
        write_tool("git_commit", "提交改动（**需你审批**）。必须显式给 files 列表，或 all=true；"
                                 "永不 push；message 要说清改了什么。",
                   {"type": "object", "required": ["message"], "properties": {
                       "message": {"type": "string", "description": "提交信息（单行，说清改了什么）"},
                       "files": {"type": "array", "items": {"type": "string"},
                                 "description": "要提交的文件（相对路径）；空着就必须 all=true"},
                       "all": {"type": "boolean",
                               "description": "true = 提交全部改动（等价 git add -A），默认 false"}}},
                   git_commit, preview_keys=("message", "files", "all"),
                   paths_fn=lambda a: a.get("files") or []),
    ]
