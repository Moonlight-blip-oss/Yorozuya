# -*- coding: utf-8 -*-
"""★ 需求 1：TaskClassifier —— 任务复杂度判定（simple / complex）

为什么要单独一层：
  四个人格需要**定位差异**——日常闲聊归银时/神乐/新八，复杂任务归小玉。
  要分流就得先有个稳定、便宜、可解释的判据，而且**不能为了判定去烧主模型 token**。

三级策略（默认只用第一级）：
  · rules  —— 关键词 + 长度 + 结构信号，纯本地、零成本、可解释（默认）
  · llm    —— 用小模型判（配合 `build_llm_prompt` / `parse_llm_reply`），需自己配一个便宜的模型
  · hybrid —— 规则先算分；**只在模糊带（2~3 分）**才去问小模型，兼顾成本与准确率

判定结果会写进上下文（`Result.to_context()`），供后续路由使用。
"""
from __future__ import annotations

import re

LEVEL_SIMPLE = "simple"
LEVEL_COMPLEX = "complex"

# 命中即 +2：工程/代码类词汇
CODE_HINTS = [
    "代码", "函数", "报错", "异常", "堆栈", "栈追踪", "bug", "调试", "debug", "编译", "运行不了",
    "测试", "单元测试", "接口", "api", "数据库", "sql", "查询语句", "正则", "算法", "数据结构",
    "重构", "实现一个", "写一个", "写个", "类库", "依赖", "版本冲突", "环境配置", "部署", "打包",
    "脚本", "变量", "循环", "递归", "性能优化", "内存泄漏", "并发", "线程", "接口文档", "爬虫",
    "python", "javascript", "typescript", "java", "golang", "rust", "html", "css", "json", "yaml",
    "git", "docker", "kubernetes", "linux", "shell", "bash", "powershell", "vue", "react", "fastapi",
    "flask", "django", "mysql", "redis", "numpy", "pandas", "pytorch",
]
# 命中即 +2：多步骤/流程类
STEP_HINTS = [
    "步骤", "分步", "逐步", "流程", "先…再", "先..再", "然后", "接着", "最后再", "一步步",
    "设计一个", "做个方案", "实施方案", "排期", "计划书", "架构", "从零", "搭建", "改造", "迁移",
    "自动化", "批处理", "批量", "集成", "对接", "联调",
]
# 命中即 +2：数据分析/推理类
DATA_HINTS = [
    "分析", "统计", "汇总", "清洗", "建模", "预测", "对比一下", "对比分析", "算一下", "计算一下",
    "数据", "表格", "图表", "可视化", "报表", "excel", "csv", "指标", "增长率", "占比", "排序",
    "逻辑推理", "推导", "证明", "论证", "推演", "多步推理", "博弈",
]
# 命中即 -2：情绪/闲聊类（负权重，避免把「今天写代码好累」误判成任务）
CHAT_HINTS = [
    "陪我", "聊天", "聊聊", "闲", "无聊", "心情", "难过", "开心", "好累", "累了", "困", "晚安",
    "早安", "午安", "吃了", "饿", "天气", "在吗", "你好", "嗨", "哈哈", "嘻嘻", "想你了", "安慰",
    "哄我", "吐槽", "故事", "唱歌", "哄睡", "夸我", "骂我",
]

# 强信号（直接 +3）
RE_CODE_FENCE = re.compile(r"```")
RE_FILE_PATH = re.compile(
    r"[\w\-./\\]+\.(py|pyw|js|mjs|ts|tsx|jsx|json|md|html|htm|css|scss|java|go|rs|c|h|cpp|cs|rb|php|"
    r"sql|yaml|yml|toml|ini|cfg|sh|bat|ps1|txt|log|csv|xlsx|xml)\b", re.I)
RE_COMMAND = re.compile(
    r"\b(npm|pnpm|yarn|pip|pip3|python|python3|node|pytest|git|docker|make|cargo|go|java|mvn|gradle|"
    r"uvicorn|flask\s+run|curl|wget|ssh|scp|rsync|tar|zip|unzip)\s+[-\w./\\]", re.I)
