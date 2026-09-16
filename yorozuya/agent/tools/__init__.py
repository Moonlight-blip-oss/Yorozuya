# -*- coding: utf-8 -*-
"""工具注册表装配"""
from __future__ import annotations

from .. import common, storage
from .base import Registry, ToolResult, read_tool, write_tool, exec_tool
from .bg import BackgroundRunner
from .edit import build_write_tools
from .files import build_file_tools
from .fs import build_read_tools, backend_name
from .git import build_git_tools
from .shell import build_exec_tools
from .weather import build_weather_tools
from .web import build_web_tools

__all__ = ["build_registry", "backend_name", "Registry", "ToolResult", "BackgroundRunner"]


def build_registry(ws, user_id: int, workspace_id: int, conn, output_limit: int = common.MAX_TOOL_OUTPUT,
                   on_line=None, record_artifact=None, model=None,
                   mcp_configs: dict | None = None, bg=None) -> Registry:
    reg = Registry()
    for t in build_read_tools(ws, output_limit):
        reg.register(t)
    for t in build_write_tools(ws, output_limit):
        reg.register(t)
    # ★ 2026-09-16 扩的工具（她要求「更多 tools」）：
    #   文件管理组（copy/move/delete→回收站/mkdir/read_many/apply_patch）、
    #   Git 组（status/diff/log 只读 + commit 需审批）、联网（web_fetch，出网需审批）、
    #   后台进程（run_background/bg_output/bg_stop/bg_list；运行结束由内核统一收掉）。
    for t in build_file_tools(ws, output_limit):
        reg.register(t)
    for t in build_git_tools(ws):
        reg.register(t)
    for t in build_web_tools(ws, output_limit):
        reg.register(t)
    # ★ 2026-09-16 加：天气 / 气候（她要求「增加查询气候的 tools」）。
    #   数据源用 Open-Meteo —— 免费且**不需要 API Key**（要 Key 等于要她先注册，多数人就此卡住）。
    #   风险 = exec（出网；地名本身也是位置信息），与 web_fetch 同一口径。
    for t in build_weather_tools(ws, output_limit):
        reg.register(t)
    for t in build_exec_tools(ws, output_limit, common.ARTIFACTS_DIR,
                              record_artifact=record_artifact, on_line=on_line):
        reg.register(t)
    bg = bg if bg is not None else BackgroundRunner(ws)
    reg.bg = bg                              # 调用方（内核）据此在运行结束时收干净
    for t in bg.tools():
        reg.register(t)
    for t in _memory_tools(user_id, workspace_id, conn):
        reg.register(t)
    for t in _plan_tools():
        reg.register(t)
    for t in _subagent_tools(ws, model):        # M3：子代理（只读）
        reg.register(t)
    if mcp_configs:                              # M3：MCP（只在配置了服务器时才注册）
        for t in _mcp_tools(mcp_configs):
            reg.register(t)
    return reg


def _subagent_tools(ws, model):
    """M3：spawn_subagent —— 派一个只读子代理（explore / plan），只回压缩摘要。

    风险 = read：子代理只带只读工具，不碰文件系统、不跑命令。
    """
    from .. import subagent as sa

    def spawn_subagent(args: dict) -> ToolResult:
        if model is None:
            return ToolResult(False, error="没有可用模型（未配置 API Key），子代理需要模型驱动")
        kind = args.get("kind") or "explore"
        task = (args.get("task") or "").strip()
        if not task:
            return ToolResult(False, error="task 不能为空")
        try:
            out = sa.spawn(ws, model, kind, task)
        except Exception as e:                                   # noqa: BLE001
            return ToolResult(False, error=f"子代理失败：{type(e).__name__}: {e}")
        return ToolResult(True,
                          summary=f"{kind} 子代理：{out['tool_calls']} 次工具调用，"
                                  f"摘要 {len(out['summary'])} 字"
                                  + ("（超上限已截断）" if out.get("truncated") else ""),
                          output=out["summary"], data=out)

    return [read_tool("spawn_subagent",
        "派一个只读子代理去做大范围搜索/理解（explore）或产出实施计划（plan）。"
        "子代理用独立上下文，只回一份压缩摘要（硬上限 1000 字），不回原始大日志 —— "
        "适合「读几十个文件」这种会撑爆主上下文的事。",
        {"type": "object", "required": ["kind", "task"], "properties": {
            "kind": {"type": "string", "enum": ["explore", "plan"],
                     "description": "explore=搜索理解；plan=产出实施计划"},
            "task": {"type": "string", "description": "子代理要做什么（尽量具体）"}}},
        spawn_subagent)]


