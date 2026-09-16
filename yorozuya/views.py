# -*- coding: utf-8 -*-
"""输出视图：把内部状态整理成前端要的 JSON 形状。"""
from yorozuya.achievements import growth_view, sync_badges
from yorozuya.common import BUILD_STAMP, model_can_think
from yorozuya.personas import THEMES, apply_persona, persona_list, theme_of

def settings_view(s):
    apply_persona(s["settings"])   # 输出前对齐人格库（性格文案不可自定义）
    v = dict(s["settings"])
    v["hasKey"] = bool(v.get("apiKey"))
    v["apiKey"] = ""
    v["personaTheme"] = theme_of(s["settings"])
    # ★ 人格任务分流（agent 层）对外契约：老数据/访客态也要有明确默认值
    v.setdefault("routeMode", "ask")            # ask=复杂任务先推脱+一键切换 / auto=直接转交小玉
    v.setdefault("classifierMode", "rules")     # rules=纯本地规则（零 token）
    v["taskPersona"] = "tama"                   # 技术担当（复杂任务的归属人格）
    # ★ 当前模型会不会输出思考过程 —— 只读派生值（不落库），设置页据此如实提示。
    #   没有它的话，「显示思考过程」就是那种"开了没反应、也不告诉你为什么"的开关。
    v["thinkCapable"] = model_can_think(v.get("model"), (s.get("stats") or {}).get("thinkSeenModel"))
    return v


def state_view(s, user_id=None):
    """统一视图。传入 user_id 时会结算里程碑，新解锁的通过 newBadges 返回一次。

    历史对话：对外**只暴露当前会话的消息**（`chats`），另外给出 `conversations` 列表。
    内部 s["chats"] 仍持有该用户的全部消息（每条带 conv 归属），切会话只是换 settings.convId。
    """
    new_badges = sync_badges(s, user_id) if user_id else []
    cur = (s["settings"].get("convId") or "").strip()
    convs = list(s.get("conversations") or [])
    if not cur or not any(c["id"] == cur for c in convs):
        cur = convs[0]["id"] if convs else ""
    msgs = [m for m in s["chats"] if (m.get("conv") or "") == cur]
    # 每条会话带上「条数 + 最后一句」，列表里好认（只取用户/AI 的正文，不含开场白标记）
    by_conv = {}
    for m in s["chats"]:
        cid = m.get("conv") or ""
        d = by_conv.setdefault(cid, {"n": 0, "last": "", "ts": 0})
        d["n"] += 1
        if (m.get("ts") or 0) >= d["ts"]:
            d["ts"] = m.get("ts") or 0
            d["last"] = (m.get("text") or "").replace("\n", " ")[:80]
    conv_list = []
    for c in convs:
        d = by_conv.get(c["id"], {"n": 0, "last": "", "ts": 0})
        conv_list.append({**c, "count": d["n"], "preview": d["last"], "current": c["id"] == cur})
    conv_list.sort(key=lambda c: c.get("updatedAt") or 0, reverse=True)
    return {
        "build": BUILD_STAMP,          # 前端展示，用于确认版本
        "settings": settings_view(s),
        "personas": persona_list(),
        "personaThemes": THEMES,      # 每套主题（含 identity 标志物文案），供人格切换菜单展示
        "conversations": conv_list,    # 历史对话列表（按最近活动排序，含当前标记）
        "chats": msgs[-500:],         # 只给当前会话的消息
        "memories": sorted(s["memories"], key=lambda m: (not m.get("pinned"), -m["ts"])),
        "todos": sorted(s["todos"], key=lambda t: (t["done"], t["due"] or float("inf"), -t["createdAt"])),
        "growth": growth_view(s),
        "newBadges": new_badges,
    }
