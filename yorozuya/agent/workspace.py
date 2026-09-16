# -*- coding: utf-8 -*-
"""工作区：路径越界防护（jail）+ 约定文件 + 配置

安全要点（都是踩过的坑）：
· 解析后的真实路径必须落在工作区内；挡 `..` 逃逸与符号链接/junction 逃逸。
· **祖先检查只查工作区之内的路径分量** —— 一路走到文件系统根会把工作区的父目录也当成越界，
  结果是「日志一切正常、改动为 0」（这个坑真实发生过）。
· `.git` / `node_modules` 等受保护目录可读不可写。
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import common


@dataclass
class WorkspaceConfig:
    name: str = ""
    test_cmd: str = ""
    verify_cmds: list = field(default_factory=list)      # 多条：全过才算验证通过
    require_verification: bool = True
    allow_prefixes: list = field(default_factory=list)   # 免审批的命令前缀
    deny_patterns: list = field(default_factory=list)    # 追加黑名单
    policy: dict = field(default_factory=dict)           # 工具名 → allow/ask/deny
    budget: dict = field(default_factory=dict)
    instructions: str = ""                               # AGENT.md
    mcp: dict = field(default_factory=dict)              # M3：MCP stdio 服务器（名字 → {command,args,env}）

    def to_dict(self) -> dict:
        return {"name": self.name, "test_cmd": self.test_cmd, "verify_cmds": self.verify_cmds,
                "require_verification": self.require_verification,
                "allow_prefixes": self.allow_prefixes, "deny_patterns": self.deny_patterns,
                "policy": self.policy, "budget": self.budget, "mcp": self.mcp}

    def verify_list(self) -> list:
        """实际要跑的验证命令（verify_cmds 优先，兼容单条 test_cmd）。"""
        cmds = [c for c in (self.verify_cmds or []) if c and c.strip()]
        if not cmds and self.test_cmd:
            cmds = [self.test_cmd]
        return cmds


CONFIG_NAME = "agent.config.json"
AGENT_MD = "AGENT.md"


def python_cmd() -> str:
    """跑 Python 项目的验证命令该用哪个解释器。

    源码运行：用当前解释器的绝对路径（最稳）。
    打包成 exe 后：`sys.executable` 是 Yorozuya.exe，不能拿来跑 .py，退回 PATH 上的 `python`。
    """
    if not getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return "python"


# ★ 自动探测验证命令：**只认得出时才给**，认不出来就返回空列表。
#   为什么宁可返回空：一条猜错的验证命令会让**每一次**运行都以「验证未通过」收场，
#   比"没有验证命令"更糟 —— 用户会以为 agent 把项目改坏了。
#   认不出来的情况由界面上"手动填一条"兜底（工作区卡片里可以直接改）。
def detect_verify_cmds(root) -> list:
    """看项目里有什么工具链，给一条**能真跑**的验证命令（最多两条，全过才算通过）。"""
    root = Path(root)
    if not root.is_dir():
        return []

    def has(*names) -> bool:
        return any((root / n).exists() for n in names)

    def read(name: str) -> str:
        try:
            return (root / name).read_text("utf-8", errors="replace")
        except Exception:
            return ""

    # ① 各语言的"官方测试入口"优先 —— 这是项目作者自己认可的验证方式
    pkg = read("package.json")
    if pkg:
        import json as _json
        try:
            scripts = (_json.loads(pkg).get("scripts") or {})
        except Exception:
            scripts = {}
        t = str(scripts.get("test") or "")
        # npm init 默认塞的那句占位（echo "Error: no test specified" && exit 1）不算测试
        if t and "no test specified" not in t:
            return ["npm test --silent"]

    if has("pom.xml") and has("mvnw", "mvnw.cmd"):
        return ["cmd /c mvnw -q test"] if os.name == "nt" else ["./mvnw -q test"]
    if has("pom.xml"):
        return ["mvn -q test"]

    if has("gradlew.bat", "gradlew"):
        return ["cmd /c gradlew test -q"] if os.name == "nt" else ["./gradlew test -q"]
    if has("build.gradle", "build.gradle.kts"):
        return ["gradle test -q"]

    if has("Cargo.toml"):
        return ["cargo test --quiet"]
    if has("go.mod"):
        return ["go test ./..."]

    if has("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg") and _has_pytest(root):
        return [f"{python_cmd()} -m pytest -q"]
    if (root / "tests").is_dir() or list(root.glob("test_*.py")) or list(root.glob("*_test.py")):
        return [f"{python_cmd()} -m pytest -q"]

    if has("tsconfig.json"):
        return ["npx tsc --noEmit"]

    mk = read("Makefile")
    if mk and re.search(r"(?m)^test\s*:", mk):
        return ["make test"]

    # ② 认不出来 → 空。**不要**瞎猜（例如给一堆散装 .java 猜 `javac *.java`：
    #   本机 java 工具链就未必在 PATH 上，猜错等于让每次运行都"验证未通过"）。
    return []


def _has_pytest(root: Path) -> bool:
    """是不是真的用 pytest（光有 pyproject.toml 不代表有测试）。"""
    if (root / "pytest.ini").exists():
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        text = ""
        try:
            text = (root / name).read_text("utf-8", errors="replace")
        except Exception:
            pass
        if "pytest" in text:
            return True
    return False


DEFAULT_CONFIG = {
    "name": "",
    "test_cmd": "",
    "verify_cmds": [],
    "require_verification": True,
    "allow_prefixes": [],
    "deny_patterns": [],
    "policy": {},
    "budget": {"max_steps": common.MAX_STEPS, "command_timeout": common.MAX_TOOL_RUNTIME},
    # M3：MCP stdio 服务器。留空 = 不注册 mcp_list_tools / mcp_call 两个工具。
    #   形如 {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}}
    "mcp": {},
}

AGENT_MD_TEMPLATE = """# 给 agent 的项目约定

