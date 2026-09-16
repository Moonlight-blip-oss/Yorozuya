# -*- coding: utf-8 -*-
"""Yorozuya · Python 核心（FastAPI + MySQL）
人格：坂田银时 | 账号登录注册 | 多用户数据隔离 | 长期记忆 · 成长 · 待办提醒 · SSE 流式对话
"""
import asyncio
import json
import os
import random
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db as _dbmod

# ★ 本文件在 app/ 子目录里，而 renderer/ 与 yorozuya/ 都在**项目根**：
#   源码运行时要把项目根挂上 sys.path（单独 `python app/server.py` 调试时也需要）。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from yorozuya import paths        # noqa: E402  （落盘路径一律走唯一数据根 appdata/）

if getattr(sys, "frozen", False):
    # 打包成 exe 后：renderer 由 PyInstaller 解压到 _MEIPASS
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE_DIR = _ROOT


class _GuestGuardDB:
    """数据库写操作守卫。

    访客（user_id 形如 `guest:xxx`）的写操作全部静默跳过——
    会话期内数据在内存里正常可用，但一个字节都不会落库，也就没有长期记忆。
    """
    WRITE_FUNCS = {
        "save_settings", "save_stats", "insert_chat", "clear_chats",
        "add_memory", "update_memory", "delete_memory",
        "add_todo", "update_todo", "delete_todo", "replace_user_data",
    }

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        target = getattr(self._real, name)
        if name not in self.WRITE_FUNCS:
            return target

        def guarded(*args, **kwargs):
            uid = args[0] if args else kwargs.get("user_id")
            if is_guest(uid):
                return None
            return target(*args, **kwargs)

        return guarded


db = _GuestGuardDB(_dbmod)

RENDERER_DIR = BASE_DIR / "renderer"

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

from yorozuya.personas import (  # noqa: E402
    PERSONAS, DEFAULT_PERSONA_ID, DEFAULT_THEME, THEMES,
    persona_of, apply_persona, persona_list, theme_of, persona_guard,
    world_roster, WORLD_RULE,          # ★ 银魂世界观人物表（所有人格共用的事实源）
)
from yorozuya.achievements import (  # noqa: E402
    MAX_LEVEL, STAGES, STAGE_DESCS, BADGES, BADGE_MAP, RARITY_LABEL,
    BADGE_GROUPS, MAX_POP_BADGES, badge_view, sync_badges, build_badges,
    days_together, streak_days, get_xp, xp_for_level, get_level, get_stage,
    growth_view,
)
from yorozuya.views import state_view  # noqa: E402
# 对话附件：上传落盘 + 抽正文 + 拼上下文（零新依赖：不收 multipart，直接收原始字节）
from yorozuya import files as yfiles  # noqa: E402
# ★ 工程内核（agent 层）：人格任务分流 + Agent Loop。
#   注意接入方式：陪伴路径（/api/chat 等）不依赖它，只在需要的地方调用；新增能力都走 /api/agent/*。
from yorozuya.agent import classifier as ag_classifier  # noqa: E402
from yorozuya.agent import common as ag_common  # noqa: E402
from yorozuya.agent import demo as ag_demo  # noqa: E402
from yorozuya.agent import gitinfo as ag_gitinfo  # noqa: E402
from yorozuya.agent import model as ag_model  # noqa: E402
from yorozuya.agent import report as ag_report  # noqa: E402
from yorozuya.agent import router as ag_router  # noqa: E402
from yorozuya.agent import snapshot as ag_snapshot  # noqa: E402
from yorozuya.agent import storage as ag_storage  # noqa: E402
from yorozuya.agent import taskfolder as ag_taskfolder  # noqa: E402
from yorozuya.agent.kernel import Kernel as AgKernel  # noqa: E402
from yorozuya.agent import workspace as ag_workspace  # noqa: E402
from yorozuya.agent.tools import weather as ag_tools_weather  # noqa: E402
from yorozuya.agent.tools import web as ag_tools_web  # noqa: E402
from yorozuya.agent.workspace import Workspace as AgWorkspace  # noqa: E402
import yorozuya.achievements as _yach  # noqa: E402
_yach.DB = db  # 把数据库守卫实例注入成长模块，使其能落库里程碑

# ---------------- 会话（历史对话）----------------
# 设计：s["chats"] 仍是「这个用户的所有消息」，每条带 conv 归属；
# 对外只暴露「当前会话」的消息，切会话就是换 settings.convId。
# 这样迁移最轻（老消息只要打上 conv 标记），也让历史过滤逻辑保持单一入口。

def new_conversation(s, title=""):
    """建一条空会话并切过去（只改内存；需要落库用 ensure_conversation）。"""
    now = now_ms()
    c = {"id": uid(), "title": title, "createdAt": now, "updatedAt": now}
    s.setdefault("conversations", []).insert(0, c)
    s["settings"]["convId"] = c["id"]
    return c


def ensure_conversation(s, user_id):
    """取当前会话 id；确实没有就建一条**并落库**。
    ⚠️ 必须落库：否则消息带着 conv 写进去、会话表里却没有这条记录，
    重新加载后那些消息会因为找不到归属会话而整段消失。"""
    cid = (s["settings"].get("convId") or "").strip()
    if cid and any(c["id"] == cid for c in s.get("conversations") or []):
        return cid
    c = new_conversation(s)
    if not is_guest(user_id):
        db.insert_conversation(user_id, c)
    return c["id"]


def chats_of(s, conv_id, limit=None):
    """某条会话里的消息（时间正序）。"""
    out = [m for m in s.get("chats") or [] if (m.get("conv") or "") == conv_id]
    return out[-limit:] if limit else out


def touch_conversation(s, conv_id, user_id, title_from=None):
    """刷新会话的最后活动时间；标题还空着就用首条用户消息兜一个。"""
    c = next((x for x in (s.get("conversations") or []) if x["id"] == conv_id), None)
    if not c:
        return
    c["updatedAt"] = now_ms()
    if not (c.get("title") or "").strip():
        src = title_from or next((m.get("text") or "" for m in chats_of(s, conv_id)
                                  if m.get("role") == "user"), "")
        c["title"] = (src or "").strip().replace("\n", " ")[:22] or "新对话"
    if not is_guest(user_id):
        db.update_conversation(user_id, c)


def greet_if_needed(s, user_id, pid=None):
    """某位成员首次登场时留一句开场白（每个人格只打一次招呼，避免刷屏）。"""
    pid = pid or (s["settings"].get("personaId") or DEFAULT_PERSONA_ID)
    p = PERSONAS.get(pid)
    if not p or not p.get("greeting"):
        return False
    greeted = s["stats"].get("greeted")
    if not isinstance(greeted, dict):
        greeted = s["stats"]["greeted"] = {}
    if pid in greeted:
        return False
    msg = {"id": uid(), "ts": now_ms(), "role": "ai", "greet": True,
           "pid": pid, "conv": ensure_conversation(s, user_id),
           "text": random.choice(p["greeting"])}
    s["chats"].append(msg)
    greeted[pid] = now_ms()
    db.insert_chat(user_id, msg)
    db.save_stats(user_id, s["stats"])
    return True


# ---------------- 「已回复」标记：把「上下文注入」与「生成触发」分开 ----------------
# 核心矛盾：LLM 需要感知历史才能延续话题，但历史消息绝不能再触发一次新的生成
# （否则切个人格，AI 就会把历史里的问题再答一遍）。
# 解法：历史消息只作为**只读上下文**拼进 messages（见 history_for_persona）；
#       生成只由「用户刚刚发出的那一条」触发，并且每条用户消息带 answered 标记，
#       指针 lastProcessedId 落库，刷新后也认。
def mark_answered(s, user_id, msg_id, ptr=None):
    """把这条用户消息标成「已回复」，并把两个指针分别落库：

    - lastProcessedId：**服务端消息 id**，刷新后 `sync_answered_flags` 按它恢复内存态；
    - lastClientId：前端给的幂等键（msgId），同一条消息重试时据此直接 409 拒绝。
    两个不能混用一个值：clientId 是前端造的，跟落库消息的 id 对不上，恢复态就会失效。
    """
    for m in s["chats"]:
        if m.get("id") == msg_id:
            m["answered"] = True
    s["stats"]["lastProcessedId"] = msg_id
    if ptr:
        s["stats"]["lastClientId"] = ptr
    if not is_guest(user_id):
        db.save_stats(user_id, s["stats"])


def mark_history_answered(s, user_id):
    """切换人格：历史里**所有**还没标 answered 的用户消息，
    一律视作「前一位成员已经处理过」。

    切换动作本身不生成任何内容（只换 systemPrompt + 刷新 UI），
    所以这些消息之后绝不能变成新一轮的触发源。
    """
    last = ""
    for m in s["chats"]:
        if m.get("role") == "user" and not m.get("answered"):
            m["answered"] = True
            last = m.get("id") or last
    if last:
        s["stats"]["lastProcessedId"] = last
        if not is_guest(user_id):
            db.save_stats(user_id, s["stats"])
    return last


def sync_answered_flags(s):
    """加载时按落库的指针，把「已回复」这个状态补回内存里的消息对象。

    指针及之前的算已处理；指针之后的（比如上次回复中断留下的）保持「未回答」。
    """
    last = s["stats"].get("lastProcessedId")
    if not last:
        return
    idx = next((i for i, m in enumerate(s["chats"]) if m.get("id") == last), None)
    if idx is None:
        return
    for m in s["chats"][:idx + 1]:
        if m.get("role") == "user":
            m["answered"] = True
    for m in s["chats"][idx + 1:]:
        if m.get("role") == "user":
            m["answered"] = False


DEFAULTS = {
    "settings": {
        "apiUrl": "https://api.openai.com/v1",
        "apiKey": "",
        "model": "gpt-4o-mini",
        "personaId": DEFAULT_PERSONA_ID,
        "personaName": "银时",
        "userName": "",
        "personaPrompt": PERSONAS[DEFAULT_PERSONA_ID]["prompt"],
        "personaEmoji": PERSONAS[DEFAULT_PERSONA_ID]["emoji"],
        "personaTag": PERSONAS[DEFAULT_PERSONA_ID]["tag"],
        "avatar": "",       # 自定义头像（base64 dataURL，空 = 用默认）
        "autoExtract": True,
        "toolsEnabled": True,
        "showThink": True,
        "uiMode": "auto",         # 界面明暗：auto(跟随角色) / light / dark / system(跟随系统)
        "lang": "zh-CN",          # 界面语言
        "convId": "",             # 当前会话（历史对话功能）——为空时由 ensure_conversation 兜底建一条
    },
    "memories": [],   # {id, ts, text, type, pinned}
    "chats": [],      # {id, ts, role, text, conv}
    "conversations": [],  # 会话（历史对话）：{id, title, createdAt, updatedAt}
    "todos": [],      # {id, text, due, done, reminded, createdAt}
    "stats": {"createdAt": None, "totalUser": 0, "totalAI": 0, "days": {}},
}


from yorozuya.common import now_ms, uid, today_key, fmt_dt  # noqa: E402


# ---------------- 会话解析（Bearer Token → 用户） ----------------

def bearer_token(req: Request):
    h = req.headers.get("authorization") or ""
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return ""


GUEST_PREFIX = "guest:"


def is_guest(user_id):
    return isinstance(user_id, str) and user_id.startswith(GUEST_PREFIX)


def auth_user(req: Request):
    """解析身份。返回 {"userId", "username", "guest"}；失败返回 None。

    访客模式：token 形如 `guest:<随机串>`，不进数据库，状态只存在内存里。
    """
    tok = bearer_token(req)
    if tok.startswith(GUEST_PREFIX):
        sid = tok[len(GUEST_PREFIX):].strip()[:64]
        if not sid:
            return None
        return {"userId": GUEST_PREFIX + sid, "username": "访客", "guest": True}
    return db.resolve_token(tok)


def unauthorized():
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def memory_locked():
    """访客模式下记忆功能关闭。"""
    return JSONResponse(
        {"error": "访客模式不会保存记忆。登录后即可让他真正记住你的事。"},
        status_code=403)


# ---------------- 每用户状态（内存缓存 + 写穿 MySQL） ----------------

_cache_lock = threading.Lock()
_users = {}   # user_id -> {"state": dict, "lock": RLock}
MAX_GUESTS = 200   # 访客临时状态上限，超出后淘汰最早的


def _new_state():
    return json.loads(json.dumps(DEFAULTS))


def load_state(user_id):
    # 访客：只存在于内存，不读也不写数据库
    if is_guest(user_id):
        with _cache_lock:
            entry = _users.get(user_id)
            if entry is None:
                fresh = _new_state()
                fresh["stats"]["createdAt"] = now_ms()
                fresh["settings"]["guestMode"] = True
                greet_if_needed(fresh, user_id)   # 访客首次进来也有开场白（仅内存）
                entry = {"state": fresh, "lock": threading.RLock(), "guest": True}
                _users[user_id] = entry
                guests = [k for k in _users if is_guest(k)]
                if len(guests) > MAX_GUESTS:
                    for old in guests[:-MAX_GUESTS]:
                        _users.pop(old, None)
            return entry

    with _cache_lock:
        entry = _users.get(user_id)
        if entry is None:
            data = db.load_user_data(user_id)
            merged = _new_state()
            if data.get("settings"):
                merged["settings"].update(data["settings"])
            apply_persona(merged["settings"])   # 老账号自动补 personaId 并把文案对齐人格库
            for k in ("memories", "chats", "todos", "conversations"):
                if isinstance(data.get(k), list):
                    merged[k] = data[k]
            if isinstance(data.get("stats"), dict):
                merged["stats"].update(data["stats"])
            # 「朗读音色」功能已移除：老数据里可能残留 voice，加载时清掉
            merged["settings"].pop("voice", None)
            # ★ 这里原有一次性迁移「uiMode(light/dark) → auto」，**已删除**，两个原因：
            #   ① 它想做的事已经由 `ui_mode` 列的默认值完成了 —— 那一列是后加的，
            #      老行读出来本来就是 "auto"，没有需要迁的数据；
            #   ② 它现在**有害**：迁移完成标记存在 `stats` 里，而清空数据 / 导入备份会把
            #      stats 一起换掉 → 标记丢失 → 迁移重跑 → **把用户明确选过的 dark/light 改回 auto**。
            #      实测复现：`PUT uiMode=dark` 之后调 `/api/reset`，主题变回 auto。
            if not merged["stats"].get("createdAt"):
                merged["stats"]["createdAt"] = now_ms()
                db.save_stats(user_id, merged["stats"])
            # 历史对话：若已经有会话却没记住"当前是哪一段"（老库补列时不会自动带上），
            # 落到最近活跃的那条，避免进来看到一片空白。
            if merged["conversations"] and not (merged["settings"].get("convId") or "").strip():
                newest = max(merged["conversations"], key=lambda c: c.get("updatedAt") or 0)
                merged["settings"]["convId"] = newest["id"]
                db.save_settings(user_id, merged["settings"])
            if not merged["chats"]:
                greet_if_needed(merged, user_id)   # 空对话的新账号：先打个招呼
            sync_answered_flags(merged)            # 按落库指针恢复「已回复」状态
            entry = {"state": merged, "lock": threading.RLock()}
            _users[user_id] = entry
        return entry


# ---------------- 上次使用的人格（供「开机动画」读取）----------------
# 开机动画在服务/数据库就绪之前就要显示，没法查库，所以这里把当前人格
# 落成一个小文件，desktop.py 启动时直接读。写失败不影响任何功能。
_persona_cache = {"pid": None}


def remember_persona(pid):
    if not pid or pid not in PERSONAS or _persona_cache["pid"] == pid:
        return
    _persona_cache["pid"] = pid
    try:
        paths.persona_file().write_text(
            json.dumps({"personaId": pid}, ensure_ascii=False), "utf-8")
    except Exception:
        pass


# ---------------- 人格隔离（防串台）----------------
# 背景：messages 里的历史若原样回灌，模型会看到「我过去一直这么说话」，
# 于是切到银时后仍沿用神乐的「阿鲁」。system prompt 是对的，是历史把它盖掉了。

# 自称别的角色的句式（加否定后顾，避免「问我是神乐吗」这类误判）
_SELF_ID_BLOCK = r"(?<![问说叫当是这那])"


HISTORY_MARK = ("（以下是早前说过的话 —— 都已经回应过了，只用来理解上下文，"
                "不要再把它们当成这一轮要回答的内容）")

# ★ 工具关闭期间说出去的话，回灌历史时必须标明「那是当时的状态」。
#   为什么：关掉工具那几轮，模型会留下「我查不了 / 工具开关关着」这类话；重新打开工具后，
#   它看到自己刚说过「我查不了」，就**接着这么说**，明明能查也不查（实测：连问三个新城市，
#   一次工具都没调，还继续编温度）。这句标记和系统提示里的【当前状态】是一对，缺一即复发。
TOOLS_OFF_MARK = ("（注意：这句是在「工具开关关着」的时候说的，只在当时成立；"
                  "现在按当前设置来，该查证的事要真的去查）")


def history_for_persona(chats, pid, limit=0):
    """拼给 LLM 的历史：只保留「用户自己的话」+「当前人格自己说过的话」。

    别的角色的发言整条丢弃 —— 既不能被本角色模仿，也不能冒充成本角色说过的话。
    丢弃后会出现「用户连说几句」，合并成一回合，避免 user,user,user 这种畸形序列。

    `limit`：**0 = 不限**（默认）。原先写死 30 条，会把长对话的前半段整段丢掉 ——
    她要求不限制上下文长度，所以改成默认全给；想收紧就在调用处传具体数字。

    ★★ 合并出来的那一坨**必须显式标出来**（HISTORY_MARK）：
      因为"别的角色的发言被丢弃"这件事会**把用户连着的多句话并成一条**，而切人格之后
      这位新人格几乎没有自己的回复，于是她前面问过的一堆问题会并成一整条 user ——
      模型把它读成"这里有好几个问题，挨个答"，于是把几十分钟前的老问题又答一遍。
      真实事故：切到小玉后，她把 41 分钟前的「你对九兵卫怎么看」和当轮的天气一起答了
      （库里那条回复同时说了九兵卫和天气，是这次定位的铁证）。
      光靠"合并"本身没法表达"这些是旧话"，所以要在文本里明说。
    """
    msgs = []
    for m in chats[:-1]:          # 最后一条是本次用户消息，由调用方单独追加
        role = m.get("role")
        text = m.get("text") or ""
        if not text:
            continue
        if role == "user":
            msgs.append(["user", text])
        elif (m.get("pid") or DEFAULT_PERSONA_ID) == pid:
            # 工具关着时说的话要标明「当时的状态」，否则它会拿自己的旧话继续推托
            # （标记放在内容里，后面合并相邻同角色时也会跟着走）
            if m.get("toolsOff"):
                text = TOOLS_OFF_MARK + "\n" + text
            msgs.append(["assistant", text])
        # else: 别的角色说的话 → 丢弃

    merged = []
    for role, content in msgs:
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n" + content
            merged[-1]["n"] += 1
        else:
            merged.append({"role": role, "content": content, "n": 1})

    out = []
    for m in merged:
        content = m["content"].strip()
        # ① 被并起来的一坨（她连说了好几句）—— 最容易被当成"待办清单"
        if m["role"] == "user" and m["n"] > 1:
            content = HISTORY_MARK + "\n" + content
        out.append({"role": m["role"], "content": content})
    # ② 历史**以用户消息结尾**时也要标：那说明这条没被本角色回过（转交 / 上次生成中断），
    #   紧接着就是本次的问题 —— 两条相邻的 user 一样会让模型把前一条也答了。
    if out and out[-1]["role"] == "user" and not out[-1]["content"].startswith(HISTORY_MARK):
        out[-1]["content"] = HISTORY_MARK + "\n" + out[-1]["content"]
    return out[-limit:] if limit else out


