# -*- coding: utf-8 -*-
"""运行报告导出：把一次运行的全过程（事件 + 工具调用 + 审批 + diff + 验证）导出成可读文件

为什么要有它：「可审计」是敢把本机交给 agent 的前提之一 ——
出问题时能一条条对：第几步调了什么、参数是什么、你批没批、验证到底跑没跑。
"""
from __future__ import annotations

import json

from . import storage


def build_report(conn, run_id: str) -> dict:
    run = storage.get_run(conn, run_id)
    if not run:
        return {}
    events = storage.list_events(conn, run_id)
    calls = storage.list_tool_calls(conn, run_id)
    cks = storage.list_checkpoints(conn, run_id)
    arts = storage.list_artifacts(conn, run_id)
    try:
        verification = json.loads(run.get("verification") or "{}")
    except Exception:
        verification = {}
    return {"run": dict(run), "events": [dict(e) for e in events],
            "tool_calls": [dict(c) for c in calls], "checkpoints": [dict(c) for c in cks],
            "artifacts": [dict(a) for a in arts], "verification": verification}


def to_markdown(data: dict) -> str:
    if not data:
        return "# 找不到这次运行\n"
    run = data["run"]
    v = data.get("verification") or {}
    L = [f"# 运行报告 · {run['id']}", "",
         f"- 目标：{run['goal']}",
         f"- 工作区 ID：{run['workspace_id']}｜人格：{run.get('persona_id')}｜模型：{run.get('model')}",
         f"- 状态：**{run['status']}**｜步数：{run['steps']}｜工具调用：{run['tool_calls']}"
         f"｜token：{run['tokens_in']}+{run['tokens_out']}｜耗时：{run['seconds']}s",
         f"- 开始：{_fmt_ts(run['created_at'])}｜结束：{_fmt_ts(run.get('ended_at'))}", ""]

    L += ["## 验证结论", ""]
    if v.get("ran"):
        verdict = "通过" if v.get("ok") else ("未通过（改动前就是红的，非本次引入）"
                                            if v.get("baseline_failed") else "**未通过**")
        L += [f"- 命令：{v.get('cmd')}", f"- 改动前基线：{'通过' if v.get('baseline_ok') else '未通过'}",
              f"- 结果：{verdict}（第 {v.get('round')} 轮）", ""]
        if v.get("output"):
            L += ["```", str(v["output"])[-1500:], "```", ""]
    else:
        L += [f"- 没有自动验证：{v.get('note') or '未配置验证命令'}", ""]

    L += ["## 改了哪些文件", ""]
    if data["checkpoints"]:
        seen = {}
        for ck in data["checkpoints"]:
            try:
                seen.update(json.loads(ck["files"] or "{}"))
            except Exception:
                pass
        for rel in sorted(seen):
            rec = seen[rel]
            L.append(f"- `{rel}`" + ("（本次新建）" if not rec.get("existed") else ""))
    else:
        L.append("- （无改动）")
    L.append("")

    L += ["## 工具调用", ""]
    if data["tool_calls"]:
        L += ["| # | 步骤 | 工具 | 风险 | 裁决 | 结果 | 耗时 | 摘要 |",
              "|---|---|---|---|---|---|---|---|"]
        for i, c in enumerate(data["tool_calls"], 1):
            L.append(f"| {i} | {c['step']} | `{c['name']}` | {c['risk']} | {c['decision']} | "
                     f"{'成功' if c['ok'] else '失败'} | {c['ms']}ms | {_esc(c['summary'])} |")
    else:
        L.append("- （无）")
    L.append("")

    L += ["## 事件流", "", "| seq | 类型 | 摘要 |", "|---|---|---|"]
    for e in data["events"]:
        L.append(f"| {e['seq']} | {e['type']} | {_esc(_brief(e))} |")
    L += ["", "## 产物（被截断的大输出）", ""]
    L += ([f"- `{a['path']}`（{a['size']} 字节）" for a in data["artifacts"]] or ["- （无）"])
    return "\n".join(L) + "\n"


def _brief(e: dict) -> str:
    try:
        p = json.loads(e["payload"] or "{}")
    except Exception:
        return ""
    for k in ("text", "summary", "name", "path", "status", "level", "cmd"):
        if p.get(k):
            return str(p[k])[:120]
    return ""


def _esc(s) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")[:120]


def _fmt_ts(ms) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""

