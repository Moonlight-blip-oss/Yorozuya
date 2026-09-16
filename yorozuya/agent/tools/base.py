# -*- coding: utf-8 -*-
"""工具基类：注册表 + 参数校验 + 统一返回

工具设计铁则：
· 参数用 JSON Schema 显式声明，**执行前**校验（模型传错参数要在调用前拒绝，而不是执行到一半崩）。
· 输出必须可截断：给你看的部分（头尾）与机器留存的部分（artifacts）分开。
· 工具的输出永远只是「数据」——内核回填上下文时会包上类型标签，永不具备指令地位。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolResult:
    ok: bool
    summary: str = ""
    output: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""
    truncated: bool = False

    def to_context(self, name: str) -> str:
        """回填进上下文的样子：带类型标签的数据区。"""
        head = f"[{'成功' if self.ok else '失败'}] {self.summary}".strip()
        body = self.output or (self.error or "")
        return (f"<<<TOOL_OUTPUT name={name}>>>\n{head}\n{body}\n<<<END_TOOL_OUTPUT>>>")


@dataclass
class Tool:
    name: str
    desc: str
    params: dict                 # JSON Schema
    risk: str                    # read / write / exec
    fn: Callable[[dict], ToolResult]
    preview_keys: tuple = ()     # 审批卡片里展示哪几个参数
    # 「这次会动哪些路径」——写前快照、路径 jail、改动清单都靠它。
    # ★ 默认实现认 path/src/dst/paths/files 这几个常见键；多文件工具（apply_patch 等）自己传一个。
    #   为什么要有它：内核原来的写前快照只看 `args["path"]`，多文件工具会**漏拍快照**
    #   → 回滚回来一半、`_touched` 也少记，验证闸门跟着失真。
    paths_fn: Callable[[dict], list] | None = None

    def paths(self, args: dict) -> list:
        """这次调用会涉及的工作区相对路径（去重、保序）。"""
        if self.paths_fn:
            try:
                return [str(x) for x in (self.paths_fn(args or {}) or []) if x]
            except Exception:
                return []
        out = []
        for key in ("path", "src", "dst", "from_path", "to_path"):
            v = (args or {}).get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        for key in ("paths", "files"):
            v = (args or {}).get(key)
            if isinstance(v, list):
                out += [str(x).strip() for x in v if isinstance(x, str) and x.strip()]
        seen, uniq = set(), []
        for x in out:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    def preview(self, args: dict) -> str:
        keys = self.preview_keys or tuple(self.params.get("properties", {}).keys())
        lines = [f"{k} = {args.get(k)!r}" for k in keys if k in args]
        return "\n".join(lines)[:1500]

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.desc,
                                                 "parameters": self.params}}


class Registry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list:
        return sorted(self._tools)

    def specs(self) -> list:
        return [self._tools[n].spec() for n in self.names()]

    def subset(self, keep: set) -> "Registry":
        r = Registry()
        for n in self.names():
            if n in keep:
                r.register(self._tools[n])
        return r


# ---------------- 轻量 JSON Schema 校验（不引第三方依赖） ----------------

_TYPE_MAP = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "array": list, "object": dict}


def validate_args(schema: dict, args) -> tuple:
    """返回 (ok, 错误说明)。只支持用到的子集：type / required / properties / enum / items。"""
    if not isinstance(args, dict):
        return False, "参数必须是 JSON 对象"
    if not schema:
        return True, ""
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args or args[key] in (None, ""):
            return False, f"缺少必填参数 `{key}`"
    for key, val in args.items():
        spec = props.get(key)
        if spec is None:
            continue                                   # 未声明的一律忽略，避免模型瞎传导致崩
        want = spec.get("type")
        py = _TYPE_MAP.get(want)
        if py and not isinstance(val, py):
            if want == "integer" and isinstance(val, str) and val.strip().lstrip("-").isdigit():
                continue
            if want == "number" and isinstance(val, str):
                continue
            return False, f"参数 `{key}` 应为 {want}，实际是 {type(val).__name__}"
        if spec.get("enum") and val not in spec["enum"]:
            return False, f"参数 `{key}` 只能是 {spec['enum']} 之一"
        if want == "integer" and isinstance(val, int):
            if "minimum" in spec and val < spec["minimum"]:
                return False, f"参数 `{key}` 不能小于 {spec['minimum']}"
            if "maximum" in spec and val > spec["maximum"]:
                return False, f"参数 `{key}` 不能大于 {spec['maximum']}"
    return True, ""


def read_tool(name: str, desc: str, params: dict, fn, paths_fn=None) -> Tool:
    from . import common
    return Tool(name, desc, params, common.RISK_READ, fn, paths_fn=paths_fn)


def write_tool(name: str, desc: str, params: dict, fn, preview_keys=("path",), paths_fn=None) -> Tool:
    from . import common
    return Tool(name, desc, params, common.RISK_WRITE, fn, preview_keys=preview_keys,
                paths_fn=paths_fn)


def exec_tool(name: str, desc: str, params: dict, fn, preview_keys=("command",), paths_fn=None) -> Tool:
    from . import common
    return Tool(name, desc, params, common.RISK_EXEC, fn, preview_keys=preview_keys,
                paths_fn=paths_fn)