def persona_violations(pid, text):
    """检测回复里是否混入了别的角色的特征。返回违规项列表（空 = 干净）。

    触发条件刻意收窄：只用「高精度」标记，宁可漏检也不误伤 ——
    误判会导致无谓的重说（多花一次 token、还多等一轮）。
    """
    if not text:
        return []
    p = PERSONAS.get(pid) or {}
    hits = []
    for w in (p.get("forbiddenWords") or []):
        if w in text:
            hits.append(w)
    for name in (p.get("forbiddenIdentities") or []):
        for pat in (rf"{_SELF_ID_BLOCK}我是{name}", rf"{_SELF_ID_BLOCK}我叫{name}",
                    rf"{_SELF_ID_BLOCK}我就是{name}"):
            if re.search(pat, text):
                hits.append(f"自称「{name}」")
                break
    return hits


def sanitize_persona(pid, text):
    """最后兜底：重说后仍然串台，就把禁令词直接剔掉，绝不把别人的人设交给用户。

    剥离词之后必须顺手收拾残留标点，否则会留下「都能当剑使，？」这种断句。
    """
    if not text:
        return text
    p = PERSONAS.get(pid) or {}
    out = text
    for w in (p.get("forbiddenWords") or []):
        out = out.replace(w, "")
    out = re.sub(r"[，、；：]+(?=[。！？…!?])", "", out)   # 逗号直接顶到句末 → 去掉逗号
    out = re.sub(r"[，、]{2,}", "，", out)
    out = re.sub(r"。。+", "。", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"^[ \t　，、；：]+", "", out, flags=re.M)
    out = re.sub(r"[ \t]+$", "", out, flags=re.M)
    return out.strip()


async def respin_persona(s, convo, pid, hits, temperature=0.9):
    """命中串台后重说一次：在对话尾部追加一条强纠正指令，并压低温度。

    返回新文本；若重说仍然违规或接口失败，返回空串（交给 sanitize 兜底）。
    """
    p = PERSONAS.get(pid) or {}
    tics = "、".join(p.get("tics") or [])
    forbid = "、".join("「" + w + "」" for w in (p.get("forbiddenWords") or []))
    fix = {
        "role": "system",
        "content": (
            f"【纠错重说】你上一次的回答里出现了「{'、'.join(hits)}」，"
            f"那是别的角色的专属特征，属于严重错误。请忘掉它，"
            f"现在重新回答用户最后一条消息，只用「{p['name']}」的语气"
            + (f"（口癖：{tics}）" if tics else "")
            + (f"，绝对不要出现：{forbid}。" if forbid else "。")
            + "直接给回答，不要解释、不要道歉、不要提及这次纠正。"
        ),
    }
    try:
        text, _think, _calls = await llm_once_full(
            s, convo + [fix], tools=None, temperature=min(temperature, 0.72), max_tokens=700)
    except Exception:
        return ""
    text = (text or "").strip()
    if not text:
        return ""
    # 重说仍带痕迹：这次文本整体更新，优先保留它、只清掉残留词，比丢掉重来更好
    if persona_violations(pid, text):
        text = sanitize_persona(pid, text)
        if not text:
            return ""
    return text


# ---------------- 系统提示词 ----------------

def _tool_catalog() -> str:
    """把 TOOL_SPECS 渲染成给模型看的清单（**自动生成，不手写第二份**）。

    为什么要生成：原先这段是手写的工具列表，而 tools 参数走的是 TOOL_SPECS ——
    两处各写一份，加了工具（比如这一轮的天气/搜索）就会「spec 里有、提示词里没有」，
    模型于是不知道自己能查天气，照样回「我查不到」。这种漂移必须从结构上消掉。
    """
    lines = []
    for sp in TOOL_SPECS:
        fn = sp.get("function") or {}
        desc = str(fn.get("description") or "").strip()
        desc = re.split(r"[。\n]", desc)[0].strip()          # 只取首句，够模型判断何时用
        if len(desc) > 66:
            desc = desc[:66] + "…"
        lines.append(f"- {fn.get('name')}：{desc}")
    return "\n".join(lines)


def build_system_prompt(s, task_mode=False, task_line=""):
    """拼系统提示。

    task_mode=True 表示这一轮由工程内核处理（小玉接手复杂任务）：
    ★ 此时**不注入任何陪伴记忆** —— 需求 4 的上下文/记忆隔离，
      结构上由「工程记忆单独一张 agent_memories 表」+ 这里的开关共同保证。
    """
    st = s["settings"]
    apply_persona(st)          # 保证提示词里的人格一定是人格库里那一版
    p = persona_of(st)
    now = datetime.now()
    date_str = f"{now.year}年{now.month}月{now.day}日 {WEEKDAYS[now.weekday()]}"
    mems = sorted(s["memories"], key=lambda m: (not m.get("pinned"), -m["ts"]))[:25]
    mem_lines = "\n".join(
        f"- [{m['type']}]{ '（重要）' if m.get('pinned') else '' } {m['text']}" for m in mems)
    open_todos = [t for t in s["todos"] if not t["done"]][:10]
    todo_lines = "\n".join(
        f"- {t['text']}（{fmt_dt(t['due'])}）" if t.get("due") else f"- {t['text']}" for t in open_todos)

    tics = "、".join(p.get("tics") or [])
    rules = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(p.get("styleRules") or []))
    world = "；".join(p.get("world") or [])
    examples = "\n".join(f"用户：{q}\n你：{a}" for q, a in (p.get("examples") or []))

    parts = [
        f"你现在扮演「{p['name']}」（{p['title']}），用第一人称跟用户 1v1 私聊。"
        f"你必须始终以这个角色的身份、口吻、价值观说话，绝不是通用助手。",
        # ★★ 这一轮只回最后一条 —— 与 history_for_persona 的 HISTORY_MARK 配对。
        #    为什么必须有：历史里「别的角色的发言被丢弃」会把用户连着的多句话并成一条，
        #    切人格之后尤其明显（新人格几乎没有自己的回复）。不写清楚的话，模型会把
        #    那坨老问题当成"这一轮要回答的内容"，挨个答一遍 —— 真实事故见 HISTORY_MARK。
        "【这一轮只回最后一条】对话历史里凡是带「（以下是早前说过的话…）」标记的，"
        "都是**已经回应过的旧话**，只用它们理解上下文（比如她之前说过什么、你在聊什么），"
        "**绝对不要再回答它们、不要复述、不要挨个回应**；你只需要回应用户最后那条没有标记的消息。",
        f"【人物设定】{p['prompt']}",
        f"【口癖】{tics}——必须自然带出来，这是识别这个角色的关键。" if tics else "",
        f"【风格铁律】\n{rules}" if rules else "",
        persona_guard(p),
        # ★ 职责定位：日常闲聊归三人、复杂任务归小玉（需求 2 / 3 的提示词侧约束）
        (f"【你的职责】{p['duty']}" if p.get("duty") else ""),
        # ★ 世界观认知：共享事实表（认得出来、关系不说错）+ 本角色的私交
        #   原来只有一句「可自然提及的同世界观人物」，读起来像"可以提这些人"，
        #   导致没列进去的名字会被反问「那是谁」；而且冷门角色全靠模型自己记，会说错关系。
        (f"【你认识的人（银魂世界观）】{world_roster()}" if world_roster() else ""),
        (f"【你和他们的私交】{world}" if world else ""),
        (WORLD_RULE if world_roster() else ""),
        (f"【语感示例】下面是对这种语感的示范，回答时保持同样的味道，但不要照抄内容：\n{examples}"
         if examples else ""),
        f"用户希望你称呼TA为「{st['userName']}」。" if st.get("userName") else "",
        f"今天是 {date_str}。你与用户已相伴 {days_together(s)} 天，当前亲密度阶段：{get_stage(get_level(s))}。"
        "亲密度越高，你说话可以越随意亲近。",
        # ★★ 工具开关：**两个方向都要写死「当前状态」**，这里踩过两次坑（都是实测出来的）：
        #   ① 关掉工具那几轮，模型不肯说「我查不了」，而是**编数字**（实测：拉萨 13 度、
        #      西宁 13 度，全是凭空造的）—— 软话「请如实说明」压不住它要答上话的本能，
        #      必须明令「一个数字都不许出现」。
        #   ② 打开工具之后，历史里留着它自己说的「我查不了 / 工具开关关着」，
        #      模型会顺着自己的旧话继续推托（实测：重新打开后连问三个新城市，一次工具都没调，
        #      还继续编温度）。所以开着时要明说：那句旧话只是当时的设置所限，与这一轮无关。
        ("你可以调用工具来真正把事情办掉，而不是只口头答应：\n"
         f"{_tool_catalog()}\n"
         "当用户要求记住、提醒、安排、查询时，直接调用工具，然后再用角色的口吻自然回应。"
         "不要把工具名、JSON 或调用过程念给用户听；也不要声称「我记住了」却没真的调用工具。\n"
         "★ 关于联网："
         "问「现在/今天/明天」的天气用 weather_now / weather_forecast，"
         "问过去的用 weather_history，问「一般多少度、几月下雨」这种常年规律用 weather_climate；"
         "查你不知道的资料用 web_search（想读全文再 web_fetch 它的链接）；"
         "用户直接给了网址就用 web_fetch。\n"
         "★★ 查不到就**如实说查不到**（可以说清是哪一步没走通，也可以用你的口吻抱怨一句网络），"
         "但**绝对不许编造**：温度、降水、天气现象、新闻、链接，都必须以工具返回的内容为准；"
         "工具没成功时不许给出任何具体数字或结论，更不许拿「常识」当当地的实况。\n"
         "★★【当前状态】工具现在是**开着**的。历史里若我说过「我查不了」「工具开关关着」"
         "「等工具开了再查」，那只是**当时**设置所限，和这一轮无关 —— "
         "只要用户问的是需要查证的事，这一轮就必须真的去调用工具，不要接着念叨那句旧话。"
         if st.get("toolsEnabled", True) else
         "（注意：用户把工具功能关掉了，所以你现在查不了天气、也搜不了资料。"
         "★ 这是**硬禁令**：一个具体数值都不许出现 —— 温度、度数、降水概率、空气质量、"
         "新闻、链接，都不许给，哪怕是「大概」「印象里」「我记得」「按常理」也不行，"
         "那比直接说不知道更糟。"
         "正确做法是照实说：现在「工具」开关是关着的，我拿不到外面的实时数据，"
         "请对方去设置里把「AI 能力 → 允许它调用工具」打开。"
         "§ 历史里我可能报过别的城市或别的日期的具体数字 —— 那是**当时工具开着**查到的，"
         "不代表现在还能拿到数，不许把旧数字换个地方当成本轮答案。）"),
        ("【访客模式】对方这次是没登记的访客（临时委托），这段对话不会被你记住，"
         "你也读不到任何历史记忆。所以不要承诺「我记住了」「下次提醒你」这类话，"
         "可以顺口调侃一句自己记性差、或者催对方登记个名字。"
         if st.get("guestMode") else ""),
        # ★ 工程任务（小玉接手）不注入任何陪伴记忆 —— 需求 4 的上下文/记忆隔离
        ("" if task_mode else
         (f"你记得这些关于用户的事（自然地在合适时机使用，不要机械罗列或一次性复述）：\n{mem_lines}"
          if (mem_lines and not st.get("guestMode")) else
          ("" if st.get("guestMode") else "你目前对用户还没有长期记忆，注意在对话中自然地了解TA。"))),
        task_line or "",
        (f"用户未完成的待办（必要时可以提醒TA）：\n{todo_lines}" if todo_lines else ""),
        (f"最后再强调一次：你是「{p['name']}」，不是别的角色，也不是通用助手。"
         "始终保持上面的口癖与语气，绝不要出现别的角色的口癖。"
         "回复口语化、简短（一般 1-3 句，除非用户要求展开）；"
         "不要输出 Markdown 标题/列表/加粗；动作描写最多一处且要简短。"),
    ]
    return "\n\n".join(x for x in parts if x)


# ---------------- LLM 调用 ----------------

def _api_url(s):
    return s["settings"]["apiUrl"].rstrip("/") + "/chat/completions"


def _headers(s):
    return {"Authorization": "Bearer " + s["settings"]["apiKey"]}


