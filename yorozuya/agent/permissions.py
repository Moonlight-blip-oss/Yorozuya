# -*- coding: utf-8 -*-
"""权限引擎：allow / ask / deny + 信任档位 + 路径 jail + 命令黑名单

默认安全（这一层是「敢用它」的前提）：
· 未配置即 ask；审批超时 = deny；deny 类**不可被「本会话记住」绕过**。
· ★ 信任档位只放宽两件事：**只读工具** 与 **工作区自己声明的验证命令**。
  写文件、执行任意命令**永远 ask** —— 成长感有了，风险不涨。
"""
from __future__ import annotations

import re

from . import common
from .workspace import Workspace

# 等级 → 档位（复用 Yorozuya 的 Lv.20 曲线，让陪伴的成长真正有用）
TIERS = [
    (1, 5, "stranger", "陌生", "全部要审批"),
    (6, 10, "familiar", "眼熟", "只读免审"),
    (11, 15, "friend", "熟人", "只读 + 工作区声明的验证命令免审"),
    (16, 20, "partner", "伙伴", "同上，且允许「本会话记住该命令」"),
]


def tier_of(level: int) -> dict:
    level = max(1, min(20, int(level or 1)))
    for lo, hi, key, name, desc in TIERS:
        if lo <= level <= hi:
            return {"key": key, "name": name, "desc": desc, "level": level}
    return {"key": "stranger", "name": "陌生", "desc": "", "level": level}


class SessionPolicy:
    """本会话内「记住的选择」（进程内，不落库）。"""

    def __init__(self):
        self.allow_tools: set = set()
        self.allow_prefixes: list = []
        self.deny_tools: set = set()

    def allow_tool(self, name: str) -> None:
        self.allow_tools.add(name)

    def allow_prefix(self, cmd: str) -> None:
        p = (cmd or "").strip()
        if p and p not in self.allow_prefixes:
            self.allow_prefixes.append(p)


class PermissionEngine:
    def __init__(self, ws: Workspace, level: int = 1, policy: SessionPolicy | None = None):
        self.ws = ws
        self.level = int(level or 1)
        self.policy = policy or SessionPolicy()
        self.tier = tier_of(self.level)

    # ---------------- 判定 ----------------

    def check(self, tool, args: dict) -> tuple:
        """返回 (verdict, 理由)。"""
        name = tool.name
        risk = tool.risk
        args = args or {}

        # ① 命令黑名单：先于一切（连审批都不问）
        if risk == common.RISK_EXEC:
            cmd = str(args.get("command") or "")
            bad = self.match_deny(cmd)
            if bad:
                return common.DENY, bad

        # ② 路径 jail：越界直接拒
        #    ★ 不能只看 args["path"]：copy_path/move_path/delete_path/apply_patch 用 src/dst/paths，
        #      只看 path 会让 jail 被绕过（例如把工作区外的文件 copy 进来、或 delete 掉外面东西）。
        #      统一走 tool.paths(args)（与写前快照同一个来源，避免两处口径不一致）。
        try:
            targets = tool.paths(args)
        except Exception:
            targets = []
        for val in targets:
            try:
                self.ws.resolve_inside(val)
            except PermissionError as e:
                return common.DENY, str(e)
        # 写入受保护目录（.git / node_modules / .yorozuya…）也直接拒
        if risk == common.RISK_WRITE and targets:
            for val in targets:
                try:
                    if self.ws.is_protected(self.ws.rel(self.ws.resolve_inside(val))):
                        return common.DENY, f"{val} 在受保护目录内，拒绝写入"
                except Exception:
                    pass

        # ③ 工作区显式策略优先
        ws_policy = (self.ws.cfg.policy or {}).get(name)
        if ws_policy in (common.ALLOW, common.ASK, common.DENY):
            return ws_policy, f"工作区策略指定 {name} = {ws_policy}"

        # ④ 会话记忆（deny 不可被绕过：上面已返回）
        if name in self.policy.deny_tools:
            return common.DENY, "本会话已拒绝该工具"
        if name in self.policy.allow_tools:
            return common.ALLOW, "本会话已允许该工具"

        # ⑤ 命令前缀白名单（工作区声明 + 会话记住）
        if risk == common.RISK_EXEC:
            cmd = str(args.get("command") or "").strip()
            for p in list(self.ws.cfg.allow_prefixes or []) + list(self.policy.allow_prefixes):
                if p and cmd.startswith(p):
                    return common.ALLOW, f"命中免审前缀「{p}」"

        # ⑥ 只读工具：看档位
        if risk == common.RISK_READ:
            if self.tier["key"] == "stranger":
                return common.ASK, "新工作区（Lv.1-5）：只读也先确认一次"
            return common.ALLOW, f"只读操作，{self.tier['name']}档免审"

        # ⑦ 写 / 执行：永远 ask（信任档位不放开）
        if risk == common.RISK_WRITE:
            return common.ASK, "写文件永远需要你确认"
        return common.ASK, "执行命令永远需要你确认"

    def match_deny(self, cmd: str) -> str:
        """命中黑名单则返回原因，否则空串。"""
        text = (cmd or "").strip()
        if not text:
            return ""
        for pat, why in common.DENY_COMMAND_PATTERNS:
            try:
                if re.search(pat, text, re.I):
                    return f"命令被黑名单拦截（{why}）"
            except re.error:
                continue
        for pat in self.ws.cfg.deny_patterns or []:
            try:
                if re.search(pat, text, re.I):
                    return f"命令被工作区黑名单拦截（{pat}）"
            except re.error:
                continue
        return ""

    def tier_table(self) -> list:
        return [{"level": f"Lv.{lo}-{hi}", "key": k, "name": n, "desc": d,
                 "current": (self.tier["key"] == k)} for lo, hi, k, n, d in TIERS]
