# -*- coding: utf-8 -*-
"""执行工具：run_command

约束（全部在权限层与这里双重把关）：
· 黑名单命令**在审批之前**就被拦掉 —— 问了也不给做。
· 超时（默认 120s）后连同**进程树**一起清理（否则会留下后台残留）。
· 输出上限：给你看头尾，全量落 artifacts；长命令的输出**边跑边推**（事件流），不让你干等。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .. import common
from .base import ToolResult, exec_tool
from .bg import kill_tree


def _has_bare_semicolon(cmd: str) -> bool:
    """命令里有没有**引号之外**的裸分号（`echo "a;b"` 不算）。

    为什么只看 `;`：cmd.exe 不认分号而认 `&` / `&&` / `|` / `>`，所以分号是唯一那个
    「在 bash 里最常见、在 cmd 里会静默变形」的分隔符。其余符号两边语义一致，不用管。
    """
    quote = ""
    for ch in str(cmd or ""):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == ";":
            return True
    return False


def build_exec_tools(ws, output_limit: int, artifacts_dir, record_artifact=None, on_line=None):
    artifacts = Path(artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)

    def run_command(args: dict) -> ToolResult:
        cmd = args["command"]
        # ★ 挡住「bash 风格分号」这一条：Windows 上 shell=True 走的是 **cmd.exe**，它不认 `;`，
        #   会把分号后面的东西当成前一个程序的**参数**。实测后果非常隐蔽：
        #   模型写 `javac -version 2>&1; java -version 2>&1` → javac 收到一堆乱参数 → 进程崩溃
        #   （退出码 3221226505 = STATUS_STACK_BUFFER_OVERRUN），模型以为"环境坏了"，
        #   接着连花 7 步去排查 Java 环境，最后什么都没做成。
        #   与其让它拿到一个看不懂的崩溃码，不如**直接拒绝并说清怎么改**（代码级约束 > 提示词请求）。
        if os.name == "nt":
            if _has_bare_semicolon(cmd):
                return ToolResult(False, error=(
                    "没执行：这条命令里有**裸分号**，而 Windows 下 run_command 走的是 cmd.exe，"
                    "它不认 `;` 分隔（会把分号后面的内容当成前一个程序的参数，可能直接把进程跑崩）。\n"
                    "改法：多条命令用 `&&`（前一条成功才继续）或 `&`（无条件继续）连接；"
                    "要「不管成败都跑」就写 `a & b`。\n"
                    "例如：`javac -version && java -version`，不要写 `javac -version; java -version`。"))
        timeout = int(args.get("timeout") or ws.cfg.budget.get("command_timeout")
                      or common.MAX_TOOL_RUNTIME)
        timeout = max(1, min(timeout, 600))
        t0 = time.time()
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = subprocess.Popen(cmd, shell=True, cwd=str(ws.root), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", env=env, bufsize=1,
                                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                                   if os.name == "nt" else 0))
        except Exception as e:
            return ToolResult(False, error=f"启动失败：{type(e).__name__}: {e}")

        chunks, timed_out = [], False

        def reader():
            try:
                for line in proc.stdout:
                    chunks.append(line)
                    if on_line:
                        try:
                            on_line(line.rstrip("\n"))
                        except Exception:
                            pass
            except Exception:
                pass

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        th.join(timeout=5)
        ms = int((time.time() - t0) * 1000)
        raw = "".join(chunks)
        body, truncated = common.clip(raw.strip() or "（无输出）", output_limit)
        artifact_path = ""
        if truncated or len(raw) > output_limit:
            artifact_path = str(artifacts / f"cmd-{int(time.time())}.log")
            try:
                Path(artifact_path).write_text(raw, encoding="utf-8")
                if record_artifact:
                    record_artifact("command", artifact_path, len(raw), raw[-400:])
            except Exception:
                artifact_path = ""
        code = proc.returncode
        head = f"$ {cmd}\n退出码 {code}" + ("（超时被终止）" if timed_out else "") + f"｜{ms}ms"
        return ToolResult(True,                                     # 命令跑起来就是有效观察
                          summary=f"`{cmd[:60]}` → 退出码 {code}" + ("（超时）" if timed_out else ""),
                          output=f"{head}\n{body}",
                          data={"command": cmd, "exit_code": code, "ms": ms, "timed_out": timed_out,
                                "ok_exit": code == 0, "artifact": artifact_path, "raw_size": len(raw)},
                          truncated=truncated or bool(artifact_path))

    return [
        exec_tool("run_command",
                  "在工作区根目录执行一条命令（默认 120s 超时，超时会连子进程一起收掉）。"
                  + ("这里走的是 **cmd.exe**，不是 bash、也不是 PowerShell："
                     "多条命令用 `&&`（前一条成功才继续）或 `&`（无条件继续）连接，"
                     "**不要用分号**（cmd 不认，会把分号后面当成前一个程序的参数，可能直接跑崩）；"
                     "不要用 `ls` / `cat` / `export` / `$VAR` 这类 bash 写法，"
                     "对应的是 `dir` / `type` / `set` / `%VAR%`。"
                     "`2>&1`、`|`、`>` 在 cmd 里照常可用。"
                     if os.name == "nt" else
                     "这里走的是 **sh**：多条命令用 `&&` 或 `;` 连接。"),
                  {"type": "object", "required": ["command"], "properties": {
                      "command": {"type": "string",
                                  "description": "要执行的命令；一次只做一件事，需要几步就分几次调用"},
                      "timeout": {"type": "integer", "minimum": 1, "maximum": 600}},
                   },
                  run_command),
    ]