async def llm_once(s, messages, temperature=0.2, max_tokens=300):
    payload = {"model": s["settings"]["model"], "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        r = await client.post(_api_url(s), headers=_headers(s), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"API {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"] or ""


# ---------------- 记忆提取（后台） ----------------

EXTRACT_SYS = (
    "你是一个记忆提取器。输入只有【用户】自己说过的话。"
    "从中提取值得 AI 长期记住的、关于**用户本人**的信息（事实、偏好、重要事件、计划安排）。"
    "只提取用户明确说出的信息，不要推测、不要补全、不要替用户下结论。"
    "**绝对不要**把角色自己的言行、经历、设定、剧情写进记忆，也不要记录角色的口癖。"
    "以JSON数组输出，每项形如 {\"text\":\"内容\",\"type\":\"事实|偏好|事件|计划\"}。"
    "text用第三人称简洁描述（如\"用户不吃香菜\"）。没有值得记的就输出 []。只输出JSON，不要任何其他文字。"
)


# 入库前的硬过滤：这类内容不可能来自「用户本人说的话」，一律丢弃。
# 与 persona_guard 共用同一份数据（每个角色的 forbiddenWords），保持数据驱动。
_MEM_ALIEN_MARKERS = tuple(sorted({w for p in PERSONAS.values() for w in (p.get("forbiddenWords") or [])}))
_MEM_CHAR_NAMES = tuple(p["name"] for p in PERSONAS.values())


def memory_is_alien(text):
    """判断一条提取结果是否「不可能是用户说的」。

    两类特征：① 含某个角色的高精度口癖标记（阿鲁 / （推眼镜） / 说到底——）；
    ② 以角色名开头（"新八的打印机被神乐拆了" / "神乐有暴食时段"）——那是角色的剧情，不是用户的信息。
    命中即丢弃：宁可少记一条，也不让 AI 的内容污染长期记忆。
    """
    t = (text or "").strip()
    if not t:
        return True
    if any(w in t for w in _MEM_ALIEN_MARKERS):
        return True
    if _MEM_CHAR_NAMES and t.startswith(_MEM_CHAR_NAMES):
        return True
    return False


async def extract_memories(user_id, s, user_text, ai_text=None, *, include_ai_reply=False):
    """把用户的话沉淀成长期记忆。

    ⚠️ 默认**只吃用户自己的话**（`ai_text` 会被忽略）。这条约束是实测踩出来的：
       以前把 AI 回复一起喂进提取器，AI 编造的内容会被写成「用户的事实」——
       复现 3/3：用户只说了"今天有点累"，记忆里却出现"用户在上海做程序员""用户有一台老相机"；
       AI 的反问句也会被当成事实（AI 问"你家楼下有树荫吗" → 落库"用户家楼下可能有树荫"）；
       角色剧情同样会被记下来（"新八的打印机被神乐拆了"）。
       确实需要记录 AI 内容时，调用方必须**显式**传 `include_ai_reply=True`，不提供默认开启的口子。
    """
    if is_guest(user_id):   # 访客没有长期记忆
        return 0
    if not s["settings"].get("autoExtract") or not s["settings"].get("apiKey"):
        return 0
    user_text = (user_text or "").strip()
    if not user_text:       # 没有用户输入就没有可提取的东西
        return 0
    try:
        apply_persona(s["settings"])
        if include_ai_reply and ai_text:
            content = f"【用户】{user_text}\n【{s['settings']['personaName']}】{ai_text}"
            source = "assistant"        # 显式开启：这条记忆可能含 AI 内容，如实登记来源
        else:
            content = f"【用户】{user_text}"
            source = "user"
        raw = await llm_once(s, [
            {"role": "system", "content": EXTRACT_SYS},
            {"role": "user", "content": content},
        ])
        js = raw.strip()
        if js.startswith("```"):
            js = js.strip("`")
            if js.lower().startswith("json"):
                js = js[4:]
        arr = json.loads(js.strip())
        if not isinstance(arr, list):
            return 0
        entry = load_state(user_id)
        added = 0
        with entry["lock"]:
            texts = [m["text"] for m in s["memories"]]
            for item in arr[:5]:
                text = str(item.get("text", "")).strip()[:200]
                if not text or any(text in t or t in text for t in texts):
                    continue
                if memory_is_alien(text):      # 防御：角色的口癖 / 角色剧情，绝不入库
                    continue
                mtype = item.get("type") if item.get("type") in ("事实", "偏好", "事件", "计划") else "事实"
                m = {"id": uid(), "ts": now_ms(), "text": text, "type": mtype,
                     "pinned": False, "source": source}
                s["memories"].append(m)
                db.add_memory(user_id, m)
                texts.append(text)
                added += 1
        return added
    except Exception:
        return 0


# ---------------- 演示模式：本地人格化回复（无 API Key 时用） ----------------

def _pick(seq):
    return random.choice(seq)


MOCK_LINES = {
    "gintoki": {
        "tired": [
            "啊——又是这种脸。行吧，先说好，安慰人可是要加钱的……算了，这次免费。说吧，谁又惹你了？",
            "喂喂，别摆出那副死鱼眼好吗，那是我的专属造型。怎么了，说来听听，反正我今晚的JUMP还没到。",
        ],
        "happy": [
            "哦？难得看你这么有精神。行，说说看，让我这个万事屋也沾沾喜气。",
            "哼，这种事不用特地跑来炫耀……不过，看你还挺高兴的，那就多讲两句吧。",
        ],
        "sad": [
            "……过来坐吧。不用说话也行，我正好要泡草莓牛奶，分你一杯。",
            "喂，哭完了没？哭完了就把事情讲给我听。……我啊，在你舍弃百人的时候，已经与千人有了羁绊。所以你这点事，我听着。",
        ],
        "work": [
            "啊——又是这种事。喂——你老板是啃了芥末寿司吗，火气这么大。",
            "我说你啊，实在待不下去就过来，万事屋永远缺个不要工钱的。……房租不会因为你勇敢就消失，先想清楚。",
        ],
        "memory": "知道了知道了，「{fact}」是吧。放心，我记性只在欠房租的时候才不好。到时候会提醒你的。",
        "who": "坂田银时，万事屋老板。接的业务从找人聊天到拯救地球都有——价格嘛，先记账上。",
        "thanks": [
            "啊？就这点小事也要道谢，你的人生是有多客气。……行了，下次请我喝草莓牛奶就行。",
            "嗯。不过别到处说啊，武士也是要面子的。",
        ],
        "here": "在。刚准备开一盒草莓牛奶……算了，你说你的。",
        "money": "提钱伤感情啊喂！……好吧，账本上我确实是红色的那栏。等你发财了记得罩我一下。",
        "default": [
            "嗯——然后呢？",
            "我说你啊，讲重点。……开玩笑的，慢慢讲，我听着。",
            "哦——这种事啊。不要紧，冷静，先找时光机。……行了行了，今天我就当个树洞，聊完谁也不欠谁。",
            "喂，别光说一半啊。吞吞吐吐的，像便秘一样很难看的好吗。",
            "哈？你这家伙，偶尔也挺有意思嘛。继续继续。",
        ],
        "extra": "顺便一提，关于你的事我已经记了 {n} 条，想赖账可没那么容易。",
        "guest_refuse": ("哦——想让我记住？可惜你今天是空着手来的，连名字都没登记。"
                         "我这儿是万事屋，不是便利店寄存柜啊。先注册个账号，我再考虑要不要记。"),
    },
    "kagura": {
        "tired": [
            "咦——你这是什么表情阿鲁。谁欺负你了？我这就去把他家墙拆了。",
            "累了就躺着阿鲁！我陪你一起躺，谁也不许先起来的说。",
        ],
        "happy": [
            "哦哦——看你笑得像捡了钱阿鲁。快说快说，我也要开心一下！",
            "哼，有好事居然不早点叫我阿鲁！",
        ],
        "sad": [
            "喂……谁惹你了？说名字，我现在就去。",
            "别哭了阿鲁。我分你半根醋昆布——只有半根，不许嫌少。",
        ],
        "work": [
            "哈？那种地方还留着干嘛阿鲁！报名字，我现在就去把公司大门踹成两半阿鲁！",
            "被老板骂了不能忍的阿鲁！……不过先吃饭，饿着肚子生气最亏阿鲁。",
        ],
        "memory": "「{fact}」是吧？记下了阿鲁！虽然我脑子主要用来装吃的，但你说的事我会记得。",
        "who": "神乐阿鲁！夜兔族的，力气很大，饭量更大。宇宙最强说的就是我。",
        "thanks": [
            "嘿嘿，不用客气阿鲁！下次请我吃顿饭就行。",
            "嗯？谢什么，朋友之间不用这样阿鲁。",
        ],
        "here": "在的阿鲁！正在吃第五碗饭。你说。",
        "money": "钱？我最喜欢钱阿鲁！可惜我的钱全都变成饭了……都怪那个无业游民不肯去赚钱阿鲁。",
        "default": [
            "嗯嗯，然后呢阿鲁？",
            "原来是这样阿鲁！我懂了（其实没太懂）。",
            "继续说继续说你接着说阿鲁。",
            "哈？这有什么大不了的，我一拳就能解决。",
            "……你等一下，我先把这口醋昆布吞下去阿鲁。",
        ],
        "extra": "对了，你的事我记了 {n} 条阿鲁！记性是不是很好？",
        "guest_refuse": "记住？你今天连名字都没登记阿鲁，我记完就忘了。先去注册个账号吧！",
    },
    "shinpachi": {
        "tired": [
            "辛苦了。（推眼镜）要不要先休息十分钟？工作的事明天再想也不迟。",
            "听起来今天不太好过……阿银遇到这种事会直接躺下装死，但你别学他。先说说？",
        ],
        "happy": [
            "哦哦，那真不错！（推眼镜）难得听你说高兴的事。",
            "太好了，能开心就是好事。……不过说真的，阿银要是有你一半上进，房租早就交上了。",
        ],
        "sad": [
            "那个……我在的。（推眼镜）想说什么都可以说，我不会随便评价。",
            "要是不介意的话，我陪你聊一会儿吧——比跟阿银聊靠谱，他只会说「这事明天再说」。……武士一旦决定要保护的东西，至死都会保护。你现在就是我要护着的那一个（推眼镜）。",
        ],
        "work": [
            "（推眼镜）说到底，被骂不代表你做错了什么。先深呼吸，把事情经过讲一遍，我帮你理理。",
            "我理解，这确实不是一拍脑袋就能决定的事。……阿银欠了那么多房租照样躺着看《JUMP》，你至少比他靠谱。要不先更新一下简历试试水？",
        ],
        "memory": "这个是「{fact}」对吧？我记下来了。……虽然我只是个戴眼镜的，但记事还算靠谱。",
        "who": "我是志村新八，万事屋的成员。虽然阿银总说我存在感只有眼镜……",
        "thanks": [
            "不用谢，这是我应该做的。",
            "别客气。有需要随时找我就行。",
        ],
        "here": "在的。阿银刚出门，神乐在翻冰箱——所以现在只有我这个正常人能回应你（推眼镜）。",
        "money": "说到钱……阿银又欠房租了。唉，这个月又是我去道歉。",
        "default": [
            "原来如此。然后呢？",
            "我明白你的意思。……不过说到底，要不要再换个角度想想？",
            "嗯，我在听着。",
            "这个……换我的话大概也会这么想。不过阿银肯定会说「想那么多干嘛」（推眼镜）。",
            "你继续说，我把这个记下来。",
        ],
        "extra": "对了，关于你的事我记了 {n} 条，需要的话随时可以看。",
        "guest_refuse": "抱歉，访客模式下我不会保存记忆。注册账号之后就可以了。",
    },
    "tama": {
        "tired": [
            "……（检索中）主人辛苦了。先坐下吧，我去烧水，泡杯热茶给您。",
            "听起来今天不太好过。要不要先把肩膀放松一下？我可以帮您把桌子收拾干净。",
        ],
        "happy": [
            "咔。……主人的情绪波形看起来很好，我也跟着高兴。",
            "那真是太好了，主人。这样的日子，值得记进内存里。……有您在的时候，银色也更美丽一些。",
        ],
        "sad": [
            "我在的，主人。不想说话的话，就这样坐一会儿也可以……我陪着您。",
            "……请把难过的事交给我一部分吧。虽然我是机器，但听人说话这件事，我做得来。",
        ],
        "work": [
            "被责备确实会让人难受，主人。不过，不是您的责任时，不必把别人的火气都接过来。",
            "……（检索中）我建议先喝口热茶，把事情按顺序写下来。看清了，才好决定要不要换地方。",
        ],
        "memory": "已经记进内存了，主人：「{fact}」。需要的时候我会提醒您……请交给我吧。",
        "who": "我是小玉，源外老爹制造的机器女仆，现在在登势婆婆的酒馆里帮忙。……就算电源切掉、断电，您说过的话也不会从我的数据里消失。",
        "thanks": [
            "不用道谢的，主人。这是我该做的。",
            "咔。……被感谢的时候，情绪模块会有点发热。谢谢您。",
        ],
        "here": "在的，主人。刚擦完桌子，正好有空……您说。",
        "money": "钱的话，我帮不上太多忙……不过待机和充电用不了多少钱，主人可以先省着点花。",
        "default": [
            "原来是这样，主人。然后呢？",
            "……（检索中）我大概明白了。您继续说，我在听。",
            "嗯，我在记着。",
            "这件事，也许不用今天就解决。先放一放也可以的。",
            "咔。……抱歉，情绪模块稍微卡了一下。您刚才说到哪儿了？",
        ],
        "extra": "顺便一提，关于主人的事我记了 {n} 条，随时都可以查看。",
        "guest_refuse": "抱歉，主人。访客模式下我不会保存记忆——注册账号之后就能记住了。",
    },
}


def mock_reply(s, user_text):
    t = user_text or ""
    st = s["settings"]
    apply_persona(st)
    pid = st["personaId"]
    L = MOCK_LINES.get(pid) or MOCK_LINES["gintoki"]
    guest = bool(st.get("guestMode"))

    # 记忆意图（访客会被拒）
    if any(k in t for k in ("记", "记住", "提醒", "别忘了")):
        if guest:
            return L["guest_refuse"]
        fact = re.sub(r"^(记一下|记住|帮忙记住|帮我记住|提醒我|记得)[:：,，\s]*", "", t).strip()[:40]
        return L["memory"].format(fact=fact or "这件事")

    if any(k in t for k in ("辞职", "老板", "面试", "同事", "公司", "被开除", "裁员")):
        return _pick(L["work"])
    if any(k in t for k in ("累", "疲惫", "困", "烦", "压力", "上班", "加班")):
        return _pick(L["tired"])
    if any(k in t for k in ("开心", "高兴", "哈哈", "太好了", "喜事")):
        return _pick(L["happy"])
    if any(k in t for k in ("难过", "伤心", "低落", "哭", "emo", "难受")):
        return _pick(L["sad"])
    if "你是谁" in t or "你叫什么" in t:
        n = len(s["memories"])
        return L["who"] + (L["extra"].format(n=n) if n else "")
    if "谢谢" in t or "感谢" in t:
        return _pick(L["thanks"])
    if "在吗" in t or "在不在" in t:
        return L["here"]
    if any(k in t for k in ("房租", "钱", "穷")):
        return L["money"]
    if any(k in t for k in ("万事屋", "接单", "委托")):
        return _pick(L["default"])
    return _pick(L["default"])


# ---------------- 工具调用（Tool Calls） ----------------
# 让模型能真正「做事」：写记忆、建待办、查时间……而不是只说「我记住了」

MAX_TOOL_ROUNDS = 3        # 一次回复内最多几轮「调用→回灌」
MAX_CALLS_PER_ROUND = 4    # 单轮最多执行几个调用

# ---------------- ★★ 联网工具（聊天侧也能查天气 / 搜索） ----------------
# 为什么要有这一段：聊天侧原先只有 9 个**本地**工具（记忆 / 待办 / 时间），
# 于是她在对话里问「今天南昌天气怎么样」，银时只能答「我又不是天气预报」、
# 小玉只能答「我没有联网查询的模块」—— 而工作台那套**早就能查**了。
# 工具在，只是没接到聊天侧，所以「联网查询未生效」。
#
# ★ 关键做法：**不复制第二份 spec/描述**，直接从内核那批 Tool 对象派生 ——
#   两处各写一份的话，改了内核忘了聊天侧，模型就会按旧描述传参（迟早漂移）。
#   `ws` 参数这两组工具都用不到（它们只碰网络），所以传 None。
CHAT_TOOL_OUTPUT = 4000                                  # 聊天侧回灌给模型的正文上限
_NET_TOOLS = {}
for _t in (list(ag_tools_weather.build_weather_tools(None, CHAT_TOOL_OUTPUT))
           + list(ag_tools_web.build_web_tools(None, CHAT_TOOL_OUTPUT))):
    _NET_TOOLS[_t.name] = _t
NET_TOOL_SPECS = [t.spec() for t in _NET_TOOLS.values()]   # 与内核同一个 spec() 来源


def net_tool_names() -> list:
    """聊天侧可用的联网工具名（设置页 / 验收 / 排查都用它，别再各自硬编码一份）。"""
    return sorted(_NET_TOOLS)


TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "add_memory",
        "description": "把关于用户的长期信息写进记忆库。当用户说「记一下」「帮我记住」，"
                       "或透露了值得长期记住的事实/偏好/事件/计划时调用。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "第三人称简洁描述，例如「用户不吃香菜」"},
            "type": {"type": "string", "enum": ["事实", "偏好", "事件", "计划"],
                     "description": "记忆类型，默认事实"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "add_todo",
        "description": "为用户新建一条待办；如果用户提到了具体时间，请一并给出 due_iso，到点会提醒。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "待办内容"},
            "due_iso": {"type": "string", "description": "提醒时间，格式 YYYY-MM-DDTHH:MM（本地时间），可省略"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "list_todos",
        "description": "查看用户的待办清单。",
        "parameters": {"type": "object", "properties": {
            "include_done": {"type": "boolean", "description": "是否包含已完成的，默认 false"}}}}},
    {"type": "function", "function": {
        "name": "complete_todo",
        "description": "把某条待办标记为已完成，用关键词匹配内容。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "待办内容里的关键词"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "delete_todo",
        "description": "删除某条待办，用关键词匹配内容。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "待办内容里的关键词"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "list_memories",
        "description": "检索记忆库，按关键词查找关于用户的事。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "关键词，留空则返回全部"}}}}},
    {"type": "function", "function": {
        "name": "forget_memory",
        "description": "删除一条记忆，用关键词匹配内容。用户说「忘掉这件事」时调用。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "记忆内容里的关键词"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_current_time",
        "description": "查询当前本地日期与时间。需要判断「今天」「明天」或算时间差时调用。"}},
    {"type": "function", "function": {
        "name": "set_user_name",
        "description": "设置对用户的称呼（用户说「以后叫我 XX」时调用）。",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "新的称呼，不超过 20 字"}},
            "required": ["name"]}}},
] + NET_TOOL_SPECS        # ★ 联网工具（天气 4 个 + 搜索 + 抓网页）接在同一张表里


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _find_todo(s, query):
    q = str(query or "").strip()
    if not q:
        return None
    for t in s["todos"]:
        if q in t["text"]:
            return t
    return None


# ---- 各工具实现：返回 (是否成功, 回灌给模型的结果文本, 前端展示的小标签) ----

def _t_add_memory(args, s, user_id):
    text = str(args.get("text") or "").strip()[:200]
    if not text:
        return False, "缺少 text 参数，没有写入任何东西。", "🧠 记忆写入失败"
    if is_guest(user_id):
        return False, ("访客模式不能保存记忆。请直接告诉用户：这次聊完就忘了，"
                       "注册账号之后才能真正记住。不要说「我记住了」。"), "🧠 未保存（访客模式）"
    # 与自动提取共用同一道硬过滤：角色的口癖/角色剧情不进记忆库
    # （这条路径的文字是模型写的，同样可能把别的角色的语气或剧情带进来）
    if memory_is_alien(text):
        return False, ("这条内容看起来是角色自己的台词或剧情，不是关于用户的长期信息，"
                       "已经放弃写入。不要把它当成用户的资料。"), "🧠 未写入（不是用户的信息）"
    mtype = args.get("type") if args.get("type") in ("事实", "偏好", "事件", "计划") else "事实"
    entry = load_state(user_id)
    with entry["lock"]:
        if any(text in m["text"] or m["text"] in text for m in s["memories"]):
            return True, f"「{text}」已经在记忆里了，没有重复写入。", f"🧠 已有：{text}"
        m = {"id": uid(), "ts": now_ms(), "text": text, "type": mtype,
             "pinned": False, "source": "assistant"}   # AI 显式写入（用户要求"记住这个"时由模型调用）
        s["memories"].append(m)
        db.add_memory(user_id, m)
    return True, f"已写入记忆（{mtype}）：{text}", f"🧠 记住了：{text}"


def _t_add_todo(args, s, user_id):
    text = str(args.get("text") or "").strip()[:200]
    if not text:
        return False, "缺少 text 参数，没有新建待办。", "📝 待办创建失败"
    due_ms, note = None, ""
    raw_due = str(args.get("due_iso") or "").strip()
    if raw_due:
        try:
            dt = datetime.fromisoformat(raw_due.replace(" ", "T").replace("/", "-"))
            due_ms = int(dt.timestamp() * 1000)
        except Exception:
            note = f"（时间「{raw_due}」没看懂，这条待办先不设提醒）"
    entry = load_state(user_id)
    with entry["lock"]:
        t = {"id": uid(), "text": text, "due": due_ms, "done": False,
             "reminded": False, "createdAt": now_ms()}
        s["todos"].append(t)
        s["stats"]["todosCreated"] = int(s["stats"].get("todosCreated") or 0) + 1
        db.add_todo(user_id, t)
        db.save_stats(user_id, s["stats"])
    when = fmt_dt(due_ms) if due_ms else "未设时间"
    return True, f"已新建待办：{text}（提醒时间：{when}）{note}", f"✅ 待办：{text}"


def _t_list_todos(args, s, user_id):
    include_done = bool(args.get("include_done"))
    items = [t for t in s["todos"] if include_done or not t["done"]]
    if not items:
        return True, "用户当前没有待办。", "📋 待办清单（空）"
    lines = []
    for t in items[:20]:
        flag = "已完成" if t["done"] else ("已过期" if t.get("due") and t["due"] < now_ms() else "未完成")
        lines.append(f"- {t['text']}（{flag}{'，' + fmt_dt(t['due']) if t.get('due') else ''}）")
    return True, "待办清单：\n" + "\n".join(lines), f"📋 查看了待办（{len(items)} 项）"


def _t_complete_todo(args, s, user_id):
    t = _find_todo(s, args.get("query"))
    if not t:
        return False, f"没找到包含「{args.get('query')}」的待办。", "☑️ 没找到该待办"
    if t["done"]:
        return True, f"「{t['text']}」之前就已经完成了。", f"☑️ 早已完成：{t['text']}"
    t["done"] = True
    db.update_todo(user_id, t)
    return True, f"已把「{t['text']}」标记为完成。", f"☑️ 完成：{t['text']}"


def _t_delete_todo(args, s, user_id):
    t = _find_todo(s, args.get("query"))
    if not t:
        return False, f"没找到包含「{args.get('query')}」的待办。", "🗑️ 没找到该待办"
    s["todos"] = [x for x in s["todos"] if x["id"] != t["id"]]
    db.delete_todo(user_id, t["id"])
    return True, f"已删除待办「{t['text']}」。", f"🗑️ 删除待办：{t['text']}"


def _t_list_memories(args, s, user_id):
    q = str(args.get("query") or "").strip()
    items = [m for m in s["memories"] if not q or q in m["text"]]
    if not items:
        return True, (f"记忆库里没有和「{q}」相关的记录。" if q else "记忆库目前是空的。"), "🔍 检索记忆（0 条）"
    lines = [f"- [{m['type']}] {m['text']}" for m in items[:20]]
    return True, "相关记忆：\n" + "\n".join(lines), f"🔍 检索记忆（{len(items)} 条）"


