# -*- coding: utf-8 -*-
"""工作区的只读 git 概览（工作台「工作区」卡片用）

为什么单独写、而不是注册成内核工具：
· 这是**界面读取**（你点开工作区看一眼分支和脏文件数），不是 agent 的动作，
  所以不该走审批 —— 保持只读、只跑 `git status/log` 这两条固定命令，不接受模型传参。
· 内核级的 `git_status / git_diff / git_log` 工具（计划书 §4.3 里那三个）仍未注册，
  这一点如实写在这里（属已知边界），不假装已经做了。
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(root: Path, args: list, timeout: float = 5.0) -> tuple:
    try:
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "没找到 git 命令"
    except subprocess.TimeoutExpired:
        return 124, "", "git 命令超时"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def git_info(root) -> dict:
    root = Path(root)
    out = {"isRepo": False, "branch": "", "dirty": 0, "staged": 0, "untracked": 0,
           "lastCommit": "", "lastCommitAt": "", "error": ""}
    if not root.is_dir():
        out["error"] = "目录不存在"
        return out
    code, _, err = _run(root, ["rev-parse", "--show-toplevel"])
    if code != 0:
        out["error"] = err or "不是 git 仓库"
        return out
    out["isRepo"] = True
    _, branch, _ = _run(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    out["branch"] = branch or "(detached)"
    _, porcelain, _ = _run(root, ["status", "--porcelain"])
    for line in (porcelain or "").splitlines():
        if not line.strip():
            continue
        if line.startswith("??"):
            out["untracked"] += 1
        elif line[0] not in (" ", "?"):
            out["staged"] += 1
        else:
            out["dirty"] += 1
    _, last, _ = _run(root, ["log", "-1", "--pretty=%h %s"])
    out["lastCommit"] = last[:200]
    _, when, _ = _run(root, ["log", "-1", "--pretty=%cI"])
    out["lastCommitAt"] = when
    return out
