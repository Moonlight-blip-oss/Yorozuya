# -*- coding: utf-8 -*-
"""演示模式：没配 API Key 时，让工作台也能跑出一个**真实的闭环**

为什么需要它
· 工作台的五张卡片（计划 / 工具 / 命令输出 / 审批 / diff）只有真跑起来才看得见。
  没配 Key 的用户点开工作台只看到一句「没配 Key」＝ 整个功能不可用，也就无从验收。
· 陪伴层早就有「演示模式」（`/api/chat` 没配 Key 时按人格本地出话），工程层沿用同一套诚实原则。

与陪伴层演示模式的**关键差别**（必须说清楚，否则会被误解）
· 这里**真的会改文件、真的会跑命令** —— 只不过动作不是模型想出来的，而是内核按**固定脚本**发起的。
· 因此它只在**专用演示工作区**（`zhiban-data/agent/demo-workspace/`）里跑，**绝不碰你登记的真实项目**：
  这是入口处硬编码的强制跳转，不依赖提示词自觉。
· 事件流、运行摘要、界面标签里都写明「演示模式」，不会让人误以为这是模型干的活。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import common
from .workspace import python_cmd

DEMO_WS_NAME = "万事屋演示工作区"

# 演示模式开场就写进事件流的一句话（诚实原则：这不是模型干的，一眼要能看出来）
DEMO_NOTE = ("【演示模式】没有配置 API Key：本次动作由**内核按固定脚本**发起，"
             "只在专用演示工作区（zhiban-data/agent/demo-workspace）里进行，"
             "不会碰你登记的任何真实项目。配上 API Key 后，同样的卡片流由真实模型驱动。")

_CALC_BUGGY = """def add(a, b):
    return a - b          # ← 故意留的 bug：加法写成了减法
"""
_CALC_FIXED = """def add(a, b):
    return a + b
"""

_CHECK = """from calc import add

assert add(2, 3) == 5, "add 结果不对"
print("ALL-OK")
"""

_AGENT_MD = """# 给 agent 的项目约定

这是**演示工作区**，专门用来让你在没有配置 API Key 时也能看到工作台的完整卡片流。
每次演示开始前，内核会把它恢复成下面这个「已知有 bug」的初始状态，所以可以反复演示。

## 怎么验证改动是好的
- 测试命令：见 agent.config.json 的 verify_cmds（跑 check_calc.py）
- 不要动的目录：无
"""


def demo_root() -> Path:
    return common.AGENT_DATA / "demo-workspace"


def _files() -> dict:
    # ★ 用 json.dumps 生成配置，不要手拼字符串：Windows 路径里的 `\U` 是非法 JSON 转义，
    #   手拼会让 Workspace.load 解析失败 → verify_cmds 变成空 → 演示永远「无法自动证明」。
    cfg = {
        "name": DEMO_WS_NAME,
        "test_cmd": "",
        "verify_cmds": [f"{python_cmd()} check_calc.py"],
        "require_verification": True,
        # 演示工作区显式放行**只读**工具（计划书 §4.3/§4.4 的「工作区策略」机制），
        # 这样演示里只会在「改文件」和「跑命令」上停下来问你 —— 审批卡看得见，但不用点七次。
        "policy": {"list_dir": "allow", "read_file": "allow", "glob": "allow",
                   "grep": "allow", "todo_write": "allow", "memory_read": "allow"},
        "allow_prefixes": [],
        "deny_patterns": [],
        "budget": {"max_steps": 24, "command_timeout": 120},
    }
    return {
        "calc.py": _CALC_BUGGY,
        "check_calc.py": _CHECK,
        "AGENT.md": _AGENT_MD,
        "agent.config.json": json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
    }


def reset_demo_workspace() -> Path:
    """把演示工作区恢复成初始状态（每次演示前调用，保证可重复）。

    注意：这里**不经过内核的快照系统** —— 它发生在 run 开始之前，
    所以「undo 回到运行前」拿到的就是这份复位后的状态，语义仍然自洽。
    """
    root = demo_root()
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in _files().items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return root


def demo_goal() -> str:
    return "演示：check_calc.py 跑不过，帮我定位并修好，最后再跑一次确认输出 ALL-OK"


def demo_model(name: str = "demo:scripted"):
    """按固定脚本行动的假模型（复用 ScriptedModel，保持与验收脚本同一套机制）。

    步骤编排（刻意覆盖五种卡片）：
      1. 计划卡（todo_write）+ 工具卡（list_dir）
      2. 命令输出卡（run_command，此时是红的）
      3. 工具卡（read_file）
      4. 审批卡 + diff 卡（edit_file，需要审批）
      5. 命令输出卡（run_command，变绿）
      6. 计划卡更新（勾掉全部）
      7. 结论
    """
    from .model import ScriptedModel

    py = python_cmd()
    plan = [
        {"text": "先看一眼工作区结构", "done": False},
        {"text": "跑一遍验证命令，确认现在的报错", "done": False},
        {"text": "读 calc.py 定位问题", "done": False},
        {"text": "修掉 add 的实现", "done": False},
        {"text": "再跑一次验证，确认变绿", "done": False},
    ]
    plan_done = [dict(t, done=True) for t in plan]

    def planner(step: int) -> dict:
        if step == 1:
            return {"text": "（演示模式）我先把这件事拆成几步，然后看一眼工作区里有什么。",
                    "tool_calls": [{"name": "todo_write", "args": {"items": plan}},
                                   {"name": "list_dir", "args": {"path": "."}}]}
        if step == 2:
            return {"text": "先跑一遍验证命令，看看当前的报错。",
                    "tool_calls": [{"name": "run_command",
                                    "args": {"command": f"{py} check_calc.py"}}]}
        if step == 3:
            return {"text": "报错是 add 的断言没过，我读一下 calc.py。",
                    "tool_calls": [{"name": "read_file", "args": {"path": "calc.py"}}]}
        if step == 4:
            return {"text": "定位到了：加法被写成了减法。我改掉它 —— 这一步要动文件，需要你批。",
                    "tool_calls": [{"name": "edit_file",
                                    "args": {"path": "calc.py",
                                             "old_string": "return a - b          # ← 故意留的 bug：加法写成了减法",
                                             "new_string": "return a + b"}}]}
        if step == 5:
            return {"text": "改完了，我让内核自己再跑一次验证（不靠我说「已通过」）。",
                    "tool_calls": [{"name": "run_command",
                                    "args": {"command": f"{py} check_calc.py"}}]}
        if step == 6:
            return {"text": "验证绿了，把计划勾掉。",
                    "tool_calls": [{"name": "todo_write", "args": {"items": plan_done}}]}
        return {"text": "（演示模式）做完了：calc.py 的 add 从 a - b 改成 a + b，"
                        "内核自跑 check_calc.py 输出 ALL-OK，验证通过。\n"
                        "这是内核按固定脚本演的一遍 —— 配上 API Key 之后，同样的卡片流就由真实模型驱动。"}

    return ScriptedModel(planner, name=name)