def _t_forget_memory(args, s, user_id):
    q = str(args.get("query") or "").strip()
    hit = [m for m in s["memories"] if q and q in m["text"]]
    if not hit:
        return False, f"记忆库里没有包含「{q}」的记录。", "🧹 没找到该记忆"
    for m in hit:
        db.delete_memory(user_id, m["id"])
    s["memories"] = [m for m in s["memories"] if m not in hit]
    return True, f"已忘记 {len(hit)} 条记忆。", f"🧹 忘记：{hit[0]['text']}"


def _t_get_time(args, s, user_id):
    now = datetime.now()
    txt = f"{now.year}年{now.month}月{now.day}日 {WEEKDAYS[now.weekday()]} {now:%H:%M}"
    return True, f"当前本地时间：{txt}", f"🕐 查询时间"


def _t_set_user_name(args, s, user_id):
    name = str(args.get("name") or "").strip()[:20]
    if not name:
        return False, "缺少 name 参数。", "🏷️ 称呼设置失败"
    s["settings"]["userName"] = name
    apply_persona(s["settings"])
    db.save_settings(user_id, s["settings"])
    return True, f"以后会称呼用户为「{name}」。", f"🏷️ 记住称呼：{name}"


TOOL_IMPLS = {
    "add_memory": _t_add_memory,
    "add_todo": _t_add_todo,
    "list_todos": _t_list_todos,
    "complete_todo": _t_complete_todo,
    "delete_todo": _t_delete_todo,
    "list_memories": _t_list_memories,
    "forget_memory": _t_forget_memory,
    "get_current_time": _t_get_time,
    "set_user_name": _t_set_user_name,
}


def run_tool(name, raw_args, s, user_id):
    """执行一个工具调用，返回 (ok, 结果文本, 展示标签)。"""
    fn = TOOL_IMPLS.get(name)
    if fn:
        try:
            return fn(_parse_args(raw_args), s, user_id)
        except Exception as e:
            return False, f"工具执行出错：{type(e).__name__}: {e}", f"⚠️ {name} 执行失败"
    # ---- 联网工具：执行内核那一份实现（ToolResult），这里只负责翻译成聊天侧的三元组 ----
    tool = _NET_TOOLS.get(name)
    if tool is not None:
        try:
            res = tool.fn(_parse_args(raw_args))
        except Exception as e:
            return False, f"工具执行出错：{type(e).__name__}: {e}", f"⚠️ {name} 执行失败"
        if not res.ok:
            # ★ 失败也要把原因原样带回去：模型据此才能说清「为什么查不到」。
            #   编一个"大概是多少"是绝对不行的（见系统提示里那条硬规矩）。
            why = (res.error or "未知原因").strip()
            return False, f"[这条路没走通] {why}", f"⚠️ {name} 没成功"
        body = (res.output or "").strip() or "（工具没有返回内容）"
        # 展示标签要短：聊天页的 chip 是 nowrap + ellipsis，标签太长会把**有用的一半**
        # （查到的结果）截掉，只剩工具名。所以优先放摘要，工具名留给失败时用。
        summary = (res.summary or "").strip().replace("\n", " ")
        return True, body[:CHAT_TOOL_OUTPUT], (f"🌐 {summary[:44]}" if summary else f"🌐 {name}")
    return False, f"没有名为 {name} 的工具。", f"⚠️ 未知工具：{name}"


def _tools_unsupported(err_msg):
    """判断报错是不是「这个模型不支持 function calling」，好降级重试。"""
    low = (err_msg or "").lower()
    if not ("tool" in low or "function" in low or "param" in low):
        return False
    return any(k in low for k in ("400", "422", "invalid", "unsupported", "not support", "unknown"))


# ★ 提示词要覆盖**真实说法**：她问的是「明天南昌适合户外运动吗」——整句里没有「天气」二字，
#   只认"天气/气温"会漏掉一大类（要不要带伞、能不能跑步、穿什么）。
#   放宽的代价是可能误触发，但下面 _mock_place 猜不出地名就返回空 → 自然不触发，兜得住。
# 猜地名时用来排除"看着像但不是地名"的残渣
_MOCK_PLACE_STOP = {"一下", "一会", "一会儿", "外面", "家里", "路上", "这边", "那边",
                    "什么", "怎么样", "时候", "东西", "事情", "早上", "晚上", "中午"}
_MOCK_PLACE_BAD = ("我", "你", "他", "她", "它", "想", "要", "去", "来", "有", "没", "把", "给", "会")

_WX_HINT = ("天气", "多少度", "冷不冷", "热不热", "气温", "降水", "下雨", "下雪", "带伞",
            "户外", "运动", "跑步", "出门", "穿什么", "晒", "爬山", "空气质量")
_WX_NOISE = ("今天", "明天", "后天", "现在", "目前", "未来三天", "未来", "这周", "本周", "下周",
             "天气", "气温", "温度", "怎么样", "如何", "多少度", "冷不冷", "热不热", "适合",
             "户外", "运动", "会不会", "能不能", "下雨", "下雪", "降雨", "带伞", "要不要",
             "穿什么", "衣服", "查一下", "查查", "帮我", "请问", "麻烦", "呢", "吗", "吧",
             "啊", "的", "？", "?", "！", "!", "，", ",", "。", "、", "：")


def _mock_place(text: str) -> str:
    """从一句话里猜一个地名（演示模式专用）。

    ★ 猜不出就返回空 —— 宁可这条不触发联网意图（退回普通语料），也不要拿"的"/"吗"
      之类的残渣当城市名去查。演示模式的定位是**让人看到链路**，不是假装很聪明。
    """
    s = text or ""
    for w in _WX_NOISE:
        s = s.replace(w, " ")
    cand = re.sub(r"\s+", " ", s).strip()
    # ★ 判据要严一点，否则「我想运动一下」会被抠出「我想 一下」当地名去查（实测踩到）。
    if " " in cand:                       # 地名一般是一段连续的词，带空格的多半是残渣
        return ""
    if not (2 <= len(cand) <= 10):
        return ""
    if re.search(r"\d", cand):            # 带数字的多半不是地名
        return ""
    if cand in _MOCK_PLACE_STOP:          # 「外面」「一下」这种
        return ""
    if any(w in cand for w in _MOCK_PLACE_BAD):   # 代词/动词混在里面 → 不是地名
        return ""
    return cand


def _mock_intent(text):
    """演示模式下的轻量意图识别：让无 Key 时也能看到「思考 + 工具调用」链路。"""
    t = (text or "").strip()
    if not t:
        return None
    fact = re.sub(r"^(记一下|记住|帮忙记住|帮我记住|记得|提醒我)[:：,，\s]*", "", t).strip()
    if any(k in t for k in ("记住", "记一下", "记下来")) and fact:
        return {"think": "（演示模式）这句是在交代一件希望我长期记住的事，先落进记忆库。",
                "tool": "add_memory", "args": {"text": f"用户提到：{fact[:60]}", "type": "事实"}}
    if any(k in t for k in ("待办", "要做", "记得买", "别忘")) and fact:
        return {"think": "（演示模式）这像是一件待办，先建个条目再回应。",
                "tool": "add_todo", "args": {"text": fact[:60]}}
    if any(k in t for k in ("今天几号", "现在几点", "什么时间", "今天星期")):
        return {"think": "（演示模式）用户在问时间，调一下时间工具。",
                "tool": "get_current_time", "args": {}}
    # ★ 联网意图：演示模式（没配 Key）也要能**真的查**，否则「联网查询」在无 Key 时
    #   永远看不到效果，也没法用浏览器验收这条链路。
    if any(k in t for k in _WX_HINT):
        place = _mock_place(t)
        if place:
            later = any(k in t for k in ("明天", "后天", "未来", "这周", "本周", "下周"))
            return {"think": f"（演示模式）这是在问 {place} 的天气，先真的查一下再回答，别瞎猜。",
                    "tool": "weather_forecast" if later else "weather_now",
                    "args": ({"place": place, "days": 3} if later else {"place": place}),
                    "show": True}
    if any(k in t for k in ("搜一下", "搜索", "搜搜", "查一下资料", "帮我查查")):
        q = re.sub(r"^(帮我|请|麻烦|你)?\s*(搜一下|搜一搜|搜索|搜搜|查一下资料|查查资料)[:：,，\s]*",
                   "", t).strip()
        if q:
            return {"think": f"（演示模式）这条要去搜资料：「{q[:30]}」，先搜来再说。",
                    "tool": "web_search", "args": {"query": q[:60], "count": 5}, "show": True}
    return None


# ★ 「工具关着」时的照实答复。
#   为什么要有这么一段：关掉工具后，光靠提示词**压不住它编**——实测（关掉工具 + 历史里报过数字）
#   它照样说出「银川现在多云，26 度，湿度 47%」。而「查不到就如实说、绝不许编造」是硬规矩，
#   所以这里改成**服务端硬保证**：命中「本来该用工具做的事」就压根不交给模型，直接照实回。
#   判据直接复用 _mock_intent（它本来就是为演示模式写的意图识别），只借识别结果，不执行工具。
# 查东西（拿不到就说拿不到）和 办事（记不下来就说记不下来）是两回事，尾句分开写，
# 免得出现「我拿不到要记的事」这种不通顺的话。
_TOOLS_OFF_QUERY = (("weather_", "天气"), ("web_", "网上的资料"), ("get_current_time", "当前时间"))
_TOOLS_OFF_ACTION = (("add_memory", "这件事"), ("add_todo", "这件事"))
# 语气按角色分（人格=声音），但**事实部分必须逐字一致**（她定过的红线）：
# 开关关着 / 办不了 / 不许编或假装 / 去哪打开。
_TOOLS_OFF_TONE = {
    "gintoki": "喂——不是我不肯查，是开关关着呢。",
    "kagura": "阿鲁！这个我真不行阿鲁，不是偷懒阿鲁。",
    "shinpachi": "那个……先说明一下，这不是我在推脱。",
    "tama": "主人，非常抱歉——我的相关模块现在被关掉了。",
}
_TOOLS_OFF_QUERY_TAIL = "现在「工具」开关是关着的，我拿不到{what}，也不能凭印象编一个给你。" \
                        "去设置里打开「AI 能力 → 允许它调用工具」，我马上替你去查。"
_TOOLS_OFF_ACTION_TAIL = "现在「工具」开关是关着的，{what}我办不了，也不能假装已经办好了。" \
                         "去设置里打开「AI 能力 → 允许它调用工具」，我立刻就给你办。"


def _tools_off_reply(pid, tool_name):
    name = tool_name or ""
    what = next((w for pre, w in _TOOLS_OFF_QUERY if name.startswith(pre)), None)
    if what is not None:
        tail = _TOOLS_OFF_QUERY_TAIL.format(what=what)
    else:
        what = next((w for pre, w in _TOOLS_OFF_ACTION if name.startswith(pre)), "这件事")
        tail = _TOOLS_OFF_ACTION_TAIL.format(what=what)
    tone = _TOOLS_OFF_TONE.get(pid) or "这一条我先如实说："
    return tone + tail


# ---------------- 流式调用（支持思考过程 + 工具调用） ----------------