def _mcp_tools(mcp_configs: dict):
    """M3：mcp_list_tools / mcp_call —— 经 stdio 调用外部 MCP 服务器。

    风险 = exec：MCP 工具能在这台机器上跑任意东西，必须走审批闸门。
    """
    from .. import mcp_client

    mcp_configs = mcp_configs or {}

    def _with_client(server: str, fn):
        cfg = mcp_configs.get(server)
        if not cfg:
            return ToolResult(False, error=f"没有配置名为 `{server}` 的 MCP 服务器"
                                           f"（可用：{', '.join(sorted(mcp_configs)) or '无'}）")
        cli = mcp_client.MCPClient(cfg.get("command"), list(cfg.get("args") or []),
                                   cfg.get("env"))
        try:
            cli.start()
            cli.initialize()
            return fn(cli)
        except Exception as e:                                   # noqa: BLE001
            return ToolResult(False, error=f"{type(e).__name__}: {e}")
        finally:
            cli.close()

    def mcp_list_tools(args: dict) -> ToolResult:
        def do(cli):
            tools = cli.list_tools()
            body = "\n".join(f"- {t.get('name')}: {(t.get('description') or '')[:80]}"
                             for t in tools)
            return ToolResult(True, summary=f"{args['server']} 暴露 {len(tools)} 个工具",
                              output=body or "（无）", data={"server": args["server"], "tools": tools})
        return _with_client(args["server"], do)

    def mcp_call(args: dict) -> ToolResult:
        def do(cli):
            r = cli.call_tool(args["tool"], args.get("arguments") or {})
            return ToolResult(True, summary=f"{args['server']}.{args['tool']}"
                                            + ("（返回错误）" if r["isError"] else ""),
                              output=r["text"],
                              data={"server": args["server"], "tool": args["tool"],
                                    "isError": r["isError"]})
        return _with_client(args["server"], do)

    return [
        exec_tool("mcp_list_tools", "列出某个已配置 MCP 服务器暴露的工具清单。",
                  {"type": "object", "required": ["server"], "properties": {
                      "server": {"type": "string", "description": "MCP 服务器名"}}},
                  mcp_list_tools, preview_keys=("server",)),
        exec_tool("mcp_call", "调用某个已配置 MCP 服务器的一个工具（会经审批）。",
                  {"type": "object", "required": ["server", "tool"], "properties": {
                      "server": {"type": "string"}, "tool": {"type": "string"},
                      "arguments": {"type": "object", "description": "工具入参"}}},
                  mcp_call, preview_keys=("server", "tool")),
    ]


def _plan_tools():
    """计划清单工具（计划卡的数据来源）。

    为什么要有它：内核的上下文里一直留着 `plan` 这一层（`ctx.build(history, goal, plan)`），
    但在此之前**没人往里写** —— 计划卡只能是摆设。加上这个工具后：
    模型调一次 `todo_write`，内核就把它记进本轮计划、发一条 `plan.update` 事件、并在后续上下文里
    作为「当前计划」回灌，于是「计划卡随进度勾选」是真的，不是前端编的。
    风险等级：read —— 它只写内存里的一张清单，不碰文件系统。
    """

    def todo_write(args: dict) -> ToolResult:
        items = args.get("items") or []
        plan = []
        for it in items[:20]:
            if isinstance(it, str):
                plan.append({"text": it.strip()[:200], "done": False})
            elif isinstance(it, dict) and (it.get("text") or "").strip():
                plan.append({"text": str(it["text"]).strip()[:200], "done": bool(it.get("done"))})
        if not plan:
            return ToolResult(False, error="items 不能为空（每项要么是字符串，要么是 {text, done}）")
        done = sum(1 for p in plan if p["done"])
        body = "\n".join(f"- [{'x' if p['done'] else ' '}] {p['text']}" for p in plan)
        return ToolResult(True, summary=f"计划已更新（{done}/{len(plan)} 完成）", output=body,
                          data={"plan": plan})

    return [
        read_tool("todo_write", "写下/更新这次任务的计划清单（会显示成计划卡，并作为后续步骤的当前计划）。",
                  {"type": "object", "required": ["items"], "properties": {
                      "items": {"type": "array", "description": "计划项，按顺序",
                                "items": {"type": "object", "properties": {
                                    "text": {"type": "string", "description": "这一步要做什么"},
                                    "done": {"type": "boolean", "description": "是否已完成"}}}}}},
                  todo_write),
    ]


def _memory_tools(user_id: int, workspace_id: int, conn):
    """工程记忆工具。

    ★ 写进 `agent_memories`（工程域），**不碰** `memories`（陪伴域）：
      她记得你爱吃什么，对你的代码库没有帮助，只会挤占预算并带来编造风险。
    """

    def memory_write(args: dict) -> ToolResult:
        text = (args.get("text") or "").strip()
        if not text:
            return ToolResult(False, error="text 不能为空")
        storage.add_memory(conn, user_id, workspace_id, text, source="agent")
        return ToolResult(True, summary=f"已记入本项目的工程记忆：{text[:40]}",
                          output=f"（工程记忆已保存，仅在本工作区使用）\n{text}",
                          data={"text": text})

    def memory_read(args: dict) -> ToolResult:
        rows = storage.list_memories(conn, user_id, workspace_id)
        if not rows:
            return ToolResult(True, summary="本项目暂无工程记忆", output="（空）")
        body = "\n".join(f"- {r['text']}" for r in rows[:30])
        return ToolResult(True, summary=f"读到 {len(rows)} 条工程记忆", output=body,
                          data={"count": len(rows)})

    return [
        read_tool("memory_read", "读取**本项目**的工程记忆（不含陪伴记忆）。",
                  {"type": "object", "properties": {}}, memory_read),
        write_tool("memory_write", "把关于这个项目的长期事实记进工程记忆（如构建方式、约定、坑）。",
                   {"type": "object", "required": ["text"], "properties": {
                       "text": {"type": "string", "description": "一句话事实，越具体越好"}}},
                   memory_write, preview_keys=("text",)),
    ]
