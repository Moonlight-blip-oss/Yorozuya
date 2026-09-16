# -*- coding: utf-8 -*-
"""agent 内核 · 常量与基础工具

设计约定（很重要，改代码前先读）：
· 本包**不 import server / db**：数据库配置直接读 `db_config.json`，这样内核可独立测试与复用。
· 内核数据（库表、快照 blobs、大输出 artifacts）一律放在**工作区之外**，
  否则 `*.db-wal`、`blobs/` 会污染「回滚后与运行前一致」这类断言。
· 工具输出永远只是**数据**，不具备指令地位（防 Prompt Injection）。
"""
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

from .. import paths        # ★ 落盘路径的唯一来源：<项目根>/appdata（见 yorozuya/paths.py）

# 程序落脚目录：源码 = 项目根；打包 = exe 同目录。
# ★ 只用来找**挨着程序放的东西**（如 vendor/rg.exe），不作为落盘数据目录。
BASE_DIR = paths.app_dir()
AGENT_DATA = paths.agent_data_dir()                     # <数据根>/zhiban-data/agent
BLOBS_DIR = AGENT_DATA / "blobs"                        # 内容寻址快照
ARTIFACTS_DIR = AGENT_DATA / "artifacts"                # 被截断的大输出

BUILD_STAMP = "2026-09-16.9"

MAX_TOOL_OUTPUT = 8000          # 单次工具输出进入上下文的字符上限
MAX_TOOL_RUNTIME = 120          # 命令默认超时（秒）
MAX_STEPS = 24                  # 单次运行步数上限
MAX_CHARS = 0                   # 上下文预算（字符）；**0 = 不限**（不压缩、不因此停止运行）
                                #   ★ 她明确要求"不限制小玉上下文长度" —— 别擅自改回一个具体数字。
                                #   真的撞到模型自身的上下文窗口时，会以 API 报错的形式暴露出来，
                                #   那时内核会给一句能看懂的提示（见 kernel 的错误分支）。
                                #   想恢复限制：在工作区的 agent.config.json 里写 "budget": {"max_chars": 60000}
MAX_SECONDS = 900               # 单次运行墙钟上限


class Ev:
    """事件类型（事件流是前端唯一的数据来源）"""
    RUN_START = "run.start"
    TEXT_DELTA = "text.delta"
    THOUGHT_DELTA = "thought.delta"
    PLAN_UPDATE = "plan.update"
    TOOL_CALL = "tool.call"
    TOOL_APPROVAL = "tool.approval"
    TOOL_RESULT = "tool.result"
    TOOL_OUTPUT = "tool.output"      # 命令的流式输出（长命令边跑边看）
    FS_DIFF = "fs.diff"
    NOTICE = "notice"
    VERIFY = "verify"
    RUN_SUMMARY = "run.summary"
    RUN_END = "run.end"


# 风险等级 → 权限档
RISK_READ = "read"
RISK_WRITE = "write"
RISK_EXEC = "exec"

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# 命令黑名单：**在审批之前就拦掉**（问了也不给做）
DENY_COMMAND_PATTERNS = [
    (r"\brm\s+(-[a-z]*\s+)*(-rf|-fr|--recursive)\s+(/|~|\*|[a-z]:\\)", "递归删除根/家目录"),
    (r"\brmdir\s+/s\b", "递归删目录"),
    (r"\bdel\s+/[sfq]\b", "强制删除"),
    (r"\bformat\s+[a-z]:", "格式化磁盘"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdiskpart\b", "磁盘分区工具"),
    (r"\bvssadmin\b", "卷影/备份管理"),
    (r"\bbcdedit\b", "引导配置"),
    (r"\bcipher\s+/w\b", "擦除空闲空间"),
    (r"\bshutdown\b", "关机"),
    (r"\b(reboot|halt|poweroff)\b", "重启"),
    (r"\breg\s+(add|delete|import|restore)\b", "改注册表"),
    (r"\bregedit\b", "注册表编辑器"),
    (r"\bnet\s+user\b.*\s/add\b", "新增系统账号"),
    (r"\btaskkill\b.*\s/im\s+(explorer|csrss|winlogon|services|lsass)\b", "杀关键系统进程"),
    (r":\(\)\s*\{.*\};\s*:", "fork 炸弹"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "放开根目录权限"),
    (r">\s*/dev/(sd|nvme|hd)", "直写块设备"),
    (r"\bgit\s+push\b", "推送远端（内核永不动 remote）"),
    (r"\bnpm\s+(publish|unpublish)\b", "发布包"),
    (r"\bpip\s+install\s+.*--target\s+[a-z]:\\(windows|program files)", "污染系统目录"),
]

# 工作台自己的留档目录名（每个跑完的任务一份，见 taskfolder.py）。
# 放在这里是为了让它同时进 PROTECTED_DIRS —— 见下面的说明。
TASK_DIR_NAME = ".yorozuya"

# 受保护的路径片段（可读不可写）
PROTECTED_DIRS = (".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
                  # ★ 任务留档目录：agent **看得到但改不了/删不掉**（写与删除直接被拒），
                  #   而且 list_dir/grep/glob 不会把它当成项目文件（不污染它自己的工作视图）。
                  #   留档是我们自己写的，不该让下一次运行反过来改它的历史。
                  TASK_DIR_NAME)


def db_config() -> dict:
    """读取 MySQL 配置（与 db.py 同一份文件；支持环境变量覆盖，便于测试隔离）。"""
    cfg = {"host": "127.0.0.1", "port": 3306, "user": "root", "password": "", "database": "zhiban"}
    f = paths.config_file()          # 与 db.py 同一份：<项目根>/db_config.json
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text("utf-8")))
        except Exception:
            pass
    env = os.environ
    if env.get("ZHIBAN_DB_NAME"):
        cfg["database"] = env["ZHIBAN_DB_NAME"]
    if env.get("ZHIBAN_DB_HOST"):
        cfg["host"] = env["ZHIBAN_DB_HOST"]
    if env.get("ZHIBAN_DB_USER"):
        cfg["user"] = env["ZHIBAN_DB_USER"]
    if env.get("ZHIBAN_DB_PASS"):
        cfg["password"] = env["ZHIBAN_DB_PASS"]
    if env.get("ZHIBAN_DB_PORT"):
        try:
            cfg["port"] = int(env["ZHIBAN_DB_PORT"])
        except ValueError:
            pass
    return cfg


def ensure_dirs() -> None:
    for d in (AGENT_DATA, BLOBS_DIR, ARTIFACTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def new_id(n: int = 12) -> str:
    return uuid.uuid4().hex[:n]


def clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> tuple:
    """截断过长输出：返回 (正文, 是否被截断)。中间保留，头尾都给你看。"""
    text = text or ""
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.6)
    tail = limit - head
    return text[:head] + f"\n\n…（此处省略 {len(text) - limit} 个字符，完整内容见 artifacts）…\n\n" + text[-tail:], True


def read_text(path) -> str:
    """宽容读取文本（UTF-8 优先，退回 GBK）。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def setup_console() -> None:
    """Windows 控制台按 UTF-8 输出，避免 GBK 报错。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def redact(text: str) -> str:
    """事件流与导出报告统一脱敏：密钥不出现在任何可读产物里。"""
    text = text or ""
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", "sk-***", text)
    text = re.sub(r"(\"api_key\"\s*:\s*\")[^\"]+", r"\1***", text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", r"\1***", text)
    return text