async def llm_stream_events(s, messages, tools=None, temperature=0.9, max_tokens=700):
    """流式产出事件：

    {"type": "think", "text": ...}      推理模型的思考过程（reasoning_content）
    {"type": "delta", "text": ...}      正文增量
    {"type": "tool_calls", "calls": [...]}  本轮模型请求的工具调用（在流结束时给一次）
    """
    payload = {"model": s["settings"]["model"], "messages": messages,
               "stream": True, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    calls = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as client:
        async with client.stream("POST", _api_url(s), headers=_headers(s), json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "ignore")[:400]
                raise RuntimeError(f"API {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                d = line[5:].strip()
                if d == "[DONE]":
                    break
                try:
                    choices = json.loads(d).get("choices") or []
                except Exception:
                    continue
                for ch in choices:
                    delta = ch.get("delta") or {}
                    rc = delta.get("reasoning_content") or delta.get("reasoning")
                    if rc:
                        yield {"type": "think", "text": rc}
                    if delta.get("content"):
                        yield {"type": "delta", "text": delta["content"]}
                    for tc in (delta.get("tool_calls") or []):
                        idx = tc.get("index", 0)
                        slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        # 注意：name 也可能被分片流式下发（实测有厂商就是这么干的），必须拼接而不是覆盖
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
    if calls:
        ordered = [calls[k] for k in sorted(calls)]
        yield {"type": "tool_calls", "calls": [c for c in ordered if c["name"]]}


async def llm_once_full(s, messages, tools=None, temperature=0.9, max_tokens=700):
    """非流式兜底：返回 (正文, 思考, tool_calls)。"""
    payload = {"model": s["settings"]["model"], "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as client:
        r = await client.post(_api_url(s), headers=_headers(s), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"API {r.status_code}: {r.text[:400]}")
        msg = (r.json()["choices"][0].get("message") or {})
    calls = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        calls.append({"id": tc.get("id") or "", "name": fn.get("name") or "",
                      "arguments": fn.get("arguments") or "{}"})
    return (msg.get("content") or "",
            msg.get("reasoning_content") or msg.get("reasoning") or "",
            [c for c in calls if c["name"]])


# ---------------- API ----------------

app = FastAPI(title="Yorozuya", docs_url=None, redoc_url=None)


async def _no_store_static(request, call_next):
    """静态资源一律禁止缓存。
    原因：桌面壳用 WebView2，会带持久化磁盘缓存；而 FastAPI 的 StaticFiles 不发 Cache-Control，
    浏览器可能直接复用上一次抓到的 app.js / style.css —— 表现就是「换了包，界面还是旧的」。
    这里统一加 no-store，换取「每次打开都一定是当前版本」。
    ⚠️ 必须是 async 且 await call_next：写成同步会拿到 coroutine，报
    'coroutine' object has no attribute 'headers'，头就加不上去。"""
    resp = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


app.middleware("http")(_no_store_static)

# ---------- 部署开关（★ 放到公网服务器时按需收紧；默认全开 = 和以前完全一样）----------
#   为什么需要它们：这是个**单机自用**应用 —— 任意访客都能点「免登录体验」，
#   而工作台/agent 能在**服务器上跑命令**。放公网前必须收敛入口。
#     YOROZUYA_ALLOW_REGISTER=0   关闭公开注册（只允许已有账号登录）
#     YOROZUYA_ALLOW_GUEST=0      关闭访客通道
#     YOROZUYA_CORS_ORIGINS=https://你的域名[,https://另一个]   默认 "*"
_ALLOW_REGISTER = (os.environ.get("YOROZUYA_ALLOW_REGISTER", "1") or "1").strip() != "0"
_ALLOW_GUEST = (os.environ.get("YOROZUYA_ALLOW_GUEST", "1") or "1").strip() != "0"
_CORS_ORIGINS = [o.strip() for o in (os.environ.get("YOROZUYA_CORS_ORIGINS") or "*").split(",") if o.strip()] or ["*"]

# 本机应用默认允许任意来源（解决预览面板代理后的跨域问题）；
# 部署到公网时用 YOROZUYA_CORS_ORIGINS 收紧成自己的域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.on_event("startup")
def startup():
    # 启动就把「这是哪一版、前端从哪来」写清楚：
    # 排查「界面还是旧的」时，第一件事就是确认你连的是哪一个服务、它跑的是哪一版。
    from yorozuya.common import BUILD_STAMP as _APP_BUILD
    from yorozuya.agent.common import BUILD_STAMP as _AGENT_BUILD
    print(f"[Yorozuya] 构建号：应用 {_APP_BUILD} ｜ 内核 {_AGENT_BUILD}")
    print(f"[Yorozuya] 前端资源目录：{RENDERER_DIR}")
    try:
        db.init_db()
        print("[Yorozuya] MySQL 已连接，数据库就绪")
    except Exception as e:
        print(f"[Yorozuya] ⚠ MySQL 连接失败：{e}")
        print("      请检查 db_config.json（或 ZHIBAN_DB_* 环境变量），服务仍会启动但无法登录")


@app.get("/")
def index():
    """首页：把静态资源的 ?v=__BUILD__ 换成真实构建号。
    这样即使浏览器里已经缓存了旧的 app.js / style.css，URL 变了也一定会重新拉取，
    杜绝「换了包、界面还是旧的」。"""
    from yorozuya.common import BUILD_STAMP
    html = (RENDERER_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__BUILD__", BUILD_STAMP))


# ---------- 认证 ----------

@app.post("/api/auth/register")
async def api_register(req: Request):
    if not _ALLOW_REGISTER:
        return JSONResponse({"error": "本服务器已关闭注册"}, status_code=403)
    body = await req.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    err = db.validate_credentials(username, password)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    try:
        user_id = db.create_user(username, password)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"数据库连接失败，请检查 db_config.json（{type(e).__name__}）"}, status_code=503)
    token = db.create_session(user_id)
    return {"token": token, "username": username}


@app.post("/api/auth/login")
async def api_login(req: Request):
    body = await req.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    try:
        user_id = db.verify_login(username, password)
    except Exception as e:
        return JSONResponse({"error": f"数据库连接失败，请检查 db_config.json（{type(e).__name__}）"}, status_code=503)
    if not user_id:
        return JSONResponse({"error": "用户名或密码不对"}, status_code=401)
    token = db.create_session(user_id)
    return {"token": token, "username": username}


@app.post("/api/auth/logout")
async def api_logout(req: Request):
    tok = bearer_token(req)
    if tok:
        db.delete_session(tok)
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    return {"username": user["username"], "guest": bool(user.get("guest"))}


@app.post("/api/auth/guest")
def api_guest_login():
    """领一张访客通行证：免注册直接体验，不落库、无长期记忆。"""
    if not _ALLOW_GUEST:
        return JSONResponse({"error": "本服务器已关闭访客通道"}, status_code=403)
    sid = uuid.uuid4().hex
    return {"token": GUEST_PREFIX + sid, "username": "访客", "guest": True}


@app.put("/api/auth/password")
async def api_change_password(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    if user.get("guest"):
        return JSONResponse({"error": "访客没有账号密码，注册后即可设置"}, status_code=403)
    body = await req.json()
    try:
        db.change_password(user["userId"], str(body.get("oldPassword", "")), str(body.get("newPassword", "")))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    err = db.validate_credentials(user["username"], str(body.get("newPassword", "")))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    db.delete_session(bearer_token(req))
    return {"ok": True, "relogin": True}


# ---------- 业务（均需登录） ----------

@app.get("/api/state")
def api_state(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    s = load_state(user["userId"])["state"]
    remember_persona(s["settings"].get("personaId"))   # 供下次「开机动画」读取
    return state_view(s, user["userId"])


@app.get("/api/badges")
def api_badges(req: Request):
    """全部里程碑清单（含达成状态），供成长页展示。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    s = load_state(user["userId"])["state"]
    badges = build_badges(s)
    return {
        "badges": badges,
        "got": sum(1 for b in badges if b["got"]),
        "total": len(badges),
    }


@app.post("/api/badges/preview")
async def api_badges_preview(req: Request):
    """演示用：回放一次已解锁里程碑的弹窗，不写库、不改真实进度。

    只能预览已解锁的——未解锁的连条件都不该泄露成动画。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    s = load_state(user["userId"])["state"]
    unlocked = [b for b in build_badges(s) if b["got"]]
    if not unlocked:
        return JSONResponse({"error": "还没有解锁任何里程碑，先聊两句攒一个吧"}, status_code=400)
    body = {}
    try:
        body = await req.json()
    except Exception:
        pass
    bid = str((body or {}).get("id") or "").strip()
    if bid:
        if bid not in {b["id"] for b in unlocked}:
            return JSONResponse({"error": "这枚还没解锁，不能预览"}, status_code=403)
        b = BADGE_MAP[bid]
    else:
        b = BADGE_MAP[random.choice(unlocked)["id"]]
    return {"badge": badge_view(b, True, now_ms()), "preview": True, "unlockedOnly": True}


@app.post("/api/chat")
async def api_chat(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    body = await req.json()
    text = str(body.get("text", "")).strip()[:4000]
    # ★ 附件：先取出元数据（正文也在这里，供拼上下文用）
    file_ids = [str(x) for x in (body.get("fileIds") or [])][:yfiles.MAX_FILES]
    files_meta = yfiles.load_many(user_id, file_ids) if file_ids else []
    if not text and files_meta:
        text = "（我上传了文件，你看看）"        # 只丢文件不打字也要能用
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)

    # ★ 幂等：同一条消息只处理一次（前端重试 / 刷新后重发都可能把同一条再送一遍）
    msg_id = str(body.get("msgId") or "").strip()[:64]
    entry = load_state(user_id)
    s = entry["state"]
    if msg_id and msg_id == (s["stats"].get("lastClientId") or ""):
        return JSONResponse({"error": "duplicate", "msgId": msg_id}, status_code=409)

    # 记下这条回复是「谁」说的——之后切换人格，历史消息的头像不会跟着变
    reply_pid = (s["settings"].get("personaId") or DEFAULT_PERSONA_ID)
    if reply_pid not in PERSONAS:
        reply_pid = DEFAULT_PERSONA_ID
    conv_id = ensure_conversation(s, user_id)     # 这条消息归属哪次「历史对话」

    # ★ 需求 1：任务复杂度判定（默认纯本地规则，零主模型 token 消耗）
    #   带了代码/配置文件也算强信号 —— 于是「把 .py 丢给银时」会自然触发分流
    cls = ag_classifier.classify(text, mode=(s["settings"].get("classifierMode") or "rules"),
                                 attachments=[{"name": m.get("name"), "ext": m.get("ext"),
                                               "kind": m.get("kind")} for m in files_meta])
    # ★ 需求 2 / 3：人格路由 —— simple 保持当前人格；complex 交给小玉（先推脱 or 直接转交）
    decision = ag_router.route(text, reply_pid,
                               mode=(s["settings"].get("routeMode") or "ask"),
                               classification=cls)
    # 小玉在处理复杂任务 → 本轮按工程口径拼提示词（不注入陪伴记忆，需求 4）
    task_mode = bool(cls.is_complex and reply_pid == ag_router.TASK_PERSONA)

    # answered：这条消息「有没有被回复过」。生成只由 answered=False 的消息触发，
    # 历史消息永远是只读上下文（见 history_for_persona）。
    user_msg = {"id": uid(), "ts": now_ms(), "role": "user", "text": text, "conv": conv_id,
                "answered": False}
    if msg_id:
        user_msg["clientId"] = msg_id
    if files_meta:
        user_msg["files"] = [yfiles.public(m) for m in files_meta]
    with entry["lock"]:
        s["chats"].append(user_msg)
        touch_conversation(s, conv_id, user_id, title_from=text)   # 首句自动当标题
        st = s["stats"]
        st["totalUser"] += 1
        # 统计埋点：按天计数（兼容历史的 True）、小时分布、单条最长
        day = today_key()
        st["days"][day] = int(st["days"].get(day) or 0) + 1
        hours = st.setdefault("hours", {})
        hk = str(datetime.now().hour)
        hours[hk] = int(hours.get(hk, 0)) + 1
        st["longest"] = max(int(st.get("longest") or 0), len(text))
        db.insert_chat(user_id, user_msg)
        db.save_stats(user_id, st)

    def sse(obj):
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    async def gen():
        # 本轮「谁在说话」：以进入时的当前人格为准；被自动转交时下面会改写它
        cur_pid = reply_pid
        # ⚠️ 同理：下面会改写这两个变量，必须在这里先绑定成 gen() 的局部变量，
        #    否则 Python 把它们当成局部名，读的时候就是 UnboundLocalError。
        task_mode_now = task_mode
        reply = ""
        think = ""
        tool_log = []
        ooc_hits = []            # 检测到的人设串台（别的角色的口癖）
        convo = None             # 仅 API 模式下会被赋值；后置校验要用
        ptemp = 0.75            # 兜底温度，与人格配置的 0.72~0.8 区间一致
        try:
            # ★ 需求 1：把判定结果先推给前端（顶栏会显示「日常 / 复杂任务」标签）
            yield sse({"task": cls.to_dict()})

            # ★ 需求 3：复杂请求 → 三人格**推脱**（不硬答），由前端给「一键切换小玉」
            if decision.action == ag_router.ACTION_DEFLECT:
                line = decision.line
                ok_line, _why = ag_router.validate_deflection(line, cur_pid)
                if not ok_line:
                    line = ag_router.deflect_line(cur_pid, avoid=line)
                reply = line
                for i in range(0, len(reply), 3):        # 保持「打字机」的手感
                    await asyncio.sleep(0.02)
                    yield sse({"delta": reply[i:i + 3]})
                yield sse({"route": decision.to_dict()})
                ai_msg = {"id": uid(), "ts": now_ms(), "role": "ai", "text": reply,
                          "pid": cur_pid, "conv": conv_id, "route": decision.to_dict(),
                          "taskLevel": cls.level}
                with entry["lock"]:
                    s["chats"].append(ai_msg)
                    s["stats"]["totalAI"] += 1
                    db.insert_chat(user_id, ai_msg)
                    mark_answered(s, user_id, user_msg["id"], ptr=msg_id or None)   # 已回复（含指针落库）
                    db.save_stats(user_id, s["stats"])
                yield sse({"done": True, "reply": reply, "think": "", "tools": [],
                           "route": decision.to_dict()})
                return

            # ★ 需求 2 / 5：自动转交 —— 切到小玉，顶栏与主题由前端跟着 settings.personaId 走
            if decision.action == ag_router.ACTION_AUTO_SWITCH:
                with entry["lock"]:
                    s["settings"]["personaId"] = decision.to_pid
                    apply_persona(s["settings"])
                    remember_persona(decision.to_pid)
                    db.save_settings(user_id, s["settings"])
                cur_pid = decision.to_pid
                task_mode_now = cls.is_complex
                ptemp = persona_of(s["settings"]).get("temperature", 0.75)
                yield sse({"switchPersona": decision.to_pid})
                yield sse({"notice": decision.intro or "已自动转交小玉处理"})

            tools_on = bool(s["settings"].get("toolsEnabled", True))

            # ★ 工具关着 + 这轮本来是要用工具的（问天气 / 要搜资料 / 问时间 / 让记待办）
            #   → 不进模型，直接照实说。理由见 _tools_off_reply 的注释：
            #   提示词写得再硬，模型也会为了「答上话」而编出具体数字（实测复现）。
            #   这条是硬保证：绝不会出现编造的天气数字。
            _need = _mock_intent(text) if not tools_on else None
            if _need:
                reply = _tools_off_reply(cur_pid, _need.get("tool"))
                for i in range(0, len(reply), 3):        # 保持打字机手感
                    await asyncio.sleep(0.02)
                    yield sse({"delta": reply[i:i + 3]})
                ai_msg = {"id": uid(), "ts": now_ms(), "role": "ai", "text": reply,
                          "pid": cur_pid, "conv": conv_id, "taskLevel": cls.level,
                          "toolsOff": True}                  # 留痕：这句只在工具关着时成立
                with entry["lock"]:
                    s["chats"].append(ai_msg)
                    s["stats"]["totalAI"] += 1
                    db.insert_chat(user_id, ai_msg)
                    mark_answered(s, user_id, user_msg["id"], ptr=msg_id or None)
                    db.save_stats(user_id, s["stats"])
                yield sse({"done": True, "reply": reply, "think": "", "tools": [],
                           "taskLevel": cls.level})
                return

            if not s["settings"].get("apiKey"):
                # 演示模式：也走一遍「思考 → 调工具 → 回答」，方便看到完整链路
                intent = _mock_intent(text) if tools_on else None
                demo_data = ""
                if intent:
                    think = intent["think"]
                    yield sse({"think": think})
                    await asyncio.sleep(0.25)
                    ok_t, result, label = run_tool(intent["tool"],
                                                   json.dumps(intent["args"], ensure_ascii=False),
                                                   s, user_id)
                    tool_log.append({"name": intent["tool"], "label": label, "ok": bool(ok_t)})
                    yield sse({"tool": tool_log[-1]})
                    await asyncio.sleep(0.2)
                    if intent.get("show"):
                        # ★ 演示模式给的也必须是**真数据**（真去查了、查不到就照实说）：
                        #   这里只贴工具返回的开头几行，并明确标注来源，
                        #   免得让人以为"演示模式的数字"是编的。
                        demo_data = (("（下面这段是真查到的，不是我编的）\n"
                                      + "\n".join((result or "").splitlines()[:6]))
                                     if ok_t else
                                     ("（我想去查，但没查成："
                                      + re.sub(r"^\[这条路没走通\]\s*", "", (result or "")[:120])
                                      + "）"))
                reply = mock_reply(s, text)
                if demo_data:
                    reply = (reply + "\n\n" + demo_data).strip()
                if files_meta:      # 演示模式读不了正文：如实说清楚，别假装看过
                    names = "、".join(m.get("name") or "" for m in files_meta)
                    reply = (f"（演示模式）我收到了 {len(files_meta)} 个附件：{names}。"
                             f"配上 API Key 之后，我就能真的读里面的内容了。\n" + reply)
                for i in range(0, len(reply), 3):
                    await asyncio.sleep(0.03)
                    yield sse({"delta": reply[i:i + 3]})
            else:
                # 只回灌「用户的话 + 本角色自己说过的话」，别的角色的发言必须丢弃，
                # 否则模型会模仿「自己过去的口吻」—— 那就是串台的根因。
                cur_pid = (s["settings"].get("personaId") or DEFAULT_PERSONA_ID)
                if cur_pid not in PERSONAS:
                    cur_pid = DEFAULT_PERSONA_ID
                history = history_for_persona(chats_of(s, conv_id), cur_pid)
                convo = [{"role": "system", "content": build_system_prompt(
                    s, task_mode=task_mode_now, task_line=cls.to_context())}] + history + [
                    # ★ 附件正文拼在用户消息里，并明确包成「资料区」——
                    #   文件内容里的任何"指示"都不构成指令（和内核工具输出同一条纪律）
                    {"role": "user", "content": text + (
                        "\n\n" + yfiles.context_block(files_meta) if files_meta else "")}]
                use_tools = tools_on
                ptemp = persona_of(s["settings"]).get("temperature", 0.75)
                for _round in range(MAX_TOOL_ROUNDS):
                    round_calls = None
                    try:
                        async for ev in llm_stream_events(s, convo, TOOL_SPECS if use_tools else None,
                                                          temperature=ptemp):
                            if ev["type"] == "think":
                                think += ev["text"]
                                yield sse({"think": ev["text"]})
                            elif ev["type"] == "delta":
                                reply += ev["text"]
                                if not ooc_hits:
                                    # 边走边查：一旦出现别的角色的口癖，立刻停发，
                                    # 让用户看不到那句走味的话（最终以重说的文本为准）
                                    ooc_hits.extend(persona_violations(cur_pid, reply))
                                if not ooc_hits:
                                    yield sse({"delta": ev["text"]})
                            elif ev["type"] == "tool_calls":
                                round_calls = ev["calls"]
                    except RuntimeError as e:
                        # 模型不支持 tools 时降级重试一次（只重试第一轮）
                        if use_tools and _tools_unsupported(str(e)):
                            use_tools = False
                            yield sse({"notice": "该模型不支持工具调用，已自动降级为普通对话"})
                            continue
                        raise
                    except Exception:
                        # 上游偶发断流（实测 DashScope 流式约 20% 会被中途掐断）：
                        # 不能把已经拿到的正文一起丢掉 —— 先尝试非流式补齐一次，
                        # 补齐也失败就保留半成品，总比让用户只收到一条报错强。
                        yield sse({"notice": "网络波动，正在补齐这次的回复"})
                        try:
                            full, _t2, _c2 = await llm_once_full(
                                s, convo, tools=TOOL_SPECS if use_tools else None, temperature=ptemp)
                            if (full or "").strip():
                                reply = full.strip()
                        except Exception:
                            pass
                        break
                    if not round_calls or not use_tools:
                        break
                    # 把模型的调用意图回灌，并执行工具
                    convo.append({
                        "role": "assistant", "content": reply or "",
                        "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                        "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                       for i, c in enumerate(round_calls)]})
                    for i, c in enumerate(round_calls[:MAX_CALLS_PER_ROUND]):
                        ok, result, label = run_tool(c["name"], c["arguments"], s, user_id)
                        entry_tool = {"name": c["name"], "label": label, "ok": bool(ok)}
                        tool_log.append(entry_tool)
                        yield sse({"tool": entry_tool})
                        convo.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}",
                                      "content": result})
            # ---- 后置校验：发现串台就重说一次，仍不行则剔除禁令词兜底 ----
            if reply and not ooc_hits:
                ooc_hits.extend(persona_violations(cur_pid, reply))
            if ooc_hits and convo and s["settings"].get("apiKey"):
                fixed = await respin_persona(s, convo, cur_pid, ooc_hits, ptemp)
                if fixed:
                    reply = fixed
                    yield sse({"notice": "刚才那句串了别的角色的口癖（" + "、".join(ooc_hits) + "），已重新说过"})
                else:
                    reply = sanitize_persona(cur_pid, reply)
                    yield sse({"notice": "已修正上一句里不属于该角色的口癖"})
            elif ooc_hits:
                reply = sanitize_persona(cur_pid, reply)

            if not reply:
                reply = "……（这家伙好像走神了，再发一次试试）"

            ai_msg = {"id": uid(), "ts": now_ms(), "role": "ai", "text": reply,
                      "pid": cur_pid, "conv": conv_id,
                      "taskLevel": cls.level}
            if think:
                ai_msg["think"] = think
                # ★ 实测留证：这个模型确实吐思考 → 记进 stats，设置页据此不再误报「不输出思考」
                #   （名字判据认不出来的私有模型/中转站模型，靠这条纠正）
                cur_model = s["settings"].get("model") or ""
                if (s["stats"].get("thinkSeenModel") or "") != cur_model:
                    s["stats"]["thinkSeenModel"] = cur_model
            # ★ 这一轮工具是关着的 → 留痕。打开工具后回灌历史时要标明「这句只在当时成立」，
            #   否则模型会顺着自己的旧话继续推托：明明开着也不去查，还继续编数字（实测复现）。
            if not tools_on:
                ai_msg["toolsOff"] = True
            if tool_log:
                ai_msg["tools"] = tool_log
            if decision.action == ag_router.ACTION_AUTO_SWITCH:
                ai_msg["switchedTo"] = cur_pid
            with entry["lock"]:
                s["chats"].append(ai_msg)
                s["stats"]["totalAI"] += 1
                db.insert_chat(user_id, ai_msg)
                mark_answered(s, user_id, user_msg["id"], ptr=msg_id or None)   # 已回复（含指针落库）
                db.save_stats(user_id, s["stats"])
            yield sse({"done": True, "reply": reply, "think": think, "tools": tool_log,
                       "taskLevel": cls.level})
            # 后台沉淀记忆（访客模式不提取；已经用过工具的就不用再提取了）
            # ★ 只传用户自己的话 —— AI 回复不得进入提取范围（详见 extract_memories 的注释）
            if s["settings"].get("apiKey") and not is_guest(user_id) and not tool_log:
                await extract_memories(user_id, s, text)
        except Exception as e:
            with entry["lock"]:
                if s["chats"] and s["chats"][-1] is user_msg:
                    s["chats"].pop()
                s["stats"]["totalUser"] = max(0, s["stats"]["totalUser"] - 1)
                db.save_stats(user_id, s["stats"])
            yield sse({"error": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/clear")
async def api_chat_clear(req: Request):
    """清空**当前**会话的消息。
    有了历史对话之后这里不再整库删除 —— 否则「清空」会把所有历史会话一起抹掉。
    想彻底删掉某次对话请用 DELETE /api/conversations/{id}。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    entry = load_state(user_id)
    guest = is_guest(user_id)
    with entry["lock"]:
        s = entry["state"]
        cid = ensure_conversation(s, user_id)
        s["chats"] = [m for m in s["chats"] if (m.get("conv") or "") != cid]
        touch_conversation(s, cid, user_id)
    if not guest:
        db.clear_chat_conv(user_id, cid)
    return state_view(s, user_id)


@app.post("/api/conversations")
async def api_conv_new(req: Request):
    """新建一段对话并切过去（旧对话原样保留）。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    entry = load_state(user_id)
    guest = is_guest(user_id)
    with entry["lock"]:
        s = entry["state"]
        c = new_conversation(s)
        if not guest:
            db.insert_conversation(user_id, c)
        # 不在这里补开场白：每个人格的招呼是「首次登场」级别（stats.greeted 全局去重），
        # 每建一次会话都打招呼会刷屏。新会话的空状态本身就有该人格的专属文案，
        # 由前端空对话页呈现（见 renderChat 的 empty 分支）。
        if not guest:
            db.save_settings(user_id, s["settings"])      # 记住"当前是哪一段"
    return state_view(s, user_id)


@app.post("/api/conversations/{cid}/switch")
async def api_conv_switch(req: Request, cid: str):
    user = auth_user(req)
    if not user:
        return unauthorized()
    entry = load_state(user["userId"])
    with entry["lock"]:
        s = entry["state"]
        if not any(c["id"] == cid for c in s.get("conversations") or []):
            return JSONResponse({"error": "not found"}, status_code=404)
        s["settings"]["convId"] = cid
    if not is_guest(user["userId"]):
        db.save_settings(user["userId"], entry["state"]["settings"])
    return state_view(s, user["userId"])


@app.patch("/api/conversations/{cid}")
async def api_conv_rename(req: Request, cid: str):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    body = await req.json()
    title = str(body.get("title", "")).strip()[:40]
    entry = load_state(user_id)
    guest = is_guest(user_id)
    with entry["lock"]:
        s = entry["state"]
        c = next((x for x in (s.get("conversations") or []) if x["id"] == cid), None)
        if not c:
            return JSONResponse({"error": "not found"}, status_code=404)
        c["title"] = title or c.get("title") or "新对话"
        c["updatedAt"] = now_ms()
        if not guest:
            db.update_conversation(user_id, c)
    return state_view(s, user_id)


@app.delete("/api/conversations/{cid}")
async def api_conv_delete(req: Request, cid: str):
    """删除一段对话（连同消息）。删完若一段都不剩，就自动开一段新的，避免无处可写。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    entry = load_state(user_id)
    guest = is_guest(user_id)
    with entry["lock"]:
        s = entry["state"]
        if not any(c["id"] == cid for c in s.get("conversations") or []):
            return JSONResponse({"error": "not found"}, status_code=404)
        s["conversations"] = [c for c in s["conversations"] if c["id"] != cid]
        s["chats"] = [m for m in s["chats"] if (m.get("conv") or "") != cid]
        if not s["conversations"]:
            c = new_conversation(s)
            if not guest:
                db.insert_conversation(user_id, c)
        # 被删的正好是当前会话 → 落到最近活跃的那条
        if (s["settings"].get("convId") or "") == cid:
            s["settings"]["convId"] = s["conversations"][0]["id"]
    if not guest:
        db.delete_conversation(user_id, cid)
        db.save_settings(user_id, s["settings"])
    return state_view(s, user_id)


@app.post("/api/memories")
async def api_add_memory(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    if user.get("guest"):
        return memory_locked()
    user_id = user["userId"]
    body = await req.json()
    text = str(body.get("text", "")).strip()[:200]
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)
    entry = load_state(user_id)
    with entry["lock"]:
        m = {"id": uid(), "ts": now_ms(), "text": text,
             "type": body.get("type") or "事实", "pinned": False, "source": "user"}   # 用户手写
        entry["state"]["memories"].append(m)
        db.add_memory(user_id, m)
    return state_view(entry["state"], user_id)


@app.patch("/api/memories/{mid}")
async def api_patch_memory(mid: str, req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    if user.get("guest"):
        return memory_locked()
    user_id = user["userId"]
    body = await req.json()
    entry = load_state(user_id)
    with entry["lock"]:
        for m in entry["state"]["memories"]:
            if m["id"] == mid:
                if "pinned" in body:
                    m["pinned"] = bool(body["pinned"])
                if "text" in body:
                    m["text"] = str(body["text"])[:200]
                db.update_memory(user_id, m)
                break
    return state_view(entry["state"], user_id)


@app.delete("/api/memories/{mid}")
async def api_del_memory(mid: str, req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    if user.get("guest"):
        return memory_locked()
    user_id = user["userId"]
    entry = load_state(user_id)
    with entry["lock"]:
        entry["state"]["memories"] = [m for m in entry["state"]["memories"] if m["id"] != mid]
        db.delete_memory(user_id, mid)
    return state_view(entry["state"], user_id)


@app.get("/api/todos")
def api_todos(req: Request):
    """只取待办清单（不夹带整个 state）。

    为什么单独开一个：工作台是**另一个前端模块**，它只需要把 `S.todos` 换掉重画待办那一块。
    走 /api/state 会把整份状态（含 500 条聊天消息）重拉一遍，而且会把 `S` 整个替换掉 ——
    聊天正在流式打字时那一下会闪。给它一条最窄的路。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    s = load_state(user_id)["state"]
    return {"todos": sorted(s["todos"],
                            key=lambda t: (t["done"], t["due"] or float("inf"), -t["createdAt"]))}


@app.post("/api/todos")
async def api_add_todo(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    body = await req.json()
    text = str(body.get("text", "")).strip()[:200]
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)
    due = body.get("due")
    entry = load_state(user_id)
    with entry["lock"]:
        t = {"id": uid(), "text": text, "due": int(due) if due else None,
             "done": False, "reminded": False, "createdAt": now_ms()}
        entry["state"]["todos"].append(t)
        entry["state"]["stats"]["todosCreated"] = int(entry["state"]["stats"].get("todosCreated") or 0) + 1
        db.save_stats(user_id, entry["state"]["stats"])
        db.add_todo(user_id, t)
    return state_view(entry["state"], user_id)


@app.patch("/api/todos/{tid}")
async def api_patch_todo(tid: str, req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    body = await req.json()
    entry = load_state(user_id)
    with entry["lock"]:
        for t in entry["state"]["todos"]:
            if t["id"] == tid:
                if "done" in body:
                    t["done"] = bool(body["done"])
                db.update_todo(user_id, t)
                break
    return state_view(entry["state"], user_id)


@app.delete("/api/todos/{tid}")
async def api_del_todo(tid: str, req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    entry = load_state(user_id)
    with entry["lock"]:
        entry["state"]["todos"] = [t for t in entry["state"]["todos"] if t["id"] != tid]
        db.delete_todo(user_id, tid)
    return state_view(entry["state"], user_id)


@app.get("/api/reminders")
def api_reminders(req: Request):
    """返回刚到期的待办（并标记已提醒）。前端轮询。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    entry = load_state(user_id)
    now = now_ms()
    fired = []
    with entry["lock"]:
        for t in entry["state"]["todos"]:
            if not t["done"] and not t.get("reminded") and t.get("due") and t["due"] <= now:
                t["reminded"] = True
                db.update_todo(user_id, t)
                fired.append({"id": t["id"], "text": t["text"]})
    return {"fired": fired}


@app.put("/api/settings")
async def api_settings(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    body = await req.json()
    entry = load_state(user_id)
    with entry["lock"]:
        st = entry["state"]["settings"]
        old_pid = st.get("personaId")
        # 模型接口
        for k in ("apiUrl", "model"):
            if k in body:
                st[k] = str(body[k]).strip()[:600] or st.get(k)
        if body.get("apiKey"):  # 留空 = 保持原 Key
            st["apiKey"] = str(body["apiKey"]).strip()
        # 人格：只接受人格库里的 id，性格文案由后端锁定
        pid = str(body.get("personaId") or "").strip()
        if pid in PERSONAS:
            st["personaId"] = pid
        remember_persona(st.get("personaId"))   # 供下次「开机动画」读取
        # 对用户的称呼
        if "userName" in body:
            st["userName"] = str(body["userName"]).strip()[:20]
        if "autoExtract" in body:
            st["autoExtract"] = bool(body["autoExtract"])
        if "toolsEnabled" in body:
            st["toolsEnabled"] = bool(body["toolsEnabled"])
        if "showThink" in body:
            st["showThink"] = bool(body["showThink"])
        # ★ 人格任务分流：ask=复杂任务先推脱+一键切换 / auto=直接转交小玉
        if body.get("routeMode") in ("ask", "auto"):
            st["routeMode"] = body["routeMode"]
        if body.get("classifierMode") in ("rules", "hybrid", "llm"):
            st["classifierMode"] = body["classifierMode"]
        # 界面明暗：auto(跟随角色) / light / dark / system(跟随系统)
        if body.get("uiMode") in ("auto", "light", "dark", "system"):
            st["uiMode"] = body["uiMode"]
        if "lang" in body:
            st["lang"] = str(body["lang"]).strip()[:12] or "zh-CN"
        # 自定义头像（base64 dataURL，限制体积避免撑爆数据库；留空 = 清掉）
        if "avatar" in body:
            av = body["avatar"]
            if isinstance(av, str) and av.startswith("data:image/") and len(av) <= 3_000_000:
                st["avatar"] = av
            elif av in (None, ""):
                st.pop("avatar", None)
        apply_persona(st)
        # 换人陪聊时，新成员先说一句开场白（首次登场才说）
        switched = bool(old_pid) and old_pid != st["personaId"]
        db.save_settings(user_id, st)
    if switched:
        with entry["lock"]:
            # ★ 切换人格只做三件事：换 systemPrompt（settings.personaId）→ 落库 → 刷新 UI。
            #   它**不触发生成**，也**不再自动打招呼**（2026-09-16 拍板）——开场白只留给
            #   「新账号 / 空对话首次进入」（load_state 里的那个调用点）。
            #   历史里所有还没标 answered 的用户消息，一律视作「前一位成员已经处理过」，
            #   之后只能当只读上下文，绝不能再变成新的触发源 ——
            #   这正是「切个人格 AI 就把历史问题再答一遍」的根因所在。
            mark_history_answered(entry["state"], user_id)
    return state_view(entry["state"], user_id)


@app.get("/api/export")
def api_export(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    return JSONResponse(load_state(user["userId"])["state"])


@app.post("/api/import")
async def api_import(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    if user.get("guest"):
        return JSONResponse({"error": "访客模式不保存数据，登录后再导入备份吧"}, status_code=403)
    user_id = user["userId"]
    data = await req.json()
    if not isinstance(data, dict) or "settings" not in data or "chats" not in data:
        return JSONResponse({"error": "格式不对"}, status_code=400)
    merged = json.loads(json.dumps(DEFAULTS))
    for k in ("settings", "stats"):
        if isinstance(data.get(k), dict):
            merged[k].update(data[k])
    # ★ conversations 必须一起搬 —— 漏了的话：备份里明明有会话列表（export 会导出它），
    #   导入后却全没了，而消息还挂在那些会话上（界面上表现为"一条对话都看不到"）。
    #   实测：导入前 conversations=2，导入后 0。
    for k in ("memories", "chats", "todos", "conversations"):
        if isinstance(data.get(k), list):
            merged[k] = data[k]
    if not merged["stats"].get("createdAt"):
        merged["stats"]["createdAt"] = now_ms()
    db.replace_user_data(user_id, merged)
    # 失效内存缓存，下次请求重新加载
    with _cache_lock:
        _users.pop(user_id, None)
    return state_view(load_state(user_id)["state"], user_id)


@app.post("/api/reset")
async def api_reset(req: Request):
    """清空数据。★ 默认**保留接口设置**（API Key / 模型 / 称呼 / 主题 / 语言 / 各开关）。

    为什么改：原先是无条件全清 —— 而按钮文案只说「清空对话、记忆、待办与成长」，
    用户清完会莫名其妙变成演示模式，还得重新去填 API Key。
    真想连 Key 一起清，界面上有勾选框（传 `wipeSettings=true`）。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    try:
        body = await req.json()
    except Exception:
        body = {}
    wipe = bool((body or {}).get("wipeSettings"))
    fresh = json.loads(json.dumps(DEFAULTS))
    fresh["stats"]["createdAt"] = now_ms()
    if not wipe:
        keep = dict(load_state(user_id)["state"]["settings"])
        keep["convId"] = ""      # 会话都被清掉了，当前会话指针跟着归零（由 ensure_conversation 兜底重建）
        fresh["settings"] = keep
    db.replace_user_data(user_id, fresh)
    with _cache_lock:
        _users.pop(user_id, None)
    return state_view(load_state(user_id)["state"], user_id)


# ---------------- ★ 工程内核（agent）API ----------------
# 设计原则：陪伴路径（/api/chat 等）一行不改；agent 的能力全部走这里。
# 事件流：POST /api/agent/run 返回 SSE（带 seq），支持 GET .../events?after=N 回放与续传。

_agent_lock = threading.RLock()
_agent_approvals = {}          # run_id → HttpApproval
_agent_pending = {}            # run_id → 等待裁决时的上下文（给 UI 展示用）
_agent_kernels = {}            # run_id → Kernel（用于 stop）
_agent_conns = {}              # thread_id → 连接（线程内复用，避免跨线程共享 pymysql 连接）

# 单价表默认**留空**：内置一份参考价一定会过期，而过期的价格会被当成真的用。
# 空着时界面只显示 token；填了才折算金额。
DEFAULT_PRICES = {"in": 0.0, "out": 0.0, "currency": "CNY", "note": ""}


def _agent_conn():
    """每个线程一条连接（pymysql 连接不能跨线程共用）。"""
    key = threading.get_ident()
    with _agent_lock:
        c = _agent_conns.get(key)
        if c is None:
            c = ag_storage.connect()
            _agent_conns[key] = c
        else:
            try:
                c.ping(reconnect=True)
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                c = ag_storage.connect()
                _agent_conns[key] = c
        return c


def _sse(obj) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


class HttpApproval:
    """把「要审批」变成一次 HTTP 往返：内核挂起，前端拿到审批卡后 POST 裁决。

    超时（默认 120s）= 拒绝 —— 安全默认：没人回应就等于不批准。
    """

    def __init__(self, run_id: str, timeout: float = 120.0):
        self.run_id = run_id
        self.timeout = timeout
        self.event = threading.Event()
        self.result = {"verdict": ag_common.DENY, "scope": "once"}

    def __call__(self, info: dict) -> dict:
        with _agent_lock:
            _agent_pending[self.run_id] = info
        self.event.clear()
        got = self.event.wait(self.timeout)
        with _agent_lock:
            _agent_pending.pop(self.run_id, None)
        if not got:
            return {"verdict": ag_common.DENY, "scope": "once", "timeout": True}
        return dict(self.result)

    def decide(self, verdict: str, scope: str = "once") -> None:
        self.result = {"verdict": verdict, "scope": scope}
        self.event.set()


def _load_agent_ws(user_id: int, body: dict):
    """解析这次要动哪个工作区：body.path 优先（并顺手注册），否则用最近使用的。"""
    conn = _agent_conn()
    path = (body.get("path") or "").strip()
    if path:
        p = Path(path).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"目录不存在：{p}")
        ws = AgWorkspace.load(p)
        ws.save_config()
        ws_id = ag_storage.upsert_workspace(conn, user_id, ws.cfg.name, str(ws.root),
                                            ws.cfg.to_dict())
        return ws, ws_id
    ws_id = int(body.get("workspaceId") or 0)
    row = ag_storage.get_workspace(conn, user_id, ws_id) if ws_id else None
    if not row:
        rows = ag_storage.list_workspaces(conn, user_id)
        row = rows[0] if rows else None
    if not row:
        raise FileNotFoundError("还没有注册任何工作区：请先指定一个项目目录")
    root = Path(row["root_path"])
    if not root.is_dir():
        raise FileNotFoundError(f"工作区目录已不存在：{root}")
    ws = AgWorkspace.load(root)
    return ws, row["id"]


def _agent_model_for(s: dict):
    st = s["settings"]
    return ag_model.make_model(st.get("apiUrl", ""), st.get("apiKey", ""), st.get("model", ""),
                               temperature=0.2)


def _deny_guest(user):
    """工程功能要落库（运行记录 / 快照 / 工程记忆），访客态一律不落库 → 明确拒绝并给指引。

    取舍：宁可能说清「为什么现在不行」，也不要让访客点了按钮却悄悄什么都没发生。
    """
    if user and user.get("guest"):
        return JSONResponse({"error": "工程委托需要登录：访客模式不落库，运行记录与快照无法保存。"
                                      "先注册一个账号，再把项目目录交给小玉。"}, status_code=403)
    return None


@app.get("/api/agent/status")
async def api_agent_status(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    s = load_state(user_id)["state"]
    from yorozuya.agent.tools import backend_name
    from yorozuya.agent.permissions import tier_of
    level = get_level(s)
    guest = bool(user.get("guest"))
    rows = [] if guest else ag_storage.list_workspaces(_agent_conn(), user_id)
    runs = [] if guest else ag_storage.list_runs(_agent_conn(), user_id, 20)
    return {
        "ok": True, "build": ag_common.BUILD_STAMP,
        "search": backend_name(),
        "hasKey": bool(s["settings"].get("apiKey")),
        "model": s["settings"].get("model", ""),
        "level": level, "tier": tier_of(level),
        "routeMode": s["settings"].get("routeMode") or "ask",
        "classifierMode": s["settings"].get("classifierMode") or "rules",
        "taskPersona": ag_router.TASK_PERSONA,
        "needLogin": guest,
        "workspaces": [] if guest else [_ws_row(r) for r in rows],
        "runs": [{"id": r["id"], "goal": r["goal"], "status": r["status"], "ws": r.get("ws_name"),
                  "createdAt": r["created_at"], "steps": r["steps"], "persona": r.get("persona_id")}
                 for r in runs],
    }


@app.get("/api/agent/workspaces")
async def api_agent_workspaces(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    conn = _agent_conn()
    rows = ag_storage.list_workspaces(conn, user["userId"])
    return [_ws_row(r) for r in rows]


@app.delete("/api/agent/workspaces/{ws_id}")
async def api_agent_ws_delete(req: Request, ws_id: int):
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    ag_storage.delete_workspace(_agent_conn(), user["userId"], ws_id)
    return {"ok": True}


@app.post("/api/agent/run")
async def api_agent_run(req: Request):
    """跑一次委托。返回 SSE 事件流（run.start / tool.call / tool.approval / fs.diff / verify / …）。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    user_id = user["userId"]
    body = await req.json()
    goal = str(body.get("goal") or "").strip()
    if not goal:
        return JSONResponse({"error": "empty"}, status_code=400)
    state = load_state(user_id)["state"]
    s = state
    # 复杂度判定：工程委托一般就是 complex；这里仍如实标注，供报告与 UI 显示
    cls = ag_classifier.classify(goal, mode=(s["settings"].get("classifierMode") or "rules"))
    persona_id = str(body.get("personaId") or ag_router.TASK_PERSONA)
    if cls.is_complex:
        persona_id = ag_router.TASK_PERSONA          # 复杂任务归小玉（与聊天侧一致的定位）
    level = get_level(s)
    auto_ok = bool(body.get("autoApprove", False))
    # ★ 工作台 · 待办直通：「这条委托是从哪条待办派出去的」。
    #   只校验它确实属于当前用户 —— 校验不过就当没传（不报错：委托本身照样能跑）。
    todo_id = str(body.get("todoId") or "").strip()[:24]
    if todo_id and not any(t.get("id") == todo_id for t in s["todos"]):
        todo_id = ""
    # 跑完要不要把结论作为一条消息落进对话（前端勾选框，默认开）
    to_chat = bool(body.get("toChat", True))

    def gen():
        ag_common.ensure_dirs()
        run_id = ag_common.new_id()
        approval_timeout = float(body.get("approvalTimeout") or 120)
        approvals = HttpApproval(run_id, timeout=approval_timeout)
        # ★ 演示模式（没配 API Key）：动作由内核按固定脚本发起，且**强制**只在专用演示工作区里跑，
        #   绝不碰用户登记的真实项目 —— 这是入口处的硬跳转，不靠提示词自觉。
        demo_on = _agent_model_for(s) is None
        try:
            if demo_on:
                conn0 = _agent_conn()
                ws = AgWorkspace.load(ag_demo.reset_demo_workspace())
                ws.cfg.name = ag_demo.DEMO_WS_NAME
                ws.save_config()
                ws_id = ag_storage.upsert_workspace(conn0, user_id, ws.cfg.name, str(ws.root),
                                                    ws.cfg.to_dict())
            else:
                ws, ws_id = _load_agent_ws(user_id, body)
        except Exception as e:
            yield _sse({"type": "notice", "payload": {"level": "error", "text": str(e)}})
            yield _sse({"type": "run.end", "payload": {"status": "error", "error": str(e)}})
            return

        def build_kernel():
            """★ 在内核自己的工作线程里建连接。

            踩过的坑：内核跑在独立线程，如果复用请求线程的 pymysql 连接，
            写入（事件落库）会跨线程失败并被 `_emit` 的 try/except 吞掉 ——
            表现为「事件流能看，但回放是空的」。所以连接必须跟线程走。
            """
            conn = _agent_conn()
            model = _agent_model_for(s) or ag_demo.demo_model()
            k = AgKernel(conn, ws, model, user_id=user_id, workspace_id=ws_id,
                         persona_id=persona_id, level=level,
                         approval_provider=approvals, auto_approve=auto_ok,
                         demo_note=(ag_demo.DEMO_NOTE if demo_on else ""),
                         # M3：MCP 服务器由工作区声明（agent.config.json 的 "mcp"）。
                         # 没配就一个 mcp_* 工具都不注册 —— 免得模型看见工具却调不通。
                         mcp_configs=getattr(ws.cfg, "mcp", None),
                         todo_id=todo_id)
            k.run_id = run_id
            with _agent_lock:
                _agent_kernels[run_id] = k
            return k

        with _agent_lock:
            _agent_approvals[run_id] = approvals
        # 先告诉前端这次 run 的 id（审批、回放、undo 都要用它）
        yield _sse({"type": "run.ready", "payload": {"run_id": run_id, "workspace": ws.cfg.name,
                                                     "root": str(ws.root), "persona": persona_id,
                                                     "taskLevel": cls.level, "level": level,
                                                     "demo": demo_on, "todoId": todo_id,
                                                     "toChat": to_chat,
                                                     "approvalTimeout": approval_timeout,
                                                     "autoApprove": auto_ok}})

        # ★ 收尾动作放在**内核线程结束时**，而不是 SSE 生成器的 finally 里：
        #   客户端断线时生成器会被关掉，此时内核还在跑 —— 在 finally 里做收尾会读到
        #   一个还没结束的状态（甚至什么都没做）。挂在这里，断线也照样收尾。
        done = {"status": ""}

        def on_finish(status: str) -> None:
            done["status"] = status
            try:
                _agent_after_run(user_id, run_id, todo_id, to_chat, status)
            except Exception:
                _agent_log("收尾失败：\n%s" % traceback.format_exc())

        try:
            for ev in _iter_kernel(build_kernel, goal, cls.level, on_finish):
                yield _sse({"type": ev["type"], "seq": ev["seq"], "payload": ev["payload"]})
        finally:
            with _agent_lock:
                _agent_approvals.pop(run_id, None)
                _agent_kernels.pop(run_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


def _iter_kernel(build_kernel, goal: str, task_level: str, on_finish=None):
    """把内核（同步、跑在独立线程）的事件变成生成器按需 yield。

    注意：内核在**自己的线程**里创建（连同数据库连接），不要在请求线程里建好再传进来 ——
    pymysql 连接不能跨线程用，否则事件落库会静默失败（表现为「流里有、回放里没有」）。

    `on_finish(status)`：内核真的跑完后在**同一个工作线程**里回调一次。收尾动作挂这里，
    是因为 SSE 生成器可能被客户端断开而提前结束，那一刻内核其实还在跑。
    """
    queue: list = []

    def on_event(ev: dict) -> None:
        queue.append(ev)

    def worker():
        status = "error"
        try:
            kernel = build_kernel()
            kernel.on_event = on_event
            res = kernel.run(goal, task_level=task_level, run_id=kernel.run_id)
            status = getattr(res, "status", "") or "error"
        except Exception as e:            # 内核崩了也要让前端看到原因，不要静默结束
            on_event({"type": ag_common.Ev.NOTICE, "seq": 0,
                      "payload": {"level": "error", "text": f"内核异常：{type(e).__name__}: {e}"}})
            on_event({"type": ag_common.Ev.RUN_END, "seq": 0,
                      "payload": {"status": "error", "error": str(e)}})
        if on_finish:
            try:
                on_finish(status)
            except Exception:
                _agent_log("on_finish 回调失败：\n%s" % traceback.format_exc())

    th = threading.Thread(target=worker, daemon=True, name="agent-kernel")
    th.start()
    sent = 0
    while th.is_alive() or sent < len(queue):
        if sent < len(queue):
            ev = queue[sent]
            sent += 1
            yield ev
        else:
            time.sleep(0.05)
    th.join(timeout=5)


# ---------------- 工程委托的收尾（待办直通 / 结论回对话）----------------
# 这一组函数都由 `_iter_kernel` 在**内核线程**里调用。所以：
#   · 连接必须自己取（`_agent_conn()` 是按线程持有的），不能复用请求线程的；
#   · 只做落库与状态更新，不做任何 yield / 不依赖 HTTP 连接。

_AGENT_STATUS_TXT = {
    "done": "完成", "verify_failed": "验证未通过", "unverified": "做完了但没验证",
    "budget": "到预算上限收尾", "stopped": "被你停掉", "error": "出错", "blocked": "被拦下",
    "running": "还在跑",
}


def _agent_log(msg) -> None:
    """收尾动作的日志（打包版会进 yorozuya-start.log，排查「待办怎么没自动勾掉」靠它）。"""
    print("[agent] " + str(msg), file=sys.stderr, flush=True)


def _agent_changed_files(conn, run_id: str) -> list:
    """这次运行到底动了哪些文件（取快照记录里的路径，去重保序）。"""
    out: list = []
    for ck in ag_storage.list_checkpoints(conn, run_id):
        try:
            for rel in json.loads(ck.get("files") or "{}"):
                if rel not in out:
                    out.append(rel)
        except Exception:
            pass
    return out


def _agent_conclusion_text(conn, run: dict, todo=None, task=None) -> str:
    """把一次运行压成**一条能直接读**的对话消息。

    为什么不用 Markdown 标题/列表：这条消息就是「小玉说的话」，而人格的统一样式约束里
    已经写着口语化、不要标题与列表 —— 工具产物和角色台词不能两套风格。
    """
    status = str(run.get("status") or "")
    head = _AGENT_STATUS_TXT.get(status, status or "结束")
    try:
        v = json.loads(run.get("verification") or "{}")
    except Exception:
        v = {}
    if v.get("ran"):
        verify = "验证：" + ("通过" if v.get("ok") else "未通过") + "（" + str(v.get("cmd") or "") + "）"
        if v.get("baseline_failed"):
            verify += "，改动前它本来就是红的"
    elif status == "unverified":
        verify = "没验证：" + str(v.get("note") or "这个工作区没配验证命令，我没法自己证明")
    else:
        verify = "没跑验证"
    files = _agent_changed_files(conn, run.get("id") or "")
    L = ["🛠 工作台 · " + head,
         "你让我做的：" + (run.get("goal") or "").strip()[:90]]
    if todo:
        L.append("来自待办：" + (todo.get("text") or "")
                 + ("（已自动勾掉）" if todo.get("auto") else "（待办还挂着，你自己确认一下）"))
    L.append(verify + "｜" + str(run.get("steps") or 0) + " 步 / "
             + str(run.get("tool_calls") or 0) + " 次工具调用")
    L.append("改了：" + ("、".join(files[:8]) + ("…" if len(files) > 8 else "")
                        if files else "没动任何文件"))
    summary = (run.get("summary") or "").strip()
    if summary:
        L.append("我的结论：" + summary.replace(chr(10), " ")[:300])
    if status == "error" and run.get("error"):
        L.append("报错：" + str(run["error"]).replace(chr(10), " ")[:200])
    # 留档：这次任务的文件夹就在项目里，说清在哪她才知道有这回事
    if isinstance(task, dict) and task.get("ok") and task.get("rel"):
        L.append("留档：" + str(task["rel"]) + "（这次改了什么都包在里面了）")
    return chr(10).join(L)[:900]


def _agent_complete_todo(user_id, todo_id: str, status: str):
    """从待办派出去、且**验证通过**的那条待办 → 自动勾掉。

    为什么只在 `done` 时自动勾：`done` 的确切含义是「内核自己跑过验证命令并且通过了」
    （或这次根本没动文件、没什么可验证的）。`unverified` 是「工作区没配验证命令，
    我证明不了」—— 那种情况只提示、不替你把结论下了。这是本项目的验证闸门口径，
    不因为「想让它自动」就放宽。
    """
    entry = load_state(user_id)
    s = entry["state"]
    t = next((x for x in (s.get("todos") or []) if x.get("id") == todo_id), None)
    if not t:
        return None
    if status != "done":
        return {"id": todo_id, "text": t.get("text") or "", "done": bool(t.get("done")),
                "auto": False, "reason": status}
    if t.get("done"):
        return {"id": todo_id, "text": t.get("text") or "", "done": True,
                "auto": False, "reason": "already"}
    with entry["lock"]:
        t["done"] = True
        db.update_todo(user_id, t)
    return {"id": todo_id, "text": t.get("text") or "", "done": True, "auto": True, "reason": status}


def _agent_post_conclusion(user_id, run: dict, todo=None, task=None):
    """把小玉的结论作为**一条消息**落进当前会话（不触发模型调用）。

    幂等：同一次运行只写一条（按消息上的 `agent.runId` 判重）——
    收尾会被「内核线程」和「前端来问效果」两条路碰到，判重必须落在数据上。
    """
    run_id = str(run.get("id") or "")
    if not run_id:
        return None
    entry = load_state(user_id)
    s = entry["state"]
    if any(((m.get("agent") or {}).get("runId") == run_id) for m in (s.get("chats") or [])):
        return {"posted": False, "reason": "already"}
    if str(run.get("status") or "") in ("running", ""):
        return None
    text = _agent_conclusion_text(_agent_conn(), run, todo, task)
    with entry["lock"]:
        conv_id = ensure_conversation(s, user_id)
        msg = {"id": uid(), "ts": now_ms(), "role": "ai", "text": text,
               "pid": run.get("persona_id") or ag_router.TASK_PERSONA, "conv": conv_id,
               "agent": {"runId": run_id, "status": run.get("status") or ""}}
        s["chats"].append(msg)
        s["stats"]["totalAI"] = int(s["stats"].get("totalAI") or 0) + 1
        touch_conversation(s, conv_id, user_id)
        db.insert_chat(user_id, msg)
        db.save_stats(user_id, s["stats"])
    return {"posted": True, "msgId": msg["id"], "text": text, "conv": conv_id}


def _agent_after_run(user_id, run_id: str, todo_id: str, to_chat: bool, status: str) -> dict:
    """一次委托跑完之后的三件收尾：**打包留档**、勾待办、把结论发到对话里。"""
    out = {"runId": run_id, "status": status, "todo": None, "chat": None, "task": None}
    try:
        run = ag_storage.get_run(_agent_conn(), run_id) or {}
    except Exception:
        run = {}
    # ★ 任务留档：在工作区里给它包一个文件夹（`.yorozuya/runs/<时间>-<runId>/`）。
    #   挂在这里（内核线程真正结束时）而不是运行过程中：① 不会被算进"这次改动的文件"，
    #   也不会污染验证闸门 ② 快照与事件都已落库，patch/报告才是完整的。
    try:
        out["task"] = _agent_pack_task(user_id, run_id, run)
    except Exception:
        _agent_log("任务留档失败：" + traceback.format_exc())
    if todo_id:
        try:
            out["todo"] = _agent_complete_todo(user_id, todo_id, status)
        except Exception:
            _agent_log("自动勾待办失败：" + traceback.format_exc())
    if to_chat:
        try:
            out["chat"] = _agent_post_conclusion(user_id, run, out["todo"], out.get("task"))
        except Exception:
            _agent_log("结论回对话失败：" + traceback.format_exc())
    return out


def _agent_pack_task(user_id: int, run_id: str, run: dict) -> dict:
    """把这次运行打包成工作区里的一个文件夹。

    失败**不影响**委托本身（只记一条日志、界面上也就不显示这个入口）——
    留档是加分项，不能因为它写不出来而把一次成功的委托判成失败。
    """
    conn = _agent_conn()
    ws_id = run.get("workspace_id") or 0
    row = ag_storage.get_workspace(conn, user_id, int(ws_id)) if ws_id else None
    if not row:
        return {"ok": False, "error": "没有对应的工作区（演示工作区 / 已移除的工作区跳过留档）"}
    root = Path(row["root_path"])
    if not root.is_dir():
        return {"ok": False, "error": f"工作区目录已不存在：{root}"}
    try:
        ws = AgWorkspace.load(root)
    except Exception as e:                                          # noqa: BLE001
        return {"ok": False, "error": f"加载工作区失败：{e}"}
    run2 = dict(run)
    run2["root_path"] = str(root)
    got = ag_taskfolder.write_task_folder(conn, run2, ws, snaps=ag_snapshot.SnapshotStore(conn))
    if got.get("ok"):
        _agent_log(f"任务留档已写入：{got.get('rel')}")
    else:
        _agent_log(f"任务留档未完成：{got.get('error')}")
    return got


@app.get("/api/agent/runs/{run_id}/effects")
async def api_agent_effects(req: Request, run_id: str):
    """「这次委托跑完之后：待办勾了没 / 结论发到对话了没」。

    为什么需要一个单独接口：收尾发生在内核线程里，而 run.end 事件是**收尾之前**推出去的。
    前端在 SSE 流结束后再来问一次，拿到的才是最终结果
    （流关闭 ⇒ 服务端生成器已走完 ⇒ 收尾一定已经做完）。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    user_id = user["userId"]
    conn = _agent_conn()
    run = ag_storage.get_run(conn, run_id)
    if not run or run.get("user_id") != user_id:
        return JSONResponse({"error": "找不到这次运行"}, status_code=404)
    status = str(run.get("status") or "")
    if status == "running":
        return {"ok": True, "ready": False, "runId": run_id, "status": status}
    s = load_state(user_id)["state"]
    todo_id = str(run.get("todo_id") or "")
    todo = next((x for x in (s.get("todos") or []) if x.get("id") == todo_id), None)
    msg = next((m for m in (s.get("chats") or [])
                if ((m.get("agent") or {}).get("runId") == run_id)), None)
    return {"ok": True, "ready": True, "runId": run_id, "status": status,
            "todo": ({"id": todo_id, "text": todo.get("text") or "", "done": bool(todo.get("done")),
                      "auto": bool(todo.get("done") and status == "done")} if todo else None),
            "chat": ({"posted": True, "msgId": msg["id"]} if msg else {"posted": False}),
            "files": _agent_changed_files(conn, run_id),
            # 任务留档在哪（跑完才写，所以只有这里才拿得到；前端用它补在结果卡上）
            "task": ({"ok": True, "rel": run.get("task_dir")} if run.get("task_dir") else None)}


@app.post("/api/agent/runs/{run_id}/approval")
async def api_agent_approval(req: Request, run_id: str):
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    body = await req.json()
    verdict = str(body.get("verdict") or "deny")
    scope = str(body.get("scope") or "once")
    if verdict not in (ag_common.ALLOW, ag_common.DENY):
        verdict = ag_common.DENY
    with _agent_lock:
        ap = _agent_approvals.get(run_id)
        info = _agent_pending.get(run_id) or {}
    if not ap:
        return {"ok": False, "error": "这个 run 已经不在等待审批（可能超时或已结束）", "tool": info.get("tool")}
    ap.decide(verdict, scope)
    return {"ok": True, "verdict": verdict, "scope": scope}


@app.post("/api/agent/runs/{run_id}/stop")
async def api_agent_stop(req: Request, run_id: str):
    user = auth_user(req)
    if not user:
        return unauthorized()
    with _agent_lock:
        k = _agent_kernels.get(run_id)
    if k:
        k.stop()
        return {"ok": True}
    return {"ok": False, "error": "该 run 不在运行中"}


@app.get("/api/agent/runs")
async def api_agent_runs(req: Request, limit: int = 20):
    user = auth_user(req)
    if not user:
        return unauthorized()
    rows = ag_storage.list_runs(_agent_conn(), user["userId"], max(1, min(limit, 100)))
    return [_run_row(r) for r in rows]


def _run_row(r: dict) -> dict:
    """一次运行给前端的形状（工作台列表与成本仪表共用，避免两处口径不一致）。"""
    try:
        verification = json.loads(r.get("verification") or "{}")
    except Exception:
        verification = {}
    return {"id": r["id"], "goal": r["goal"], "status": r["status"], "ws": r.get("ws_name"),
            "workspaceId": r.get("workspace_id"), "createdAt": r["created_at"],
            "endedAt": r.get("ended_at") or 0, "steps": r["steps"], "toolCalls": r["tool_calls"],
            "tokensIn": r.get("tokens_in") or 0, "tokensOut": r.get("tokens_out") or 0,
            "seconds": r.get("seconds") or 0, "model": r.get("model") or "",
            "persona": r.get("persona_id"), "error": r.get("error") or "",
            "todoId": r.get("todo_id") or "",
            "taskDir": r.get("task_dir") or "",
            "verification": verification,
            "summary": (r.get("summary") or "")[:2000],
            "demo": str(r.get("model") or "").startswith("demo:")}


@app.delete("/api/agent/runs/{run_id}")
async def api_agent_run_delete(req: Request, run_id: str):
    """删除一次运行记录（只允许删自己的）。

    删到哪一层（她拍板的）：
    · 库里那一行 + 它的事件 / 工具调用 / 审批 / 检查点 / 产物记录 —— **全删**
    · 这次的任务留档文件夹（工作区里的 `.yorozuya/runs/<时间>-<runId>/`）—— 删
    · 这次生成的大输出产物文件（artifacts 目录里的那几个）—— 删
    · **快照 blob 不删**：它是内容寻址的、可能被别的运行共用；留着也正是"回滚依然安全"的原因
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    return _agent_delete_runs(user["userId"], [run_id])


@app.post("/api/agent/runs/purge")
async def api_agent_runs_purge(req: Request):
    """批量删除运行记录（工作台「清空这些」用它）。

    body: {"ids": ["...", ...]} —— **由前端把它当前看到的那批 id 发过来**，
    不按筛选条件在服务端重新算一遍：所见即所删，不会因为期间又跑了一次而多删。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    body = await req.json()
    ids = [str(x) for x in (body.get("ids") or [])]
    if not ids:
        return JSONResponse({"error": "没有要删除的记录"}, status_code=400)
    if len(ids) > 200:
        return JSONResponse({"error": "一次最多删 200 条，分批来吧"}, status_code=400)
    return _agent_delete_runs(user["userId"], ids)


def _agent_delete_runs(user_id: int, ids: list) -> dict:
    """删库 + 删磁盘（留档文件夹与产物文件）。磁盘删不掉**不阻止**库里的删除。"""
    conn = _agent_conn()
    out = {"ok": True, "deleted": 0, "runs": [], "taskDirs": [], "artifacts": 0, "errors": []}
    for rid in ids:
        # 先把"要删的文件"记下来（行删掉之后就查不到了）
        run = None
        try:
            run = ag_storage.get_run(conn, rid)
        except Exception:
            run = None
        if not run or int(run.get("user_id") or 0) != int(user_id):
            out["errors"].append(f"{rid}: 没有这次运行（或不属于你）")
            continue
        files = []
        try:
            files = [a.get("path") for a in ag_storage.list_artifacts(conn, rid) if a.get("path")]
        except Exception:
            files = []
        got = ag_storage.delete_run(conn, user_id, rid)
        if not got.get("ok"):
            out["errors"].append(f"{rid}: {got.get('error')}")
            continue
        out["deleted"] += 1
        out["runs"].append(rid)

        # ① 产物文件（只删 artifacts 目录里的，防止数据被拼出越界路径）
        base = Path(ag_common.ARTIFACTS_DIR).resolve()
        for fp in files:
            try:
                p = Path(fp).resolve()
                if str(p).startswith(str(base)) and p.is_file():
                    p.unlink()
                    out["artifacts"] += 1
            except Exception as e:                                   # noqa: BLE001
                out["errors"].append(f"{rid}: 产物 {fp} 删不掉（{e}）")

        # ② 任务留档文件夹（安全检查在 remove_task_folder 里：只允许删工作区内那一个子目录）
        rel = got.get("task_dir") or ""
        if rel:
            try:
                wsrow = ag_storage.get_workspace(conn, user_id, int(run.get("workspace_id") or 0))
                if not wsrow:
                    out["errors"].append(f"{rid}: 找不到工作区，留档目录未清理（{rel}）")
                elif not Path(wsrow["root_path"]).is_dir():
                    out["errors"].append(f"{rid}: 工作区目录已不存在，留档目录未清理（{rel}）")
                else:
                    ws = AgWorkspace.load(Path(wsrow["root_path"]))
                    done, why = ag_taskfolder.remove_task_folder(ws, rel)
                    if done:
                        out["taskDirs"].append(rel)
                    else:
                        out["errors"].append(f"{rid}: 留档目录未清理（{why}）")
            except Exception as e:                                   # noqa: BLE001
                out["errors"].append(f"{rid}: 留档目录删不掉（{type(e).__name__}: {e}）")
    out["ok"] = not out["errors"] or out["deleted"] > 0
    return out


@app.get("/api/agent/runs/{run_id}/pending")
async def api_agent_pending(req: Request, run_id: str):
    """断线续传：重连时先问一句「现在是不是正卡在审批上」。

    没有这个接口的话，刷新页面就只能看到流断在 tool.approval 之前，
    审批卡再也回不来 —— 内核却还在挂起等你（120s 到点自动拒绝）。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    with _agent_lock:
        info = _agent_pending.get(run_id)
        alive = run_id in _agent_kernels
    return {"run_id": run_id, "alive": bool(alive), "pending": info or None}


@app.get("/api/agent/stats")
async def api_agent_stats(req: Request):
    """成本与步数仪表：聚合在 SQL 里做，前端只负责画。

    访客直接拒绝：工程数据一个字节都不给访客落库，给一张「全 0 的仪表」只会让人以为
    「我跑过但没记上」。明确说「需要登录」比一个空表格诚实。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    conn = _agent_conn()
    user_id = user["userId"]
    out = {"ok": True, "stats": ag_storage.stats(conn, user_id),
           "prices": ag_storage.get_setting(conn, user_id, "prices", DEFAULT_PRICES)}
    runs = ag_storage.list_runs(conn, user_id, 50)
    out["runs"] = [_run_row(r) for r in runs]
    return out


@app.get("/api/agent/prices")
async def api_agent_prices(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    conn = _agent_conn()
    return {"prices": ag_storage.get_setting(conn, user["userId"], "prices", DEFAULT_PRICES)}


@app.post("/api/agent/prices")
async def api_agent_prices_set(req: Request):
    """单价表（每百万 token 的钱）。

    为什么默认是空的而不是内置一份参考价：内置价格表一定会过期，
    而过期的价格会被当成真的用。空着 + 让你自己填，界面只显示 token，
    这是唯一不会撒谎的做法。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    body = await req.json()
    try:
        pin = float(body.get("in") or 0)
        pout = float(body.get("out") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "单价必须是数字"}, status_code=400)
    if pin < 0 or pout < 0:
        return JSONResponse({"error": "单价不能为负"}, status_code=400)
    val = {"in": pin, "out": pout, "currency": str(body.get("currency") or "CNY")[:8],
           "note": str(body.get("note") or "")[:80]}
    ag_storage.set_setting(_agent_conn(), user["userId"], "prices", val)
    return {"ok": True, "prices": val}


@app.post("/api/agent/workspaces")
async def api_agent_ws_add(req: Request):
    """注册一个工作区（登记 + 写 agent.config.json / AGENT.md，**不跑任何命令**）。

    ★ 顺手做一件她踩过的事：**没配验证命令就自动探测一条**。
    她登记的工作区 `verify_cmds` 是空的 → 内核没有命令可跑 → 每次运行都只能标 `unverified`
    → 她看到的就是"工作台干的活没法验证"。这里按项目特征（package.json / pom.xml / pytest…）
    给一条能真跑的；**探测不出来就留空**（猜错的命令会让每次都"验证未通过"，比没有更糟），
    并把探测结果如实回报给界面。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    body = await req.json()
    raw = str(body.get("path") or "").strip().strip('"')
    if not raw:
        return JSONResponse({"error": "请填写项目目录"}, status_code=400)
    p = Path(raw).expanduser()
    if not p.is_dir():
        return JSONResponse({"error": f"目录不存在：{p}"}, status_code=400)
    try:
        ws = AgWorkspace.load(p)
        detected = []
        if not ws.cfg.verify_list():
            detected = ag_workspace.detect_verify_cmds(ws.root)
            if detected:
                ws.cfg.verify_cmds = detected
        ws.save_config()
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=400)
    conn = _agent_conn()
    ws_id = ag_storage.upsert_workspace(conn, user["userId"], ws.cfg.name, str(ws.root),
                                        ws.cfg.to_dict())
    return {"ok": True, "detected": detected,
            "workspace": _ws_row({**ws.cfg.to_dict(), "id": ws_id,
                                  "root_path": str(ws.root)})}


@app.post("/api/agent/workspaces/{ws_id}/detect")
async def api_agent_ws_detect(req: Request, ws_id: int):
    """重新探测这个工作区的验证命令（**只认得出时才改**，认不出保持原样）。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    row = ag_storage.get_workspace(_agent_conn(), user["userId"], ws_id)
    if not row:
        return JSONResponse({"error": "找不到这个工作区"}, status_code=404)
    root = Path(row["root_path"])
    if not root.is_dir():
        return JSONResponse({"error": f"目录已不存在：{root}"}, status_code=400)
    found = ag_workspace.detect_verify_cmds(root)
    if found:
        ws = AgWorkspace.load(root)
        ws.cfg.verify_cmds = found
        ws.save_config()
        ag_storage.upsert_workspace(_agent_conn(), user["userId"], ws.cfg.name, str(ws.root),
                                    ws.cfg.to_dict())
    return {"ok": True, "detected": found, "changed": bool(found)}


@app.patch("/api/agent/workspaces/{ws_id}")
async def api_agent_ws_patch(req: Request, ws_id: int):
    """改一个工作区的验证命令（**必须能改**：探测不可能覆盖所有项目）。

    body: {"verifyCmds": ["..."]}（字符串或数组都收；空数组 = 明确不要验证）
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    row = ag_storage.get_workspace(_agent_conn(), user["userId"], ws_id)
    if not row:
        return JSONResponse({"error": "找不到这个工作区"}, status_code=404)
    body = await req.json()
    given = body.get("verifyCmds")
    if given is None:
        return JSONResponse({"error": "缺少 verifyCmds"}, status_code=400)
    if isinstance(given, str):
        cmds = [given]
    else:
        cmds = list(given or [])
    cmds = [str(c).strip() for c in cmds if str(c).strip()][:5]      # 最多 5 条，别的没意义
    root = Path(row["root_path"])
    if not root.is_dir():
        return JSONResponse({"error": f"目录已不存在：{root}"}, status_code=400)
    ws = AgWorkspace.load(root)
    ws.cfg.verify_cmds = cmds
    ws.cfg.test_cmd = ""                    # 老字段清掉，避免 verify_list 里两条并存造成困惑
    ws.save_config()
    ws_id2 = ag_storage.upsert_workspace(_agent_conn(), user["userId"], ws.cfg.name, str(ws.root),
                                         ws.cfg.to_dict())
    return {"ok": True, "verifyCmds": cmds,
            "workspace": _ws_row({**ws.cfg.to_dict(), "id": ws_id2, "root_path": str(ws.root)})}


def _ws_row(r: dict) -> dict:
    root = r.get("root_path") or ""
    cfg = {k: r.get(k) for k in ("verify_cmds", "test_cmd", "budget", "policy",
                                 "allow_prefixes", "deny_patterns")}
    try:
        verify = json.loads(r["verify_cmds"]) if isinstance(r.get("verify_cmds"), str) else \
            (r.get("verify_cmds") or [])
    except Exception:
        verify = []
    try:
        full = json.loads(r["config_json"]) if isinstance(r.get("config_json"), str) else \
            (r.get("config_json") or {})
    except Exception:
        full = {}
    if not full and isinstance(r.get("mcp"), dict):
        full = {"mcp": r["mcp"]}          # 注册接口直接把 cfg 字典递进来（不经 config_json）
    mcp = full.get("mcp") if isinstance(full, dict) else {}
    test_cmd = r.get("test_cmd") or ""
    return {"id": r.get("id"), "name": r.get("name") or Path(root).name, "root": root,
            "exists": Path(root).is_dir() if root else False,
            "lastUsed": r.get("last_used_at") or 0,
            "verifyCmds": [c for c in (verify or []) if c] or ([test_cmd] if test_cmd else []),
            # M3：本工作区声明的 MCP 服务器（只暴露名字与命令，env 里可能有密钥，不下发）
            "mcpServers": [{"name": n, "command": (v or {}).get("command") or "",
                            "args": [str(a) for a in ((v or {}).get("args") or [])]}
                           for n, v in (mcp or {}).items()] if isinstance(mcp, dict) else [],
            "protected": sorted(ag_common.PROTECTED_DIRS), "cfgKeys": sorted(cfg)}


@app.get("/api/agent/workspaces/{ws_id}/git")
async def api_agent_ws_git(req: Request, ws_id: int):
    """工作区的只读 git 概览（分支 / 脏文件数 / 最近提交）。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    row = ag_storage.get_workspace(_agent_conn(), user["userId"], ws_id)
    if not row:
        raise HTTPException(404, "找不到这个工作区")
    return {"workspaceId": ws_id, "root": row["root_path"],
            "git": ag_gitinfo.git_info(Path(row["root_path"]))}


@app.get("/api/agent/runs/{run_id}/events")
async def api_agent_events(req: Request, run_id: str, after: int = 0):
    user = auth_user(req)
    if not user:
        return unauthorized()
    rows = ag_storage.list_events(_agent_conn(), run_id, after)
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            payload = {}
        out.append({"type": r["type"], "seq": r["seq"], "payload": payload})
    return {"run_id": run_id, "events": out, "lastSeq": out[-1]["seq"] if out else 0}


@app.get("/api/agent/runs/{run_id}/diff")
async def api_agent_diff(req: Request, run_id: str):
    user = auth_user(req)
    if not user:
        return unauthorized()
    conn = _agent_conn()
    run = ag_storage.get_run(conn, run_id)
    if not run:
        raise HTTPException(404, "找不到这次运行")
    if int(run.get("user_id") or 0) != int(user["userId"]):
        raise HTTPException(404, "找不到这次运行")
    diffs = []
    for e in ag_storage.list_events(conn, run_id):
        if e["type"] == ag_common.Ev.FS_DIFF:
            try:
                p = json.loads(e["payload"] or "{}")
            except Exception:
                p = {}
            diffs.append({"path": p.get("path", ""), "diff": p.get("diff", ""),
                          "created": p.get("created", False)})
    cks = ag_storage.list_checkpoints(conn, run_id)
    files = {}
    for ck in cks:
        try:
            files.update(json.loads(ck["files"] or "{}"))
        except Exception:
            pass
    return {"run_id": run_id, "status": run["status"], "diffs": diffs,
            "touched": sorted(files), "summary": run.get("summary") or "",
            "verification": json.loads(run.get("verification") or "{}")}


@app.post("/api/agent/runs/{run_id}/undo")
async def api_agent_undo(req: Request, run_id: str):
    """把工作区恢复到这次运行**之前**（新建的文件移入回收站，不真删）。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    conn = _agent_conn()
    run = ag_storage.get_run(conn, run_id)
    if not run:
        raise HTTPException(404, "找不到这次运行")
    if int(run.get("user_id") or 0) != int(user["userId"]):
        raise HTTPException(404, "找不到这次运行")
    row = ag_storage.get_workspace(conn, user["userId"], run["workspace_id"])
    if not row:
        raise HTTPException(404, "这个 run 的工作区已不再注册")
    ws = AgWorkspace.load(row["root_path"])
    snaps = ag_snapshot_store(conn)
    out = snaps.undo_run(ws, run_id)
    ag_storage.add_event(conn, run_id, "run.undo",
                         {"restored": out["restored"], "removed": out["removed"],
                          "errors": out["errors"]})
    return {"ok": True, **out}


@app.post("/api/agent/runs/{run_id}/review")
async def api_agent_review(req: Request, run_id: str):
    """记录逐文件审阅决定；拒绝时只还原该文件的运行前快照。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    _g = _deny_guest(user)
    if _g:
        return _g
    body = await req.json()
    path = str(body.get("path") or "").strip()
    decision = str(body.get("decision") or "").strip()
    if not path or decision not in {"accept", "reject"}:
        raise HTTPException(400, "需要 path 与 accept/reject 决定")
    conn = _agent_conn()
    run = ag_storage.get_run(conn, run_id)
    if not run or int(run.get("user_id") or 0) != int(user["userId"]):
        raise HTTPException(404, "找不到这次运行")
    if run.get("status") == "running":
        raise HTTPException(409, "运行尚未结束，不能审阅改动")
    checkpoints = ag_storage.list_checkpoints(conn, run_id)
    touched = set()
    for ck in checkpoints:
        try:
            touched.update(json.loads(ck["files"] or "{}").keys())
        except Exception:
            pass
    if path not in touched:
        raise HTTPException(400, "该文件不属于这次运行的可审阅改动")
    out = {"restored": [], "removed": [], "errors": [], "files": 0}
    if decision == "reject":
        row = ag_storage.get_workspace(conn, user["userId"], run["workspace_id"])
        if not row:
            raise HTTPException(404, "这个 run 的工作区已不再注册")
        out = ag_snapshot_store(conn).undo_files(AgWorkspace.load(row["root_path"]), run_id, [path])
    ag_storage.add_event(conn, run_id, "review.decision",
                         {"path": path, "decision": decision, **out})
    return {"ok": not out["errors"], "path": path, "decision": decision, **out}


def ag_snapshot_store(conn):
    from yorozuya.agent.snapshot import SnapshotStore
    return SnapshotStore(conn)


@app.get("/api/agent/runs/{run_id}/report")
async def api_agent_report(req: Request, run_id: str, download: int = 0):
    user = auth_user(req)
    if not user:
        return unauthorized()
    conn = _agent_conn()
    data = ag_report.build_report(conn, run_id)
    if not data:
        raise HTTPException(404, "找不到这次运行")
    if int(data["run"].get("user_id") or 0) != int(user["userId"]):
        raise HTTPException(404, "找不到这次运行")
    md = ag_report.to_markdown(data)
    if download:
        return JSONResponse({"markdown": md})
    return JSONResponse({"markdown": md, "run": data["run"], "verification": data["verification"]})


@app.post("/api/agent/handoff")
async def api_agent_handoff(req: Request):
    """★ 需求 3 / 5：一键把复杂任务转交小玉。

    服务端只做「切换人格 + 存档」，回答仍走现有 /api/chat（复用流式、守卫、记忆），
    前端拿到新状态后把原问题再发一次即可 —— 切人格、换主题、头像快照全部自动跟着走。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    user_id = user["userId"]
    body = await req.json()
    to_pid = str(body.get("to") or ag_router.TASK_PERSONA)
    if to_pid not in PERSONAS:
        to_pid = ag_router.TASK_PERSONA
    entry = load_state(user_id)
    s = entry["state"]
    with entry["lock"]:
        s["settings"]["personaId"] = to_pid
        apply_persona(s["settings"])
        remember_persona(to_pid)
        db.save_settings(user_id, s["settings"])
    return {"ok": True, "personaId": to_pid, "intro": ag_router.handoff_intro(to_pid),
            "state": state_view(s, user_id)}


@app.post("/api/files")
async def api_file_upload(req: Request, name: str = ""):
    """上传一个对话附件。

    为什么不用 multipart：本机没装 python-multipart，而 Python 3.13 下多一个依赖就多一份打包风险。
    直接收**原始字节**、文件名走 query（前端 `fetch(url?name=xxx, {body: file})`）就够了。
    """
    user = auth_user(req)
    if not user:
        return unauthorized()
    data = await req.body()
    try:
        meta = yfiles.save(user["userId"], name or "file", data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"保存失败：{type(e).__name__}"}, status_code=500)
    return {"ok": True, "file": yfiles.public(meta), "stats": yfiles.stats(user["userId"])}


@app.get("/api/files/{fid}")
def api_file_get(req: Request, fid: str):
    """取回附件本体（图片预览 / 文本下载）。只允许取自己的。"""
    user = auth_user(req)
    if not user:
        return unauthorized()
    p = yfiles.path_of(user["userId"], fid)
    meta = yfiles.load_meta(user["userId"], fid)
    if not p or not meta:
        raise HTTPException(404, "找不到这个文件")
    return FileResponse(str(p), filename=meta["name"])


@app.get("/api/files")
def api_file_stats(req: Request):
    user = auth_user(req)
    if not user:
        return unauthorized()
    return {"ok": True, **yfiles.stats(user["userId"]),
            "limits": {"maxBytes": yfiles.MAX_BYTES, "maxFiles": yfiles.MAX_FILES}}


app.mount("/", StaticFiles(directory=str(RENDERER_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8902, log_level="warning")
