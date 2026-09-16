# -*- coding: utf-8 -*-
"""★ 需求 2 / 3 / 5：PersonaRouter —— 人格路由（日常聊天 vs 复杂任务）

四个人的定位差异靠这一层落地：
  · simple  → 保持当前人格（银时 / 神乐 / 新八 陪聊）
  · complex → 交给小玉（技术担当）：
        mode="ask"  先由当前人格**按角色口吻推脱**，UI 给一键切换（需求 3 的交互）
        mode="auto" 直接切换给小玉并作答（需求 2 的「自动转交」）

推脱话术有两道保险：
  ① 人格数据里的 `deflect` 例句（personas.py，随角色性格）；
  ② 模型生成后**必须过校验**（不说长答案、不含代码、不得罪角色、正确指向小玉），不过就退回例句。
     这样「银时不会硬答代码题」是代码保证的，不是靠提示词祈祷。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import classifier

TASK_PERSONA = "tama"                    # 技术担当
ACTION_KEEP = "keep"
ACTION_DEFLECT = "deflect"
ACTION_AUTO_SWITCH = "auto_switch"

# 最终兜底（personas 数据读不到时用；也保证「三个人格都会推脱」这件事不依赖数据完整性）
FALLBACK_DEFLECT = {
    "gintoki": "这种麻烦事，去找小玉吧，我回去躺着了。",
    "kagura": "阿鲁，这种烧脑的东西不适合我，去找小玉阿鲁！",
    "shinpachi": "这种技术问题……还是交给小玉比较专业。",
    "tama": "主人，这个请交给我吧。",
}
FALLBACK_NAMES = {"gintoki": "银时", "kagura": "神乐", "shinpachi": "新八", "tama": "小玉"}

MAX_DEFLECT_CHARS = 60                   # 推脱就该短


@dataclass
class Decision:
    level: str
    action: str
    from_pid: str
    to_pid: str = ""
    score: int = 0
    reasons: list = field(default_factory=list)
    line: str = ""                       # 推脱话术（action=deflect 时）
    intro: str = ""                      # 小玉接手时的开场（action=auto_switch 时）
    mode: str = "ask"

    def to_dict(self) -> dict:
        return {"level": self.level, "action": self.action, "fromPid": self.from_pid,
                "toPid": self.to_pid, "suggestPid": self.to_pid if self.action == ACTION_DEFLECT else "",
                "score": self.score, "reasons": self.reasons,
                "line": self.line, "intro": self.intro, "mode": self.mode}

    def notice(self) -> str:
        if self.action == ACTION_DEFLECT:
            return f"检测到复杂任务（{'、'.join(self.reasons[:2])}）→ 建议交给小玉"
        if self.action == ACTION_AUTO_SWITCH:
            return f"检测到复杂任务（{'、'.join(self.reasons[:2])}）→ 已自动转交小玉"
        return ""


# ---------------- 人格数据读取（惰性、可降级） ----------------

def persona_data(pid: str) -> dict:
    try:
        from ..personas import PERSONAS          # noqa: WPS433（惰性导入：内核可独立测试）
        return PERSONAS.get(pid) or {}
    except Exception:
        return {}


def persona_name(pid: str) -> str:
    return persona_data(pid).get("name") or FALLBACK_NAMES.get(pid, pid)


def deflect_line(pid: str, avoid: str = "") -> str:
    """取一句符合角色性格的推脱话术。"""
    lines = [x for x in (persona_data(pid).get("deflect") or []) if x]
    pool = [x for x in lines if x != avoid] or lines
    if not pool:
        return FALLBACK_DEFLECT.get(pid, FALLBACK_DEFLECT["gintoki"])
    return random.choice(pool)


def validate_deflection(text: str, pid: str) -> tuple:
    """校验一句「推脱」是否合格：(ok, 原因)。

    合格 = 短、不夹答案、不夹代码、提到小玉、没有别的角色的口癖、不骂人。
    不合格就退回人格数据里的例句 —— 这是「银时不硬答代码题」的代码保证。
    """
    t = (text or "").strip()
    if not t:
        return False, "空回复"
    if len(t) > MAX_DEFLECT_CHARS:
        return False, f"太长（{len(t)} 字），推脱应该一两句"
    if re.search(r"```|^(def |class |function |SELECT |import )", t, re.M):
        return False, "夹带了代码"
    if not re.search(r"小玉|技术担当|技术", t):
        return False, "没把话头指向小玉"
    p = persona_data(pid)
    for bad in p.get("forbiddenWords") or []:
        if bad and bad in t:
            return False, f"出现了别的角色的口癖「{bad}」"
    return True, "通过"


def handoff_intro(pid: str = TASK_PERSONA) -> str:
    """小玉接手复杂任务时的开场（需求 2：明确她是技术担当）。"""
    if pid == TASK_PERSONA:
        return random.choice([
            "主人，这件事请交给我吧。（检索中……）我先看一下再动手。",
            "收到，主人。技术方面的委托由我负责——请交给我吧。",
        ])
    return ""


# ---------------- 路由 ----------------

def route(text: str, current_pid: str, mode: str = "ask",
          classification: classifier.Result | None = None,
          classifier_mode: str = "rules", llm=None) -> Decision:
    """核心路由：决定「谁来回这句话」。

    mode: "ask"（先推脱+一键切换，默认）| "auto"（直接转交小玉）
    """
    r = classification or classifier.classify(text, mode=classifier_mode, llm=llm)
    d = Decision(level=r.level, action=ACTION_KEEP, from_pid=current_pid,
                 score=r.score, reasons=list(r.reasons), mode=mode)

    if not r.is_complex:
        return d                                    # 需求 2：simple 保持当前人格

    if current_pid == TASK_PERSONA:
        return d                                    # 已经是小玉，直接干

    d.to_pid = TASK_PERSONA
    if mode == "auto":
        d.action = ACTION_AUTO_SWITCH
        d.intro = handoff_intro(TASK_PERSONA)
    else:
        d.action = ACTION_DEFLECT
        d.line = deflect_line(current_pid)
    return d
