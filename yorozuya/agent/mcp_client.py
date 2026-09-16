# -*- coding: utf-8 -*-
"""MCP stdio 客户端（M3）：JSON-RPC 2.0 over stdio

只做三件事（也是验收要求的三步）：
  initialize → tools/list → tools/call

协议：每行一个 JSON-RPC 消息（newline-delimited）。子进程用 stdio 双向通信。
安全：任何 MCP 工具调用都记成 exec 风险 —— 它能在本机跑任意东西，
      所以调用路径必须经过主内核的权限/审批闸门，这里只提供「发请求」的原语。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, command: str, args: list | None = None, env: dict | None = None,
                 timeout: int = 60):
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.timeout = timeout
        self.proc = None
        self._q: queue.Queue = queue.Queue()
        self._next_id = 0
        self.server_info: dict = {}
        self.tools: list = []

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        full_env = os.environ.copy()
        if self.env:
            full_env.update(self.env)
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=full_env)
        except FileNotFoundError as e:
            raise MCPError(f"MCP 命令找不到：{self.command}") from e
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        try:
            for line in self.proc.stdout:
                self._q.put(line)
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.close()
                self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    # ---------------- JSON-RPC ----------------

    def _send(self, method: str, params: dict | None = None, timeout: int | None = None) -> dict:
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        try:
            line = self._q.get(timeout=(timeout or self.timeout))
        except queue.Empty:
            raise MCPError(f"MCP 调用超时：{method}（>{timeout or self.timeout}s）")
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise MCPError(f"MCP 返回了非 JSON：{line[:200]}") from e
        if data.get("error"):
            err = data["error"]
            raise MCPError(f"MCP 错误（{method}）：{err.get('message')}（code {err.get('code')}）")
        return data.get("result", {})

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    # ---------------- MCP 三步 ----------------

    def initialize(self) -> dict:
        result = self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "yorozuya-agent", "version": "0.1"},
        })
        self._notify("notifications/initialized")
        self.server_info = result.get("serverInfo") or {}
        return result

    def list_tools(self) -> list:
        result = self._send("tools/list")
        self.tools = result.get("tools") or []
        return self.tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self._send("tools/call", {"name": name, "arguments": arguments or {}})
        content = result.get("content") or []
        # 把 content 里的 text 拼成一段可读输出（其余类型如实标注）
        texts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                texts.append(str(c.get("text") or ""))
            else:
                texts.append(json.dumps(c, ensure_ascii=False))
        return {"result": result, "text": "\n".join(texts), "isError": bool(result.get("isError"))}
