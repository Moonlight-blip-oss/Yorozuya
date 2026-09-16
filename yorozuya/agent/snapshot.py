# -*- coding: utf-8 -*-
"""快照与回滚：内容寻址（同一个内容只存一份）+ 一键 undo

为什么必须有它：git 只管「已提交的历史」，而 agent 干活时大量改动是**未提交**的中间态；
用户说「撤回」，撤的是那一次运行，不是回退一个 commit。

两条工程约定：
· 写操作**之前**统一由内核 capture（不放在工具里，免得有人漏）。
· 删除一律**移入回收站**（`zhiban-data/agent/trash/`），不做真删除：
  既安全（可人工捞回），又不依赖删除权限（受限环境下删除常被拦）。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import common, storage

TRASH_DIR = common.AGENT_DATA / "trash"


def move_to_trash(path: Path) -> Path | None:
    """把文件/目录**移入回收站**（返回落点），失败返回 None。

    为什么全项目都用"移"而不是"删"：
    · 语义安全 —— 用户说"删掉"，agent 不该做不可逆的事，捞回来比后悔强；
    · 工程上还绕开一类环境限制 —— 某些受限环境（沙箱/企业策略）会**拦截真删除**
      （实测：沙箱里 `shutil.rmtree` 直接抛 SAFE_DELETE_FAIL_CLOSED），而"移动"不受影响。
    唯一一份实现，`SnapshotStore._trash` 与工具层的 delete_path 都走它。
    """
    try:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        dst = TRASH_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.name}"
        n = 1
        while dst.exists():
            dst = dst.with_name(f"{dst.name}.{n}")
            n += 1
        shutil.move(str(path), str(dst))
        return dst
    except Exception:
        return None


class SnapshotStore:
    def __init__(self, conn, blobs_dir=None):
        self.conn = conn
        self.blobs = Path(blobs_dir or common.BLOBS_DIR)
        self.blobs.mkdir(parents=True, exist_ok=True)

    # ---------------- 存 ----------------

    def _put_blob(self, data: bytes) -> str:
        h = common.sha256_bytes(data)
        p = self.blobs / h[:2] / h
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return h

    def blob_path(self, h: str) -> Path:
        return self.blobs / h[:2] / h

    def capture(self, run_id: str, step: int, ws, rels, label: str = "") -> dict:
        """记录这些文件「改动前」的样子。返回 files 记录（同时落库）。"""
        files = {}
        for rel in rels:
            rel = str(rel)
            if rel in files:
                continue
            try:
                p = ws.resolve_inside(rel)
            except Exception:
                continue
            if p.exists() and p.is_file():
                data = p.read_bytes()
                files[rel] = {"hash": self._put_blob(data), "existed": True, "size": len(data)}
            else:
                files[rel] = {"existed": False}
        if files:
            storage.add_checkpoint(self.conn, run_id, step, label or "auto", files)
        return files

    # ---------------- 还原 ----------------

    def _restore_one(self, ws, rel: str, rec: dict) -> str:
        p = ws.resolve_inside(rel)
        if rec.get("existed"):
            data = self.blob_path(rec["hash"]).read_bytes()
            if p.exists() and p.is_file() and common.sha256_file(p) == rec["hash"]:
                return "same"                       # 已经是目标内容，不用写
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.is_dir():
                self._trash(p)
            p.write_bytes(data)
            return "restored"
        if p.exists():
            self._trash(p)                          # 本次新建的 → 移入回收站
            self._prune_empty(p.parent, ws)
            return "removed"
        return "same"

    def _trash(self, path: Path) -> None:
        if move_to_trash(path) is None:
            # 连移都失败（极少数情况）：**不真删**，留在原处比误删强
            pass

    def _prune_empty(self, d: Path, ws) -> None:
        """清掉因回滚而空掉的目录（工作区根不删）。"""
        try:
            cur = d
            while cur != ws.root and ws._is_inside(cur):
                if not any(cur.iterdir()):
                    cur.rmdir()
                    cur = cur.parent
                else:
                    break
        except Exception:
            pass

    def undo_run(self, ws, run_id: str) -> dict:
        """把工作区恢复到这次运行**之前**的状态。"""
        cks = storage.list_checkpoints(self.conn, run_id)
        out = {"restored": [], "removed": [], "errors": [], "files": 0}
        seen = set()
        for ck in reversed(cks):                    # 倒序：最早的检查点最后应用 = 运行前状态
            try:
                files = json.loads(ck["files"] or "{}")
            except Exception:
                continue
            for rel, rec in files.items():
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    act = self._restore_one(ws, rel, rec)
                    if act == "restored":
                        out["restored"].append(rel)
                    elif act == "removed":
                        out["removed"].append(rel)
                except Exception as e:
                    out["errors"].append(f"{rel}: {type(e).__name__}: {e}")
        out["files"] = len(seen)
        return out

    def undo_files(self, ws, run_id: str, paths: list[str]) -> dict:
        """仅还原本次运行触及的指定文件。

        逐文件审阅不能拿 ``undo_run`` 再让前端过滤结果：那会在用户拒绝一个
        文件时顺手撤掉同一轮的所有改动。这里仍从最早的快照恢复，因此语义和
        整轮回滚一致；本次新建文件照旧移入回收站。
        """
        wanted = {str(p) for p in (paths or []) if str(p)}
        out = {"restored": [], "removed": [], "errors": [], "files": 0}
        if not wanted:
            return out
        seen = set()
        for ck in reversed(storage.list_checkpoints(self.conn, run_id)):
            try:
                files = json.loads(ck["files"] or "{}")
            except Exception:
                continue
            for rel, rec in files.items():
                if rel not in wanted or rel in seen:
                    continue
                seen.add(rel)
                try:
                    act = self._restore_one(ws, rel, rec)
                    if act == "restored":
                        out["restored"].append(rel)
                    elif act == "removed":
                        out["removed"].append(rel)
                except Exception as e:
                    out["errors"].append(f"{rel}: {type(e).__name__}: {e}")
        out["files"] = len(seen)
        return out
