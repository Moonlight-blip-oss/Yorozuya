# -*- coding: utf-8 -*-
"""Agent 内核：单步循环 + 三道闸门（权限 / 预算 / ★ 验证）

一次「步进」就是下面 `step()` 里那条流程；这里只强调两处：

① 工具输出回填上下文时**永远是数据**（`<<<TOOL_OUTPUT>>>` 包裹），不具备指令地位。
② ★ 验证闸门：改完必须由**内核自己**跑验证命令证明没坏，而不是相信模型说「我已验证」。
   实测过真实模型改完直接声称「运行验证 → 输出 ALL-OK ✅」而链路里根本没有这次调用。
   判定按「基线四象限」：绿→绿 通过／绿→红 回归／红→绿 通过／红→红 无法证明。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import common, context as ctx_mod, storage, tools as tools_mod
from .permissions import PermissionEngine, SessionPolicy
from .snapshot import SnapshotStore
from .workspace import Workspace

MAX_VERIFY_ROUNDS = 2


@dataclass
class RunResult:
    run_id: str
    status: str = "done"        # done / verify_failed / unverified / blocked / budget / stopped / error
    text: str = ""
    steps: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    touched: list = field(default_factory=list)
    diffs: list = field(default_factory=list)
    approvals: list = field(default_factory=list)
    summary: str = ""
    error: str = ""
    verification: dict = field(default_factory=dict)
    seconds: float = 0.0
    task_level: str = ""


class ApprovalTimeout(Exception):
    pass


class Kernel:
    def __init__(self, conn, ws: Workspace, model, *, user_id: int = 0, workspace_id: int = 0,
                 persona_id: str = "tama", level: int = 1, on_event=None, approval_provider=None,
                 auto_approve: bool = False, snapshot_store=None, budget: dict | None = None,
                 context_builder=None, demo_note: str = "", mcp_configs: dict | None = None,
                 todo_id: str = ""):
        self.conn = conn
        self.ws = ws
        self.model = model
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.persona_id = persona_id
        self.todo_id = todo_id or ""        # 从哪条待办派出去的（空 = 不是从待办起的）
        self.on_event = on_event
        self.approval_provider = approval_provider
        self.auto_approve = auto_approve
        self.demo_note = demo_note or ""      # 演示模式：开场就把「这不是模型干的」写进事件流

        self.budget = budget or ws.cfg.budget or {}
        self.ctx = context_builder or ctx_mod.ContextBuilder(ws, self.budget)
        self.perms = PermissionEngine(ws, level, SessionPolicy())
        self.snaps = snapshot_store or SnapshotStore(conn)
        # 后台进程管理器：**内核持有**（运行结束时统一收掉，见下面的 finally）
        self.bg = tools_mod.BackgroundRunner(ws)
        self.registry = tools_mod.build_registry(
            ws, user_id, workspace_id, conn, common.MAX_TOOL_OUTPUT,
            on_line=self._on_line, record_artifact=self._record_artifact,
            model=model, mcp_configs=mcp_configs, bg=self.bg)

        self._run_id = ""
        self._step = 0
        self._touched: set = set()
        self._diffs: list = []
        self.plan: list = []            # 计划卡的数据来源：todo_write 工具写它
        self._verified: bool | None = None
        self._verify_attempts = 0
        self._verification: dict = {}
        self._baseline: dict | None = None
        self._stop = False
        self._stop_status = ""       # 停止时希望报告的状态（默认 stopped；审批超时停下 = blocked）
        self._approval_timeouts = 0  # 连续「没人回应」的审批次数（用于及时止损）

    # ---------------- 事件 ----------------

    def _emit(self, etype: str, payload: dict) -> dict:
        payload = dict(payload or {})
        seq = 0
        if self._run_id:
            try:
                seq = storage.add_event(self.conn, self._run_id, etype, payload)
            except Exception:
                seq = 0
        payload["seq"] = seq
        store = {"type": etype, "seq": seq, "payload": payload}
        if self.on_event:
            try:
                self.on_event(store)
            except Exception:
                pass
        return store

    def _on_line(self, text: str) -> None:
        """命令的流式输出 → 事件（长命令不再「卡住不动」）。"""
        if self._run_id:
            self._emit(common.Ev.TOOL_OUTPUT, {"step": self._step, "text": text})

    def _record_artifact(self, kind: str, path: str, size: int, preview: str) -> None:
        if self._run_id:
            try:
                storage.add_artifact(self.conn, self._run_id, self._step, kind, path, size, preview)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop = True

    # ---------------- 验证闸门 ----------------

    def _run_verify_cmds(self) -> tuple:
        """跑工作区声明的验证命令；全部退出码 0 才算通过。"""
        cmds = self.ws.cfg.verify_list()
        if not cmds:
            return False, "（本工作区未配置验证命令）"
        outs, ok_all = [], True
        for c in cmds:
            r = self.registry.get("run_command")
            res = r.fn({"command": c, "timeout": self.ws.cfg.budget.get("command_timeout", 120)})
            code = (res.data or {}).get("exit_code")
            ok = bool((res.data or {}).get("ok_exit"))
            ok_all = ok_all and ok
            # ★ 被**拒绝执行**（命令黑名单 / 路径 jail / 裸分号…）时 data 是空的、只有一个 error，
            #   以前这里只打「退出码 None」不说原因 —— 于是"验证没通过"变成一句无从下手的空话。
            detail = (res.output or "").strip() or (res.error or "").strip()
            if not detail and code is None:
                detail = "（这条命令没有被执行，且没有给出原因 —— 属于内核 bug，请反馈）"
            outs.append(f"$ {c}  → 退出码 {code}\n{detail[-1200:]}")
        return ok_all, "\n\n".join(outs)

    def _ensure_baseline(self) -> None:
        """第一次写之前先跑一次，记下「改动前的状态」—— 才能区分「这次改坏的」与「本来就坏的」。"""
        if self._baseline is not None:
            return
        if not self.ws.cfg.verify_list():
            self._baseline = {"ran": False, "ok": True, "cmd": ""}
            return
        ok, out = self._run_verify_cmds()
        self._baseline = {"ran": True, "ok": bool(ok), "cmd": "；".join(self.ws.cfg.verify_list()),
                          "output": out[-600:]}
        self._emit(common.Ev.NOTICE, {
            "level": "info" if ok else "warn",
            "text": f"改动前基线：{'通过' if ok else '未通过（既有问题，本次改动不背锅）'}"})

    def _code_touched(self) -> bool:
        doc = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv"}
        return any(("." + r.rsplit(".", 1)[-1].lower()) not in doc for r in self._touched)

    def _verification_gate(self) -> dict:
        """交付前的最后一道闸门。返回 {action: pass|retry, status, observation}"""
        if not self._touched:
            return {"action": "pass", "status": "done"}
        if self._verified is True:
            return {"action": "pass", "status": "done"}
        if not self.ws.cfg.require_verification:
            self._verification = {"ran": False, "ok": False, "source": "off",
                                  "note": "工作区关闭了验证闸门"}
            return {"action": "pass", "status": "unverified"}

        base_ok = bool((self._baseline or {}).get("ok"))
        cmds = self.ws.cfg.verify_list()
        if not cmds:
            self._verification = {"ran": False, "ok": False, "source": "no-cmd",
                                  "note": "工作区没配验证命令，无法自动证明"}
            return {"action": "pass", "status": "unverified"}

        if self._verify_attempts >= MAX_VERIFY_ROUNDS:
            return {"action": "pass", "status": "verify_failed"}

        self._verify_attempts += 1
        ok, out = self._run_verify_cmds()
        self._verified = bool(ok)
        self._verification = {"ran": True, "ok": bool(ok), "baseline_ok": base_ok,
                              "code_touched": self._code_touched(),
                              "cmd": "；".join(cmds), "round": self._verify_attempts,
                              "source": "kernel", "output": out[-1500:]}
        self._emit(common.Ev.VERIFY, {"ok": bool(ok), "cmd": "；".join(cmds),
                                      "baseline_ok": base_ok, "output": out[-1200:]})
        self._emit(common.Ev.NOTICE, {
            "level": "info" if ok else "warn",
            "text": f"内核自检（第 {self._verify_attempts} 轮）→ {'通过' if ok else '未通过'}"
                    + (f"（改动前{'通过' if base_ok else '未通过'}）" if not ok else "")})
        if ok:
            return {"action": "pass", "status": "done"}
        # 红 → 红 且本次只动了文档/配置：既有问题不背锅
        if not base_ok and not self._code_touched():
            self._verification["baseline_failed"] = True
            self._emit(common.Ev.NOTICE, {"level": "info",
                                          "text": "本次只改了文档/配置，验证命令在改动前就是红的 → 不背锅"})
            return {"action": "pass", "status": "done"}
        if self._verify_attempts < MAX_VERIFY_ROUNDS:
            return {"action": "retry", "status": "verify_failed",
                    "observation": (f"内核自检未通过（命令：{'；'.join(cmds)}），这轮改动还不算完成。\n"
                                    + ("注意：这条验证在改动前是通过的，说明改动引入了回归。\n" if base_ok else
                                       "注意：这条验证在改动前就失败；如果你是在修它，请让它变绿。\n")
                                    + f"{out[-1500:]}\n\n请定位并修正后重试。")}
        return {"action": "pass", "status": "verify_failed"}

    # ---------------- 主循环 ----------------

    def run(self, goal: str, task_level: str = "", on_delta=None,
            run_id: str = "") -> RunResult:
        """跑一次委托。

        run_id 可以由调用方**预先指定**（服务层要先把 id 告诉前端，好让审批 / 回放 / undo
        都能对上同一次运行）。不传就自己生成。
        """
        t0 = time.time()
        run_id = run_id or common.new_id()
        self._run_id = run_id
        if self.workspace_id is None:
            self.workspace_id = storage.upsert_workspace(self.conn, self.user_id, self.ws.cfg.name,
                                                         str(self.ws.root), self.ws.cfg.to_dict())
        storage.create_run(self.conn, run_id, self.user_id, self.workspace_id or 0, goal,
                           self.model.name, self.persona_id, self.todo_id)
        res = RunResult(run_id=run_id, task_level=task_level)
        self._emit(common.Ev.RUN_START, {
            "run_id": run_id, "workspace": self.ws.cfg.name, "root": str(self.ws.root),
            "goal": goal, "model": self.model.name, "persona": self.persona_id,
            "taskLevel": task_level,
            "budget": {"max_steps": self.ctx.max_steps, "max_seconds": self.ctx.max_seconds}})
        if self.demo_note:
            self._emit(common.Ev.NOTICE, {"level": "warn", "text": self.demo_note, "demo": True})

        history: list = []
        mems = ctx_mod.load_project_memories(self.conn, self.user_id, self.workspace_id or 0)
        try:
            for step in range(1, self.ctx.max_steps + 1):
                self._step = step
                res.steps = step
                if self._stop:
                    res.status = self._stop_status or "stopped"
                    break
                reason = self.ctx.should_stop(step, time.time() - t0, history)
                if reason:
                    res.status = "budget"
                    res.error = reason
                    self._emit(common.Ev.NOTICE, {"level": "warn", "text": f"预算闸门：{reason}"})
                    break

                messages = self.ctx.build(history, goal, self.plan, mems, model=self.model)
                resp = self.model.complete(messages, tools=self.registry.specs())
                res.tokens_in += getattr(resp, "tokens_in", 0)
                res.tokens_out += getattr(resp, "tokens_out", 0)

                if resp.text:
                    res.text = resp.text
                    self._emit(common.Ev.TEXT_DELTA, {"step": step, "text": resp.text})
                if not resp.tool_calls:
                    res.status = "done"
                    break

                history.append({"role": "assistant", "content": resp.text,
                                "tool_calls": [{"id": c["id"], "type": "function",
                                                "function": {"name": c["name"],
                                                             "arguments": c["arguments"]}}
                                               for c in resp.tool_calls]})
                for call in resp.tool_calls:
                    if self._stop:
                        break
                    obs = self._exec_call(step, call, res)
                    history.append({"role": "tool", "tool_call_id": call["id"],
                                    "name": call["name"], "content": obs})
            else:
                res.status = "budget"
                res.error = "达到步数上限"

            # ★ 验证闸门：改完必须由内核自己证明没坏；失败则把真实报错喂回去重试（限次）
            for _round in range(MAX_VERIFY_ROUNDS + 1):
                gate = self._verification_gate()
                if gate["action"] == "pass":
                    if res.status == "done":
                        res.status = gate["status"]
                    break
                if res.status == "done":
                    res.status = gate["status"]
                history.append({"role": "system", "content": "【内核·验证闸门】" + gate["observation"]})
                self._emit(common.Ev.NOTICE, {"level": "warn", "text": "验证未通过，已把真实报错交回模型修正"})
                messages = self.ctx.build(history, goal, self.plan, mems, model=self.model)
                resp = self.model.complete(messages, tools=self.registry.specs())
                res.tokens_in += getattr(resp, "tokens_in", 0)
                res.tokens_out += getattr(resp, "tokens_out", 0)
                if resp.text:
                    res.text = resp.text
                    self._emit(common.Ev.TEXT_DELTA, {"step": self._step, "text": resp.text})
                for c in resp.tool_calls or []:
                    obs = self._exec_call(self._step, c, res)
                    history.append({"role": "tool", "tool_call_id": c["id"],
                                    "name": c["name"], "content": obs})

        except Exception as e:
            res.status = "error"
            res.error = f"{type(e).__name__}: {e}"
            self._emit(common.Ev.NOTICE, {"level": "error", "text": res.error})
            # ★ 上下文放开之后（max_chars=0 = 不压缩），唯一还会拦路的就是**模型自己的窗口**。
            #   这类报错原文通常是英文的 "maximum context length is N tokens"，直接丢给用户
            #   等于没说清"该怎么办"。这里补一句能落地的：要么换长窗口模型，要么给这个工作区
            #   设个 max_chars 让内核主动压缩。
            _low = str(e).lower()
            if any(k in _low for k in ("context length", "context_length", "too many tokens",
                                       "maximum context", "context window", "reduce the length")):
                self._emit(common.Ev.NOTICE, {
                    "level": "error",
                    "text": "看起来是**模型自己的上下文窗口满了**（当前的设置是不压缩、不限制长度）。"
                            "两条路：① 换一个窗口更大的模型；"
                            "② 在这个工作区的 agent.config.json 里加 "
                            "\"budget\": {\"max_chars\": 60000} —— 打开压缩后内核会把早期步骤压成要点。"})
        finally:
            # ★ 后台进程（run_background 起的服务/长任务）**必须在这里收干净**：
            #   不收的话会留下占着端口的孤儿进程，而她看到的只是"工作台跑完了"。
            try:
                killed = self.bg.close_all()
                if killed:
                    self._emit(common.Ev.NOTICE, {
                        "level": "info",
                        "text": f"已收掉 {killed} 个后台进程（这次运行起的服务不会留在机器上）"})
            except Exception:
                pass

        res.touched = sorted(self._touched)
        res.diffs = self._diffs
        res.verification = self._verification
        res.seconds = round(time.time() - t0, 2)
        res.summary = self._summary(res)
        storage.finish_run(self.conn, run_id, res.status, steps=res.steps,
                           tool_calls=res.tool_calls, tokens_in=res.tokens_in,
                           tokens_out=res.tokens_out, seconds=res.seconds,
                           summary=res.summary, error=res.error, verification=res.verification)
        self._emit(common.Ev.RUN_SUMMARY, {"status": res.status, "touched": res.touched,
                                           "verification": res.verification,
                                           "summary": res.summary})
        self._emit(common.Ev.RUN_END, {"status": res.status, "steps": res.steps,
                                       "tool_calls": res.tool_calls, "tokens_in": res.tokens_in,
                                       "tokens_out": res.tokens_out, "seconds": res.seconds,
                                       "verification": res.verification})
        return res

    # ---------------- 单次调用 ----------------

    def _exec_call(self, step: int, call: dict, res: RunResult) -> str:
        name = call["name"]
        args = call.get("args") or {}
        tool = self.registry.get(name)
        if tool is None:
            res.tool_calls += 1
            return f"[失败] 未知工具：{name}（可用：{', '.join(self.registry.names())}）"

        ok_args, why = tools_mod.base.validate_args(tool.params, args)
        if not ok_args:
            res.tool_calls += 1
            storage.add_tool_call(self.conn, self._run_id, step, name, args, tool.risk, "deny",
                                  False, 0, why)
            return f"[失败] 参数不合法：{why}"

        # ① 权限
        verdict, reason = self.perms.check(tool, args)
        decision = verdict
        if verdict == common.DENY:
            self._emit(common.Ev.TOOL_CALL, {"step": step, "name": name, "args": args,
                                             "risk": tool.risk, "decision": "deny", "reason": reason})
            res.tool_calls += 1
            storage.add_tool_call(self.conn, self._run_id, step, name, args, tool.risk, "deny",
                                  False, 0, reason)
            self._emit(common.Ev.TOOL_RESULT, {"step": step, "name": name, "ok": False,
                                               "summary": reason})
            return f"[被拒绝] {reason}"
        if verdict == common.ASK and not self.auto_approve:
            self._emit(common.Ev.TOOL_APPROVAL, {"step": step, "name": name, "args": args,
                                                 "risk": tool.risk, "reason": reason,
                                                 "preview": tool.preview(args)})
            got = self._ask(name, tool, args, reason)
            decision = got.get("verdict", common.DENY)
            scope = got.get("scope", "once")
            # ★ 「没人回应」≠「用户点了拒绝」。原先这两件事在下游完全同形（decision 都是 deny、
            #   记录都写"用户拒绝"、给模型的话也说"用户拒绝了"），后果是：
            #   模型以为是她拒的 → 换个写法再试同一个操作 → 白烧预算还什么都做不成。
            timed_out = bool(got.get("timeout"))
            res.approvals.append({"name": name, "decision": decision, "scope": scope,
                                  "timeout": timed_out})
            try:
                storage.add_approval(self.conn, self._run_id, name, decision, scope, timed_out)
            except Exception:
                pass
            if decision != common.ALLOW:
                res.tool_calls += 1
                note = "审批超时（没人回应）" if timed_out else "用户拒绝"
                storage.add_tool_call(self.conn, self._run_id, step, name, args, tool.risk,
                                      "deny", False, 0, note)
                self._emit(common.Ev.TOOL_RESULT, {"step": step, "name": name, "ok": False,
                                                   "summary": note})
                if timed_out:
                    # 级别用 error：前端对 error 提示是**强制滚到眼前**的，
                    # 因为「审批卡没被看见」这件事必须让用户知道（否则他会以为是自己拒的）。
                    # 秒数只在真拿得到时才报 —— 拿不到就写「超时」，不编一个数字。
                    _secs = int(getattr(self.approval_provider, "timeout", 0) or 0)
                    _how = f"超过 {_secs} 秒" if _secs > 0 else "超时"
                    self._emit(common.Ev.NOTICE, {
                        "level": "error",
                        "text": f"审批卡{_how}没人回应，已按「拒绝」处理（{name}）。"
                                f"如果你当时并没有看到这张卡，请把这条告诉我 —— "
                                f"那是界面问题，不是你点错了。"})
                    self._approval_timeouts += 1
                    if self._approval_timeouts >= 2:
                        # 及时止损：人都已经不在电脑前两次了，再往下跑就是白烧 token 和步数
                        self._emit(common.Ev.NOTICE, {
                            "level": "error",
                            "text": "连续两次审批没人回应 —— 已停下这次委托，避免继续白烧预算。"
                                    "你回来后在「委托」页重新发起即可。"})
                        self._stop = True
                        self._stop_status = "blocked"
                        return "[已停下] 连续审批没人回应，本次委托已中止。"
                    return ("[被拒绝] 审批超时：**没有人回应**这张审批卡（不是用户点了拒绝）。"
                            "不要换个写法再试同一个操作；改用只读手段确认现状，或直接停下来说明情况。")
                return "[被拒绝] 用户拒绝了这次调用，请换个思路或直接询问用户。"
            if scope == "tool":
                self.perms.policy.allow_tool(name)
            elif scope == "prefix" and tool.risk == common.RISK_EXEC:
                self.perms.policy.allow_prefix(_prefix_of(str(args.get("command") or "")))

        self._emit(common.Ev.TOOL_CALL, {"step": step, "name": name, "args": args,
                                         "risk": tool.risk, "decision": decision, "reason": reason,
                                         "preview": tool.preview(args)})

        # ② 写操作：先记「改动前的基线」，再快照（保证 undo 一定回得来）
        #    ★ 用 tool.paths(args) 而不是只看 args["path"]：apply_patch / copy_path / delete_path
        #      这些一次动多个路径的工具，只看 `path` 会**漏拍快照**（回滚回来一半，改动清单也少记）。
        touch = tool.paths(args) if tool.risk == common.RISK_WRITE else []
        if touch:
            self._ensure_baseline()
            try:
                self.snaps.capture(self._run_id, step, self.ws, touch,
                                   label=f"{name} {', '.join(touch[:3])}")
            except Exception as e:
                return f"[失败] 写前快照失败，已中止这次写入：{e}"

        t0 = time.time()
        try:
            result = tool.fn(args)
        except PermissionError as e:
            result = tools_mod.ToolResult(False, error=str(e))
        except Exception as e:
            result = tools_mod.ToolResult(False, error=f"{type(e).__name__}: {e}")
        ms = int((time.time() - t0) * 1000)
        res.tool_calls += 1
        payload = result.data or {}
        if tool.risk == common.RISK_WRITE and result.ok and payload.get("changed", True):
            # 改动清单：优先用工具自己回报的 `paths`（多文件工具），否则退回写前算出来的那几个
            rels = [str(x) for x in (payload.get("paths") or touch or [args.get("path", "")]) if x]
            diffs = payload.get("diffs") or []
            for rel in rels:
                self._touched.add(rel)
            if diffs:
                for d in diffs:
                    if d.get("diff"):
                        self._diffs.append({"path": d.get("path", rels[0]), "diff": d["diff"]})
                        self._emit(common.Ev.FS_DIFF, {"step": step, "path": d.get("path", rels[0]),
                                                       "diff": d["diff"],
                                                       "created": bool(d.get("created"))})
            elif payload.get("diff") is not None or rels:
                rel = payload.get("path") or (rels[0] if rels else "")
                if payload.get("diff"):
                    self._diffs.append({"path": rel, "diff": payload["diff"]})
                self._emit(common.Ev.FS_DIFF, {"step": step, "path": rel,
                                               "diff": payload.get("diff", ""),
                                               "created": payload.get("created", False)})
        self._emit(common.Ev.TOOL_RESULT, {"step": step, "name": name, "ok": result.ok,
                                           "summary": result.summary or result.error,
                                           "ms": ms, "truncated": result.truncated,
                                           "output": (result.output or result.error or "")[-4000:]})
        storage.add_tool_call(self.conn, self._run_id, step, name, args, tool.risk, decision,
                              bool(result.ok), ms, result.summary or result.error)
        # 计划卡：todo_write 的结果同时进「本轮计划」——后续上下文会带上它，界面也会收到 plan.update
        if name == "todo_write" and result.ok and (payload.get("plan") is not None):
            self.plan = payload["plan"]
            self._emit(common.Ev.PLAN_UPDATE, {"step": step, "plan": self.plan})
        return result.to_context(name)

    def _ask(self, name: str, tool, args: dict, reason: str) -> dict:
        if not self.approval_provider:
            return {"verdict": common.DENY, "scope": "once"}     # 安全默认：没人可问 = 拒绝
        try:
            got = self.approval_provider({"tool": name, "args": args, "risk": tool.risk,
                                          "reason": reason, "preview": tool.preview(args),
                                          "step": self._step, "run_id": self._run_id})
            if isinstance(got, dict):
                return got
            if isinstance(got, tuple):
                return {"verdict": got[0], "scope": got[1] if len(got) > 1 else "once"}
            return {"verdict": str(got) or common.DENY, "scope": "once"}
        except ApprovalTimeout:
            return {"verdict": common.DENY, "scope": "once"}
        except Exception:
            return {"verdict": common.DENY, "scope": "once"}

    # ---------------- 摘要 ----------------

    def _summary(self, res: RunResult) -> str:
        lines = [f"状态：{res.status}",
                 f"步数：{res.steps}｜工具调用：{res.tool_calls}｜token：{res.tokens_in}+{res.tokens_out}"
                 f"｜耗时：{res.seconds}s"]
        if res.touched:
            lines.append("改动：" + "、".join(res.touched))
            lines.append(f"回滚：POST /api/agent/runs/{res.run_id}/undo")
        else:
            lines.append("未改动任何文件")
        v = res.verification or {}
        if v.get("ran"):
            verdict = "通过" if v.get("ok") else ("未通过（改动前就是红的，非本次引入）"
                                                if v.get("baseline_failed") else "未通过")
            lines.append(f"验证（第 {v.get('round')} 轮）：{verdict}｜命令：{v.get('cmd')}")
        else:
            lines.append("验证：**没有自动验证**（" + (v.get("note") or "未配置验证命令") + "）")
        return "\n".join(lines)


def _prefix_of(cmd: str) -> str:
    parts = (cmd or "").strip().split()
    return " ".join(parts[:2]) + " " if len(parts) >= 2 else (cmd or "").strip()