RE_ENUM = re.compile(r"(^|\n)\s*(\d+[.、)]|[①②③④⑤⑥⑦⑧⑨]|[-*]\s+\S)")
RE_TRACE = re.compile(r"(traceback|error:|exception|exit code|退出码|line \d+, in )", re.I)


class Result:
    """一次复杂度判定的结果（可解释：reasons 会展示给用户与写入上下文）。"""

    __slots__ = ("level", "score", "reasons", "source")

    def __init__(self, level: str, score: int, reasons: list, source: str = "rules"):
        self.level = level
        self.score = score
        self.reasons = reasons
        self.source = source

    @property
    def is_complex(self) -> bool:
        return self.level == LEVEL_COMPLEX

    def to_dict(self) -> dict:
        return {"level": self.level, "score": self.score, "reasons": self.reasons,
                "source": self.source}

    def to_context(self) -> str:
        """写进上下文的那一行（需求 1：判定结果写入上下文，供后续路由使用）。"""
        label = "复杂任务" if self.is_complex else "日常/轻量请求"
        why = "、".join(self.reasons) if self.reasons else "无明显工程信号"
        return f"【任务分级】{label}（判定依据：{why}；来源：{self.source}）"

    def __repr__(self) -> str:
        return f"<Result {self.level} score={self.score} {self.reasons}>"


def _hits(text: str, words: list) -> list:
    low = text.lower()
    return [w for w in words if w.lower() in low]


def _code_exts() -> set:
    """代码/配置类后缀（惰性取 yorozuya.files 的那份，取不到就用内置兜底）。"""
    try:
        from ..files import CODE_EXTS
        return CODE_EXTS
    except Exception:
        return {".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".cpp", ".cs", ".rb", ".php",
                ".sh", ".bat", ".ps1", ".sql", ".html", ".css", ".json", ".yaml", ".yml", ".toml"}


def classify(text: str, mode: str = "rules", llm=None,
             attachments: list | None = None) -> Result:
    """判定复杂度。

    mode:
      · "rules"  —— 纯本地规则（默认，零 token）
      · "llm"    —— 完全交给 llm(prompt)->str 判定
      · "hybrid" —— 规则定不了的时候才问 llm
    llm: 可调用的判题函数（接收 prompt 返回文本）；不传则退化为 rules。
    attachments: 本条消息带的附件（[{name, ext, kind}...]）。带了代码/配置文件基本就是在谈活儿，
                 这是很强的信号 —— 于是「上传一个 .py 丢给银时」就能自然触发分流。
    """
    text = (text or "").strip()
    score, reasons = 0, []

    if not text:
        return Result(LEVEL_SIMPLE, 0, ["空输入"])

    # ---- 附件信号：带了代码/配置文件，基本就是在谈活儿 ----
    exts = {str(a.get("ext") or "").lower() for a in (attachments or []) if isinstance(a, dict)}
    if exts:
        score += 1
        reasons.append(f"带 {len(exts)} 个附件")
    if exts & _code_exts():
        score += 2
        reasons.append("附件是代码/配置")

    # ---- 强信号：出现就算工程任务，不再听其它信号解释 ----
    if RE_CODE_FENCE.search(text):
        score += 3
        reasons.append("含代码块")
    if RE_FILE_PATH.search(text):
        score += 3
        reasons.append("提到文件名/扩展名")
    if RE_COMMAND.search(text):
        score += 2
        reasons.append("提到命令行")
    if RE_TRACE.search(text):
        score += 3
        reasons.append("含报错信息")

    # ---- 弱信号 ----
    code = _hits(text, CODE_HINTS)
    if code:
        score += 2 * min(2, len(code))
        reasons.append("工程词：" + "、".join(code[:3]))
    step = _hits(text, STEP_HINTS)
    if step:
        score += 2 * min(2, len(step))
        reasons.append("多步骤：" + "、".join(step[:3]))
    data = _hits(text, DATA_HINTS)
    if data:
        score += 2 * min(2, len(data))
        reasons.append("分析类：" + "、".join(data[:3]))

    # ---- 结构信号 ----
    lines = [x for x in text.splitlines() if x.strip()]
    if len(lines) >= 3 or RE_ENUM.search(text):
        score += 1
        reasons.append("结构化列举")
    if len(text) >= 80:
        score += 1
        reasons.append("长文本")
    if len(text) >= 150:
        score += 1
        reasons.append("超长文本")

    # ---- 情绪/闲聊：负权重（闲聊里提到「代码」不该被判成任务） ----
    chat = _hits(text, CHAT_HINTS)
    if chat:
        score -= 2 * min(2, len(chat))
        reasons.append("闲聊信号：" + "、".join(chat[:3]))
    if re.fullmatch(r"[\s\W]{0,8}", text) or len(text) <= 3:
        score -= 1
        reasons.append("极短输入")

    level = LEVEL_COMPLEX if score >= 3 else LEVEL_SIMPLE
    r = Result(level, score, reasons)

    # ---- 可选：小模型介入 ----
    if mode in ("llm", "hybrid") and llm is not None:
        if mode == "llm" or 2 <= score <= 3:          # hybrid 只在模糊带问
            try:
                out = parse_llm_reply(llm(build_llm_prompt(text)))
                if out in (LEVEL_SIMPLE, LEVEL_COMPLEX):
                    r = Result(out, score, reasons + [f"小模型判定：{out}"], source="hybrid")
            except Exception:
                pass                                          # 问不动就信规则，不阻断主流程
    return r


