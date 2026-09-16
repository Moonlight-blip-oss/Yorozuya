# -*- coding: utf-8 -*-
"""后台进程工具：run_background / bg_output / bg_stop / bg_list

为什么要有它：「起了服务再验证」是最常见的一类任务（`npm run dev`、`uvicorn`、`flask run`…），
而 run_command 会**一直等到命令结束**（默认 120s）→ 起服务的命令永远等不到头，只能靠超时被收掉，
那 120 秒就白烧了。

安全边界（都很硬）：
· 同时最多 **2 个**后台进程；单次调用 1 个。
· 输出写进 `zhiban-data/agent/bg/<id>.log`（不是管道），所以进程活着也能读、不会因为没人读管道被阻塞。
· 启动命令同样过**命令黑名单**（走 exec 风险级 = 要审批）。
· ★ **一次运行结束一定全部收掉**（内核在 finally 里调 close_all，Windows 用 taskkill /T 连子进程）；
  `bg_stop` 也能立刻收。绝不把进程留在机器上占端口。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .. import common
from .base import ToolResult, exec_tool, read_tool

MAX_PROCS = 2
MAX_WAIT_MS = 10000
DEFAULT_WAIT_MS = 1500


class BackgroundRunner:
    def __init__(self, ws, log_dir=None):
        self.ws = ws
        self.log_dir = Path(log_dir or (common.AGENT_DATA / "bg"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._procs: dict[str, dict] = {}

    # ---------------- 生命周期 ----------------

    def start(self, command: str, label: str = "", wait_ms: int = DEFAULT_WAIT_MS) -> dict:
        if len(self._alive()) >= MAX_PROCS:
            return {"ok": False, "error": f"后台进程最多同时 {MAX_PROCS} 个，先 bg_stop 一个"}
        bid = "bg%d%04d" % (int(time.time() % 100000), len(self._procs) + 1)
        log = self.log_dir / f"{bid}.log"
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            fh = open(log, "wb")
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "error": f"打不开日志文件：{e}"}
        try:
            proc = subprocess.Popen(command, shell=True, cwd=str(self.ws.root), stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                                   if os.name == "nt" else 0))
        except Exception as e:                                  # noqa: BLE001
            try:
                fh.close()
            except Exception:
                pass
            return {"ok": False, "error": f"启动失败：{type(e).__name__}: {e}"}
        self._procs[bid] = {"proc": proc, "log": log, "cmd": command, "label": label,
                            "started": time.time(), "offset": 0, "fh": fh}
        wait = max(0, min(int(wait_ms or 0), MAX_WAIT_MS))
        if wait:
            time.sleep(wait / 1000.0)          # 留一点时间让它把启动报错吐出来
        return {"ok": True, "id": bid, "pid": proc.pid, "log": str(log)}

    def _alive(self) -> list:
        return [k for k, v in self._procs.items() if v["proc"].poll() is None]

    def info(self, bid: str) -> dict:
        rec = self._procs.get(bid)
        if not rec:
            return {}
        code = rec["proc"].poll()
        return {"id": bid, "pid": rec["proc"].pid, "running": code is None,
                "exit_code": code, "seconds": round(time.time() - rec["started"], 1),
                "cmd": rec["cmd"], "label": rec["label"], "log": str(rec["log"])}

    def read(self, bid: str, tail_lines: int = 200, max_chars: int = 6000) -> dict:
        """读**增量**输出（上次读到哪就从哪继续）；`tail_lines>0` 时给最后 N 行。"""
        rec = self._procs.get(bid)
        if not rec:
            return {"ok": False, "error": f"没有这个后台进程：{bid}（用 bg_list 看还有哪些）"}
        try:
            data = rec["log"].read_bytes()
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "error": f"读日志失败：{e}"}
        text = data[rec["offset"]:].decode("utf-8", "replace")
        rec["offset"] = len(data)
        if tail_lines and text.count("\n") > tail_lines:
            text = "\n".join(text.splitlines()[-tail_lines:])
        if len(text) > max_chars:
            text = "…（前面省略）…\n" + text[-max_chars:]
        return {"ok": True, "text": text, "state": self.info(bid), "bytes": len(data)}

    def stop(self, bid: str) -> dict:
        rec = self._procs.get(bid)
        if not rec:
            return {"ok": False, "error": f"没有这个后台进程：{bid}"}
        proc = rec["proc"]
        if proc.poll() is None:
            kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            rec["fh"].close()
        except Exception:
            pass
        return {"ok": True, "exit_code": proc.poll(), "state": self.info(bid)}

    def close_all(self) -> int:
        """收掉所有还活着的（内核在运行结束时必调）。返回收掉了几个。"""
        n = 0
        for bid in list(self._procs):
            rec = self._procs[bid]
            if rec["proc"].poll() is None:
                kill_tree(rec["proc"])
                n += 1
            try:
                rec["fh"].close()
            except Exception:
                pass
        return n

    # ---------------- 工具 ----------------

    def tools(self):
        def run_background(args: dict) -> ToolResult:
            cmd = str(args.get("command") or "").strip()
            if not cmd:
                return ToolResult(False, error="command 不能为空")
            got = self.start(cmd, label=str(args.get("label") or ""),
                             wait_ms=int(args.get("wait_ms") or DEFAULT_WAIT_MS))
            if not got.get("ok"):
                return ToolResult(False, error=got.get("error", "启动失败"))
            first = self.read(got["id"], tail_lines=60)["text"]
            state = self.info(got["id"])
            how = "仍在运行" if state["running"] else "已退出（退出码 %s）" % state["exit_code"]
            head = (f"后台已启动：{got['id']}（PID {got['pid']}），{how}\n"
                    f"用 bg_output(\"{got['id']}\") 看后续输出，bg_stop 收掉它。\n"
                    f"★ 这次运行结束时内核会**自动收掉**所有后台进程，不会留在机器上。")
            return ToolResult(True, summary=f"后台进程 {got['id']} 已启动（PID {got['pid']}）",
                              output=head + ("\n\n--- 启动输出 ---\n" + first if first.strip() else "（还没有输出）"),
                              data={"id": got["id"], "pid": got["pid"], "running": state["running"],
                                    "log": got["log"]})

        def bg_output(args: dict) -> ToolResult:
            bid = str(args.get("id") or "").strip()
            got = self.read(bid, tail_lines=int(args.get("tail_lines") or 200))
            if not got.get("ok"):
                return ToolResult(False, error=got["error"])
            st = got["state"] or {}
            how = "运行中" if st.get("running") else "已退出（退出码 %s）" % st.get("exit_code")
            text = got["text"]
            return ToolResult(True, summary=f"{bid} " + ("运行中" if st.get("running") else "已退出"),
                              output=f"{bid}：{how}｜已跑 {st.get('seconds')}s\n"
                                     + (text.strip() or "（还没有新的输出）"),
                              data=st)

        def bg_stop(args: dict) -> ToolResult:
            bid = str(args.get("id") or "").strip()
            got = self.stop(bid)
            if not got.get("ok"):
                return ToolResult(False, error=got["error"])
            return ToolResult(True, summary=f"{bid} 已停止",
                              output=f"{bid} 已停止（退出码 {got.get('exit_code')}）", data=got)

        def bg_list(args: dict) -> ToolResult:
            rows = [self.info(k) for k in self._procs]
            if not rows:
                return ToolResult(True, summary="没有后台进程", output="（这次运行还没起过后台进程）",
                                  data={"count": 0})
            lines = [f"- {r['id']}｜PID {r['pid']}｜{'运行中' if r['running'] else '已退出'}"
                     f"｜{r['seconds']}s｜{r['cmd'][:80]}" for r in rows]
            return ToolResult(True, summary=f"{len(rows)} 个后台进程（运行中 {len(self._alive())}）",
                              output="\n".join(lines), data={"count": len(rows)})

        return [
            exec_tool("run_background",
                      "在后台起一个**长驻**命令（起服务、跑长任务），不阻塞继续干活。"
                      f"默认等 {DEFAULT_WAIT_MS}ms 把启动输出带回来；最多同时 {MAX_PROCS} 个；"
                      "这次运行结束时会被自动收掉。",
                      {"type": "object", "required": ["command"], "properties": {
                          "command": {"type": "string", "description": "要后台跑的命令"},
                          "label": {"type": "string", "description": "给这次起的东西起个名，便于识别"},
                          "wait_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS,
                                      "description": f"启动后等多久再回报（默认 {DEFAULT_WAIT_MS}）"}}},
                      run_background, preview_keys=("command", "label")),
            read_tool("bg_output", "读某个后台进程**新增的**输出（上次读到哪就从哪继续）。",
                      {"type": "object", "required": ["id"], "properties": {
                          "id": {"type": "string", "description": "run_background 返回的 id"},
                          "tail_lines": {"type": "integer", "minimum": 10, "maximum": 1000}}},
                      bg_output),
            exec_tool("bg_stop", "收掉一个后台进程（连它的子进程一起）。",
                      {"type": "object", "required": ["id"], "properties": {
                          "id": {"type": "string"}}},
                      bg_stop, preview_keys=("id",)),
            read_tool("bg_list", "列出本次运行起过的后台进程。",
                      {"type": "object", "properties": {}}, bg_list),
        ]


def kill_tree(proc) -> None:
    """连子进程一起收掉（Windows 用 taskkill /T；其它平台 kill 进程组）。

    放在 bg 里是为了**只有一份**：shell.py 的 run_command 超时也走它。
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
