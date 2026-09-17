# -*- coding: utf-8 -*-
"""通用小工具（时间、ID、星期）。"""
import time
import uuid
from datetime import datetime

# 构建版本号：显示在「设置 → 账号状态」里，用来一眼确认跑的是哪一版。
# ★ 改了 renderer/ 下的任何东西（HTML/JS/CSS/locales）就要**顺手改这一行**，两个理由：
#   ① 后端用它替换 `?v=__BUILD__`，号不变则静态资源的缓存键不变，客户端可能继续吃旧 JS；
#   ② 「设置 → 账号状态 → 客户端版本」是判断"我跑的是不是新包"最快的办法 ——
#      号不变就分不出新旧，2026-09-17 那次"改了没生效"的困惑就是这么来的。
# 格式：YYYY-MM-DD.HHMM（打包/改前端那一刻的本机时间，人工维护，不是自动生成）。
BUILD_STAMP = "2026-09-17.1217"

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# ★ 当前模型会不会输出思考过程（reasoning_content）？
#   为什么需要它：设置页有个「显示思考过程」开关，但它**只影响显示** ——
#   模型本身不吐思考时，用户打开开关也什么都看不到（"我开了却没效果"= 静默失效）。
#   真实案例：她用的 `deepseek-chat` 一个字的思考都不返回（连 enable_thinking 参数都被静默忽略），
#   换 `deepseek-reasoner` 立刻有 169 字。所以要在界面上**如实说明**，而不是让她自己猜。
#   判据两条，任一满足即算"会"：
#     ① 实测见过 —— 这个模型真的吐过思考（server 收到 think 时记进 stats.thinkSeenModel）
#     ② 名字像推理模型 —— 只认高置信的写法；认不出来就当"不会"，由 ① 兜底纠正
_THINK_MODEL_HINTS = ("reason", "qwq", "-r1", "r1-", "thinking", "-think", "o1-", "o3-", "o4-",
                      "glm-z1")


def model_can_think(model, seen_model=""):
    m = (model or "").strip().lower()
    if not m:
        return False
    if seen_model and seen_model.strip().lower() == m:
        return True                      # 实测见过 = 最硬的证据
    return any(k in m for k in _THINK_MODEL_HINTS)


def now_ms():
    return int(time.time() * 1000)


def uid():
    return uuid.uuid4().hex[:12]


def today_key():
    return datetime.now().strftime("%Y-%m-%d")


def fmt_dt(ms):
    if not ms:
        return ""
    d = datetime.fromtimestamp(ms / 1000)
    return f"{d.month}月{d.day}日 {d.hour:02d}:{d.minute:02d}"