（这个文件会被拼进 agent 的系统提示，优先级高于它自己的习惯）

## 怎么验证改动是好的
- 测试命令：
- 不要动的目录：

## 代码约定
- 语言/风格：
- 禁止：
"""


class Workspace:
    def __init__(self, root: Path, cfg: WorkspaceConfig):
        self.root = Path(root).resolve()
        self.cfg = cfg
        if not cfg.name:
            cfg.name = self.root.name

    # ---------------- 载入 / 保存 ----------------

    @classmethod
    def load(cls, root) -> "Workspace":
        root = Path(root).resolve()
        cfg = WorkspaceConfig(name=root.name)
        f = root / CONFIG_NAME
        if f.exists():
            try:
                data = json.loads(f.read_text("utf-8"))
                for k in DEFAULT_CONFIG:
                    if k in data:
                        setattr(cfg, k, data[k])
            except Exception:
                pass
        md = root / AGENT_MD
        if md.exists():
            try:
                cfg.instructions = md.read_text("utf-8")[:4000]
            except Exception:
                pass
        if not cfg.name:
            cfg.name = root.name
        return cls(root, cfg)

    def save_config(self) -> None:
        f = self.root / CONFIG_NAME
        if not f.exists():
            f.write_text(json.dumps({**DEFAULT_CONFIG, **self.cfg.to_dict()},
                                    ensure_ascii=False, indent=2), "utf-8")
        if not (self.root / AGENT_MD).exists():
            (self.root / AGENT_MD).write_text(AGENT_MD_TEMPLATE, "utf-8")

    # ---------------- 路径 jail ----------------

    def _is_inside(self, p: Path) -> bool:
        try:
            return p == self.root or self.root in p.parents
        except Exception:
            return False

    def resolve_inside(self, rel_or_abs: str) -> Path:
        raw = Path(str(rel_or_abs))
        p = raw if raw.is_absolute() else (self.root / raw)
        resolved = p.resolve()
        if not self._is_inside(resolved):
            raise PermissionError(f"路径越界：{rel_or_abs} 不在工作区内")
        # 只检查「工作区之内」的那些分量是否为重解析点（符号链接/junction），
        # 绝不检查工作区之外的祖先 —— 否则必然误判（见模块注释）。
        parts = resolved.relative_to(self.root).parts
        cur = self.root
        for part in parts:
            cur = cur / part
            if cur.exists() and cur.is_symlink():
                real = Path(os.path.realpath(str(cur)))
                if not self._is_inside(real):
                    raise PermissionError(f"路径经由链接指向工作区外：{rel_or_abs} → {real}")
        return resolved

    def is_protected(self, rel: str) -> bool:
        return any(seg in common.PROTECTED_DIRS for seg in Path(rel).parts)

    def rel(self, p) -> str:
        try:
            return Path(p).resolve().relative_to(self.root).as_posix()
        except Exception:
            return str(p)

    # ---------------- 遍历 ----------------

    def iter_files(self, skip_protected: bool = True):
        for dirpath, dirnames, filenames in os.walk(self.root):
            d = Path(dirpath)
            rel_dir = d.relative_to(self.root)
            if skip_protected:
                dirnames[:] = [x for x in dirnames if x not in common.PROTECTED_DIRS]
            if skip_protected and rel_dir.parts and rel_dir.parts[0] in common.PROTECTED_DIRS:
                continue
            for fn in filenames:
                if skip_protected and fn in ("agent.db",):
                    continue
                yield d / fn

    def manifest(self) -> dict:
        """全量 sha256 清单（验收用：回滚后必须与运行前逐字节一致）。"""
        out = {}
        for p in self.iter_files():
            try:
                out[self.rel(p)] = common.sha256_file(p)
            except Exception:
                pass
        return out
