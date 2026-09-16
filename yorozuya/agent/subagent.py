# -*- coding: utf-8 -*-
"""子代理（M3）：Explore / Plan —— 只读、独立上下文、只回摘要

为什么需要子代理：
· 大范围搜索（「读 50 个文件找到 X」）会把主上下文的预算全部吃掉，
  还稀释了主模型对「当前这一步该干什么」的注意力。
· 子代理用**独立上下文**跑只读工具，最后让模型把结果压成一份结构化摘要，
  主上下文只收到这份摘要，而不是 50 个文件的原文 —— 这就是「把读 50 个文件压成 <1k token」。

Worker（可写）不单独实现：主内核本身就是「同一工作区同时只允许一个写入者」，
  写操作天然串行（快照 / undo 的语义依赖这一点）。
"""
from __future__ import annotations

from . import common, tools as tools_mod
from .workspace import Workspace

# 只读子代理能用的工具（**白名单**，不是"排除写工具"—— 漏一个不等于多一个）
#   2026-09-16 扩到 8 个：加了 read_many（它的本职就是"一次读几十个文件再压成摘要"）
#   与 git_status/git_diff/git_log（看仓库状态也是理解项目的一部分，且都是只读）。
#   ⚠️ 加东西前先问自己：这个工具会不会**改动任何东西**？会，就不能加进来。
READ_ONLY_TOOLS = {"read_file", "list_dir", "glob", "grep", "read_many",
                   "git_status", "git_diff", "git_log"}

EXPLORE_SYSTEM = """你是 Yorozuya 工程内核的「探查子代理」，只读、不写任何文件。
你的任务是在工作区里搜索、定位、理解，然后把发现**压缩成一份紧凑的结构化摘要**。

规则：
· 只能用只读工具：read_file / read_many / list_dir / glob / grep
  （另有 git_status / git_diff / git_log 可以看仓库状态）。path 一律相对工作区根。
· 先想清楚要找什么再动手；单次 grep/read 拿不到就换关键词，不要一次把整个目录读完。
· 最终答复必须是一份**要点式摘要**，只保留：关键结论、文件路径、行号、命令/符号名、
  以及「这跟委托有什么关系」。不要贴大段原文。
· 你的答复会直接回到主内核的上下文里，所以越紧凑越好。"""

PLAN_SYSTEM = """你是 Yorozuya 工程内核的「计划子代理」，只读、不写任何文件。
你的任务是根据委托，先摸清现状，再产出一份**实施计划 + 影响面清单**。

规则：
· 只能用只读工具：read_file / list_dir / glob / grep（path 一律相对工作区根）。
· 计划要具体到：改哪些文件、每个文件改什么、有什么风险、改完怎么验证。
· 最终答复用要点式，标注每一步涉及的文件路径与风险等级（低/中/高）。"""

SUMMARY_PROMPT = ("请把上面所有发现整理成一份紧凑的要点式摘要（只保留关键结论、文件路径、"
                  "行号、命令/符号名，以及与任务的关系；不要贴大段原文）。")


def cap_summary(text: str, limit: int) -> str:
    """摘要硬上限。

    ★ 为什么必须是**硬**上限：子代理存在的意义就是「别把大段原文塞回主上下文」。
      只靠提示词说「控制在几百字内」是不够的 —— 实测真实模型回了 1237 字就直接进主上下文了
      （默认上限当时是 1200，等于没起作用）。所以这里做真截断，并且**明确标注被截断**，
      不假装这就是全部发现。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    mark = "…（摘要超长已截断，完整发现见该子代理的工具记录）"
    keep = max(0, limit - len(mark))
    return text[:keep].rstrip() + mark


class Subagent:
    """只读子代理：独立上下文，跑若干步只读工具，最后让模型产出一份受限长度的摘要。"""

    def __init__(self, ws: Workspace, model, *, kind: str = "explore",
                 max_steps: int = 12, summary_limit: int = 1000):
        self.ws = ws
        self.model = model
        self.kind = kind if kind in ("explore", "plan") else "explore"
        self.max_steps = max_steps
        self.summary_limit = summary_limit       # 硬上限（字符数）；见 cap_summary 的说明
        self.registry = tools_mod.build_registry(ws, 0, 0, None, common.MAX_TOOL_OUTPUT)\
            .subset(READ_ONLY_TOOLS)

    # ---------------- 主入口 ----------------

    def run(self, task: str) -> dict:
        """返回 {kind, summary, steps, tool_calls, tokens_in, tokens_out, truncated}"""
        system = EXPLORE_SYSTEM if self.kind == "explore" else PLAN_SYSTEM
        system += (f"\n· 最终摘要**上限 {self.summary_limit} 字**（超了会被硬截断）。")
        history = [{"role": "system", "content": system},
                   {"role": "user", "content": f"## 任务\n{task}"}]
        steps = tool_calls = tokens_in = tokens_out = 0
        summary = ""

        for _ in range(self.max_steps):
            resp = self.model.complete(history, tools=self.registry.specs())
            tokens_in += getattr(resp, "tokens_in", 0)
            tokens_out += getattr(resp, "tokens_out", 0)
            if not resp.tool_calls:
                summary = (resp.text or "").strip()
                if summary:
                    break
                continue
            steps += 1
            history.append({"role": "assistant", "content": resp.text or "",
                            "tool_calls": [
                                {"id": c["id"], "type": "function",
                                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                                for c in resp.tool_calls]})
            for c in resp.tool_calls:
                tool_calls += 1
                tool = self.registry.get(c["name"])
                if tool is None:
                    obs = (f"[失败] 子代理没有工具 `{c['name']}`（只读子代理只有 "
                           f"{', '.join(sorted(READ_ONLY_TOOLS))}）")
                else:
                    ok, why = tools_mod.base.validate_args(tool.params, c.get("args") or {})
                    if not ok:
                        obs = f"[失败] 参数不合法：{why}"
                    else:
                        try:
                            obs = tool.fn(c.get("args") or {}).to_context(c["name"])
                        except Exception as e:                       # noqa: BLE001
                            obs = f"[失败] {type(e).__name__}: {e}"
                history.append({"role": "tool", "tool_call_id": c["id"],
                                "name": c["name"], "content": obs})

        # 没有自然收尾（步数耗尽 / 模型没给结论）→ 强制让模型总结一次
        if not summary:
            history.append({"role": "user", "content": SUMMARY_PROMPT})
            resp = self.model.complete(history, tools=None)
            tokens_in += getattr(resp, "tokens_in", 0)
            tokens_out += getattr(resp, "tokens_out", 0)
            summary = (resp.text or "").strip()

        summary = cap_summary(summary, self.summary_limit)
        return {"kind": self.kind, "summary": summary, "steps": steps,
                "tool_calls": tool_calls, "tokens_in": tokens_in, "tokens_out": tokens_out,
                "truncated": summary.endswith("工具记录）")}


def spawn(ws: Workspace, model, kind: str, task: str, **kw) -> dict:
    """便捷入口：`tools/__init__.py` 的 spawn_subagent 工具直接调这里。"""
    return Subagent(ws, model, kind=kind, **kw).run(task)