def build_llm_prompt(text: str) -> str:
    """给小模型的判题提示（只回一个词，尽量省 token）。"""
    return (
        "判定下面这句话属于哪一类，只回答 simple 或 complex，不要任何解释。\n"
        "simple = 日常闲聊、情绪陪伴、简单问答；complex = 写代码、调试、多步骤任务、数据分析、逻辑推理。\n"
        f"用户说：{text[:500]}\n只回答一个词："
    )


def parse_llm_reply(reply: str) -> str:
    low = (reply or "").strip().lower()
    if "complex" in low:
        return LEVEL_COMPLEX
    if "simple" in low:
        return LEVEL_SIMPLE
    return ""


# ---------------- 自测（`python -m yorozuya.agent.classifier`） ----------------

_CASES = [
    ("今天有点累", LEVEL_SIMPLE),
    ("陪我聊会儿天吧，今天被老板骂了", LEVEL_SIMPLE),
    ("你今天心情怎么样呀", LEVEL_SIMPLE),
    ("明天早上八点提醒我吃药", LEVEL_SIMPLE),
    ("我今天写代码写得好累", LEVEL_SIMPLE),
    ("check_calc.py 跑不过，帮我定位并修好", LEVEL_COMPLEX),
    ("帮我写一个 Python 函数，读取 csv 并统计每个月销售额", LEVEL_COMPLEX),
    ("这个报错怎么回事：Traceback (most recent call last): KeyError: 'user'", LEVEL_COMPLEX),
    ("帮我重构一下这个模块，先看依赖关系，再分步改，最后跑测试", LEVEL_COMPLEX),
    ("把这份数据按城市汇总，做个对比分析并输出图表", LEVEL_COMPLEX),
    ("```python\nprint(1)\n``` 这段为什么会报错？", LEVEL_COMPLEX),
]


def _selftest() -> int:
    from . import common
    common.setup_console()
    bad = 0
    print("分级自测：")
    for text, want in _CASES:
        got = classify(text)
        ok = got.level == want
        bad += 0 if ok else 1
        flag = "PASS" if ok else "FAIL"
        print(f"  {flag}  score={got.score:>3}  {got.level:<7} 期望 {want:<7} | {text[:34]}"
              f"  [{'、'.join(got.reasons[:3])}]")
    print(f"\n{len(_CASES) - bad}/{len(_CASES)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
