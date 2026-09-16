# -*- coding: utf-8 -*-
"""模型适配层：OpenAI 兼容（同步）+ 脚本化假模型（验收用）

为什么用同步：内核是同步的（工具本身就是同步的：读文件、跑命令），
服务层用一个工作线程跑内核、把事件推给 SSE，比到处 await 更好调试，也更好写验收脚本。

沿用 Yoruzuya 已验证的那套协议：`/chat/completions` + 流式 tool_calls + reasoning_content。
"""
from __future__ import annotations

import json
import time

import httpx

DEFAULT_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class ModelResponse:
    __slots__ = ("text", "tool_calls", "tokens_in", "tokens_out", "raw")

    def __init__(self, text: str = "", tool_calls: list | None = None,
                 tokens_in: int = 0, tokens_out: int = 0, raw=None):
        self.text = text or ""
        self.tool_calls = tool_calls or []
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.raw = raw


class OpenAIModel:
    """真实模型（OpenAI 兼容）。"""

    def __init__(self, api_url: str, api_key: str, model: str, temperature: float = 0.2,
                 thinking: bool = False, timeout: int = 180):
        self.api_url = (api_url or DEFAULT_URL).rstrip("/")
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature
        self.thinking = thinking
        self.timeout = timeout
        self.name = f"openai:{self.model}"

    def complete(self, messages: list, tools: list | None = None) -> ModelResponse:
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature,
                   "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        last = None
        for attempt in range(3):                        # 429/5xx 退避重试
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(self.api_url + "/chat/completions", headers=headers, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    return self._parse(data)
                last = f"API {r.status_code}: {r.text[:200]}"
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(last)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"模型调用失败：{last}")

    def _parse(self, data: dict) -> ModelResponse:
        ch = (data.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {"__raw__": fn.get("arguments")}
            calls.append({"id": c.get("id") or "", "name": fn.get("name") or "",
                          "args": args, "arguments": fn.get("arguments") or "{}"})
        usage = data.get("usage") or {}
        return ModelResponse(text=msg.get("content") or "", tool_calls=calls,
                             tokens_in=int(usage.get("prompt_tokens") or 0),
                             tokens_out=int(usage.get("completion_tokens") or 0), raw=data)


class ScriptedModel:
    """脚本化假模型：按步数返回预定动作。

    验收用的是**确定性**模型，才能把「内核行为」和「模型表现」分开测
    （否则测试红了不知道是内核错还是模型乱来）。planner 签名兼容 (messages, step) 与 (step)。
    """

    def __init__(self, planner, name: str = "scripted"):
        self.planner = planner
        self.name = name
        self.step = 0
        self.calls = 0

    def complete(self, messages: list, tools: list | None = None) -> ModelResponse:
        self.calls += 1
        self.step += 1
        try:
            plan = self.planner(messages, self.step)
        except TypeError:
            plan = self.planner(self.step)
        plan = plan or {}
        calls = []
        for i, tc in enumerate(plan.get("tool_calls") or []):
            args = tc.get("args") or {}
            calls.append({"id": tc.get("id") or f"call_{self.step}_{i}", "name": tc["name"],
                          "args": args, "arguments": json.dumps(args, ensure_ascii=False)})
        return ModelResponse(text=plan.get("text") or "", tool_calls=calls,
                             tokens_in=100, tokens_out=50)


class StaticModel:
    """只会说一句话（用于「模型直接给答案」的最小用例）。"""

    def __init__(self, text: str, name: str = "static"):
        self.text = text
        self.name = name

    def complete(self, messages: list, tools: list | None = None) -> ModelResponse:
        return ModelResponse(text=self.text, tokens_in=10, tokens_out=10)


def make_model(api_url: str = "", api_key: str = "", model: str = "", temperature: float = 0.2):
    """按用户配置构造真实模型；没配 Key 时返回 None（由调用方决定怎么降级）。"""
    if not api_key:
        return None
    return OpenAIModel(api_url or DEFAULT_URL, api_key, model or DEFAULT_MODEL, temperature)
