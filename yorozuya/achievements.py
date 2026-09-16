# -*- coding: utf-8 -*-
"""成长体系：XP / 等级 / 亲密度阶段 / 里程碑徽章（含按人格动态生成的「角色」组）。"""
import re
from datetime import datetime, timedelta

from yorozuya.common import now_ms

# 数据库实例由 server.py 注入（避免 yorozuya 与 server 循环导入）。
# 访客模式下 server.py 传入的是 _GuestGuardDB 守卫，写操作会被静默跳过。
DB = None



MAX_LEVEL = 20          # 等级上限
STAGES = ["初识", "熟悉", "信任", "默契", "知己", "灵魂同频"]
STAGE_DESCS = {
    "初识": "刚认识，互相还在摸索彼此的频道。",
    "熟悉": "已经能接上话了，偶尔互相吐槽。",
    "信任": "开始说些不会随便告诉别人的话。",
    "默契": "一个开头就知道你接下来要说什么。",
    "知己": "这家伙……算是真正的伙伴了。",
    "灵魂同频": "无需多言，一个眼神就懂。",
}

# ---------------- 里程碑（成就）体系 · 50 枚 ----------------
# rarity: common 普通 / rare 稀有 / epic 史诗 / legend 传说
# group: 用于成长页分组展示

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")


# ---- 统计小工具（都基于内存中的 state，不额外落库） ----

def _user_texts(s):
    return [str(m.get("text") or "") for m in s["chats"] if m.get("role") == "user"]


def _hour_hits(s, hours):
    """指定小时段（本地时间）的发言次数。"""
    hist = s["stats"].get("hours", {}) or {}
    return sum(int(hist.get(str(h), 0)) for h in hours)


def _max_day_msgs(s):
    days = s["stats"].get("days", {}) or {}
    return max([int(v or 0) for v in days.values()] or [0])


def _kw_hits(s, kws):
    return sum(1 for t in _user_texts(s) if any(k in t for k in kws))


def _emoji_hits(s):
    return sum(1 for t in _user_texts(s) if EMOJI_RE.search(t))


def _mem_of_type(s, t):
    return sum(1 for m in s["memories"] if m.get("type") == t)


def _pinned_count(s):
    return sum(1 for m in s["memories"] if m.get("pinned"))


def _done_todos(s):
    return sum(1 for t in s["todos"] if t["done"])


def _due_todos(s):
    return sum(1 for t in s["todos"] if t.get("due"))


def _done_due_todos(s):
    return sum(1 for t in s["todos"] if t.get("due") and t["done"])


def _has_overdue(s):
    now = now_ms()
    return any((not t["done"]) and t.get("due") and t["due"] < now for t in s["todos"])


def _stage_idx(s):
    return STAGES.index(get_stage(get_level(s)))


def _active_days(s):
    """真正聊过天的天数（去重）。与 days_together（相伴天数）不同：中间断过就不算。"""
    return len(s["stats"].get("days", {}) or {})


def _mem_kinds(s):
    """已经收集到的记忆类型集合（事实/偏好/事件/计划）。"""
    return {m.get("type") for m in s["memories"] if m.get("type")}


def _mem_chars(s):
    """记忆正文合计字数。"""
    return sum(len(str(m.get("text") or "")) for m in s["memories"])


def _mem_of_source(s, src):
    """按来源统计（user=她自己手写的 / assistant=AI 写入的 / legacy=旧数据）。"""
    return sum(1 for m in s["memories"] if m.get("source") == src)


def _conv_count(s):
    return len(s.get("conversations") or [])


def _files_of(s):
    """所有对话附件（不含图片内容，只看元数据）。"""
    out = []
    for m in s["chats"]:
        out.extend(m.get("files") or [])
    return out


def _file_count(s, kind=None):
    items = _files_of(s)
    if kind:
        return sum(1 for f in items if (f or {}).get("kind") == kind)
    return len(items)


def _tool_hits(s):
    """AI 真的调用过工具的消息条数。"""
    return sum(1 for m in s["chats"] if m.get("tools"))


def _complex_hits(s):
    """被判为复杂任务的次数（只看用户消息上的 taskLevel，避免与 AI 消息重复计数）。"""
    return sum(1 for m in s["chats"]
               if m.get("role") == "user" and m.get("taskLevel") == "complex")


def _pending_todos(s):
    return sum(1 for t in s["todos"] if not t["done"])


BADGES = [
    # ============ 破冰 · 交流 ============
    {"id": "first", "group": "交流", "ico": "👋", "name": "初次见面",
     "desc": "第一次和银时说话", "hint": "发出第一条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 1},
    {"id": "talk10", "group": "交流", "ico": "💬", "name": "打开话匣",
     "desc": "十句话，勉强算是认识了吧", "hint": "累计 10 条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 10},
    {"id": "talk20", "group": "交流", "ico": "🥛", "name": "草莓牛奶",
     "desc": "你俩的对话已经攒够一盒牛奶的钱了", "hint": "累计 20 条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 20},
    {"id": "talk50", "group": "交流", "ico": "🍡", "name": "三色丸子",
     "desc": "五十句，够换一串团子坐下来慢慢吃了", "hint": "累计 50 条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 50},
    {"id": "talk100", "group": "交流", "ico": "🍨", "name": "巧克力芭菲",
     "desc": "一百句话，够换一份银时最爱的芭菲", "hint": "累计 100 条消息", "rarity": "rare",
     "fn": lambda s: s["stats"]["totalUser"] >= 100},
    {"id": "talk200", "group": "交流", "ico": "📖", "name": "万事屋常客",
     "desc": "两百句了，你已经比登势婆婆来的次数还多", "hint": "累计 200 条消息", "rarity": "rare",
     "fn": lambda s: s["stats"]["totalUser"] >= 200},
    {"id": "talk500", "group": "交流", "ico": "🍶", "name": "千杯不醉",
     "desc": "五百句，你俩比酒馆里的常客还能聊", "hint": "累计 500 条消息", "rarity": "epic",
     "fn": lambda s: s["stats"]["totalUser"] >= 500},
    {"id": "talk1000", "group": "交流", "ico": "🏮", "name": "万事屋的传说",
     "desc": "一千句话。江户的街头巷尾都在传你们的故事", "hint": "累计 1000 条消息", "rarity": "legend",
     "fn": lambda s: s["stats"]["totalUser"] >= 1000},


    {"id": "talk5", "group": "交流", "ico": "🍙", "name": "零星几句",
     "desc": "五句话，够换一颗饭团了", "hint": "累计 5 条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 5},
    {"id": "talk30", "group": "交流", "ico": "🍢", "name": "三十句",
     "desc": "三十句话，够串一串三色团子了", "hint": "累计 30 条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 30},
    {"id": "talk75", "group": "交流", "ico": "🍜", "name": "熟客上门",
     "desc": "七十五句，登势婆婆开始给你留位子了", "hint": "累计 75 条消息", "rarity": "common",
     "fn": lambda s: s["stats"]["totalUser"] >= 75},
    {"id": "talk150", "group": "交流", "ico": "🍰", "name": "一百五十句",
     "desc": "一百五十句，够换一份芭菲再加一盒草莓牛奶", "hint": "累计 150 条消息", "rarity": "rare",
     "fn": lambda s: s["stats"]["totalUser"] >= 150},
    {"id": "talk300", "group": "交流", "ico": "🎏", "name": "三百句",
     "desc": "三百句话，够铺满江户一条街的招牌了", "hint": "累计 300 条消息", "rarity": "rare",
     "fn": lambda s: s["stats"]["totalUser"] >= 300},
    {"id": "talk700", "group": "交流", "ico": "🎊", "name": "七百句",
     "desc": "七百句话。他说你比他的房租还缠人", "hint": "累计 700 条消息", "rarity": "epic",
     "fn": lambda s: s["stats"]["totalUser"] >= 700},
    {"id": "talk1500", "group": "交流", "ico": "👑", "name": "一千五百句",
     "desc": "一千五百句话。万事屋的门槛被你踩低了三寸", "hint": "累计 1500 条消息", "rarity": "legend",
     "fn": lambda s: s["stats"]["totalUser"] >= 1500},
    {"id": "ai100", "group": "交流", "ico": "🤖", "name": "一百次回音",
     "desc": "他已经回你一百次了，一次都没嫌烦", "hint": "AI 回复累计 100 条", "rarity": "rare",
     "fn": lambda s: s["stats"]["totalAI"] >= 100},
    {"id": "conv5", "group": "交流", "ico": "🚪", "name": "换过五间屋子",
     "desc": "五个会话，像搬了五次家，每次都把话带着走", "hint": "创建过 5 个会话", "rarity": "common",
     "fn": lambda s: _conv_count(s) >= 5},
    {"id": "conv20", "group": "交流", "ico": "🏘️", "name": "二十间屋子",
     "desc": "二十个会话，每间屋子里都留着一段话", "hint": "创建过 20 个会话", "rarity": "rare",
     "fn": lambda s: _conv_count(s) >= 20},

    # ============ 记忆 ============
    {"id": "mem1", "group": "记忆", "ico": "🌱", "name": "第一印象",
     "desc": "他记住了关于你的第一件事", "hint": "拥有 1 条记忆", "rarity": "common",
     "fn": lambda s: len(s["memories"]) >= 1},
    {"id": "mem5", "group": "记忆", "ico": "🧠", "name": "记住你了",
     "desc": "银时的脑子里终于有你这个人了", "hint": "拥有 5 条记忆", "rarity": "common",
     "fn": lambda s: len(s["memories"]) >= 5},
    {"id": "mem20", "group": "记忆", "ico": "📚", "name": "共同记忆",
     "desc": "二十件事，足够拼出一个人大概的样子", "hint": "拥有 20 条记忆", "rarity": "rare",
     "fn": lambda s: len(s["memories"]) >= 20},
    {"id": "mem50", "group": "记忆", "ico": "🏛️", "name": "记忆宫殿",
     "desc": "五十条记忆，这记性比他的房租账本强多了", "hint": "拥有 50 条记忆", "rarity": "legend",
     "fn": lambda s: len(s["memories"]) >= 50},
    {"id": "pin1", "group": "记忆", "ico": "📌", "name": "重要的事",
     "desc": "你把某件事钉在了他脑门上", "hint": "置顶 1 条记忆", "rarity": "common",
     "fn": lambda s: _pinned_count(s) >= 1},
    {"id": "pin3", "group": "记忆", "ico": "🗂️", "name": "重点标记",
     "desc": "三件事被钉住，再也赖不掉了", "hint": "置顶 3 条记忆", "rarity": "rare",
     "fn": lambda s: _pinned_count(s) >= 3},
    {"id": "plan5", "group": "记忆", "ico": "🗺️", "name": "计划通",
     "desc": "五条计划在他那儿挂着，跑不掉了", "hint": "记下 5 条「计划」类记忆", "rarity": "rare",
     "fn": lambda s: _mem_of_type(s, "计划") >= 5},
    {"id": "pref5", "group": "记忆", "ico": "🍽️", "name": "口味档案",
     "desc": "爱吃什么、讨厌什么，他记得比你还清楚", "hint": "记下 5 条「偏好」类记忆", "rarity": "rare",
     "fn": lambda s: _mem_of_type(s, "偏好") >= 5},
    {"id": "event5", "group": "记忆", "ico": "🎬", "name": "生活记录员",
     "desc": "五件发生在你身上的事，都被他收进了抽屉", "hint": "记下 5 条「事件」类记忆", "rarity": "rare",
     "fn": lambda s: _mem_of_type(s, "事件") >= 5},


    {"id": "mem10", "group": "记忆", "ico": "🧩", "name": "十件事",
     "desc": "十条记忆，够拼出一个人大概的轮廓了", "hint": "拥有 10 条记忆", "rarity": "rare",
     "fn": lambda s: len(s["memories"]) >= 10},
    {"id": "mem30", "group": "记忆", "ico": "🗄️", "name": "三十条档案",
     "desc": "三十条。这记性比他那个房租账本强多了", "hint": "拥有 30 条记忆", "rarity": "rare",
     "fn": lambda s: len(s["memories"]) >= 30},
    {"id": "mem100", "group": "记忆", "ico": "📦", "name": "专属档案室",
     "desc": "一百条记忆，够给你开一间档案室了", "hint": "拥有 100 条记忆", "rarity": "epic",
     "fn": lambda s: len(s["memories"]) >= 100},
    {"id": "pin10", "group": "记忆", "ico": "📍", "name": "钉满整面墙",
     "desc": "十件事被钉在墙上，再也赖不掉了", "hint": "置顶 10 条记忆", "rarity": "epic",
     "fn": lambda s: _pinned_count(s) >= 10},
    {"id": "memFact10", "group": "记忆", "ico": "🔍", "name": "事实核查",
     "desc": "十条关于你自己的事实，他一条都没记错", "hint": "记下 10 条「事实」类记忆", "rarity": "rare",
     "fn": lambda s: _mem_of_type(s, "事实") >= 10},
    {"id": "memKind4", "group": "记忆", "ico": "🗃️", "name": "四种都齐了",
     "desc": "事实、偏好、事件、计划——一个都没落下", "hint": "四种记忆类型各至少 1 条", "rarity": "rare",
     "fn": lambda s: len(_mem_kinds(s)) >= 4},
    {"id": "memMine5", "group": "记忆", "ico": "✍️", "name": "亲手写下的",
     "desc": "有五条是你自己写进去的，他不许改一个字", "hint": "手动添加 5 条记忆", "rarity": "rare",
     "fn": lambda s: _mem_of_source(s, "user") >= 5},
    {"id": "memChar2000", "group": "记忆", "ico": "📚", "name": "记忆典籍",
     "desc": "记忆正文合起来两千字，够写一篇小传了", "hint": "记忆正文合计 2000 字", "rarity": "epic",
     "fn": lambda s: _mem_chars(s) >= 2000},

    # ============ 陪伴 ============
    {"id": "days2", "group": "陪伴", "ico": "🌤️", "name": "明天见",
     "desc": "第二天你还来了——这比什么承诺都实在", "hint": "相伴 2 天", "rarity": "common",
     "fn": lambda s: days_together(s) >= 2},
    {"id": "days7", "group": "陪伴", "ico": "📆", "name": "一周之约",
     "desc": "七天，从陌生人变成习惯", "hint": "相伴 7 天", "rarity": "common",
     "fn": lambda s: days_together(s) >= 7},
    {"id": "days30", "group": "陪伴", "ico": "🌙", "name": "相伴一月",
     "desc": "从陌生到习惯，只用了一个月", "hint": "相伴 30 天", "rarity": "rare",
     "fn": lambda s: days_together(s) >= 30},
    {"id": "days100", "group": "陪伴", "ico": "💯", "name": "百日之约",
     "desc": "一百天。银时说这不叫坚持，这叫日常", "hint": "相伴 100 天", "rarity": "legend",
     "fn": lambda s: days_together(s) >= 100},
    {"id": "days365", "group": "陪伴", "ico": "🎂", "name": "一年之约",
     "desc": "整整一年。万事屋的房租都没能撑这么久", "hint": "相伴 365 天", "rarity": "legend",
     "fn": lambda s: days_together(s) >= 365},
    {"id": "streak3", "group": "陪伴", "ico": "🕯️", "name": "三日不断",
     "desc": "连着三天，火苗还没灭", "hint": "连续陪伴 3 天", "rarity": "common",
     "fn": lambda s: streak_days(s) >= 3},
    {"id": "streak7", "group": "陪伴", "ico": "🔥", "name": "七日之约",
     "desc": "连续七天，一天都没落下", "hint": "连续陪伴 7 天", "rarity": "rare",
     "fn": lambda s: streak_days(s) >= 7},
    {"id": "streak30", "group": "陪伴", "ico": "🗓️", "name": "月度常客",
     "desc": "整整一个月，登势婆婆都要给你发会员卡了", "hint": "连续陪伴 30 天", "rarity": "epic",
     "fn": lambda s: streak_days(s) >= 30},


    {"id": "days5", "group": "陪伴", "ico": "🍵", "name": "第五天",
     "desc": "第五天。习惯都是这么一天天养出来的", "hint": "相伴 5 天", "rarity": "common",
     "fn": lambda s: days_together(s) >= 5},
    {"id": "days14", "group": "陪伴", "ico": "🌗", "name": "半月",
     "desc": "半个月，昼夜都陪你换过一轮了", "hint": "相伴 14 天", "rarity": "rare",
     "fn": lambda s: days_together(s) >= 14},
    {"id": "days60", "group": "陪伴", "ico": "🌸", "name": "两个月",
     "desc": "两个月，季节都跟着换了一轮", "hint": "相伴 60 天", "rarity": "epic",
     "fn": lambda s: days_together(s) >= 60},
    {"id": "days200", "group": "陪伴", "ico": "🍂", "name": "两百天",
     "desc": "两百天。他说这已经不叫坚持，叫过日子", "hint": "相伴 200 天", "rarity": "legend",
     "fn": lambda s: days_together(s) >= 200},
    {"id": "streak14", "group": "陪伴", "ico": "🕰️", "name": "半月未断",
     "desc": "连着一整个半月，一天都没落下", "hint": "连续陪伴 14 天", "rarity": "rare",
     "fn": lambda s: streak_days(s) >= 14},
    {"id": "streak60", "group": "陪伴", "ico": "🌊", "name": "两月未断",
     "desc": "连续两个月，火苗一次都没熄", "hint": "连续陪伴 60 天", "rarity": "epic",
     "fn": lambda s: streak_days(s) >= 60},
    {"id": "streak100", "group": "陪伴", "ico": "💎", "name": "百日未断",
     "desc": "一百天连着来。这已经不是习惯，是本能", "hint": "连续陪伴 100 天", "rarity": "legend",
     "fn": lambda s: streak_days(s) >= 100},
    {"id": "active40", "group": "陪伴", "ico": "🗓️", "name": "活跃四十天",
     "desc": "四十个有话说过的日子，一天一句也算", "hint": "累计 40 天聊过天（不必连续）", "rarity": "rare",
     "fn": lambda s: _active_days(s) >= 40},

    # ============ 万事屋业务 ============
    {"id": "todo1", "group": "业务", "ico": "📝", "name": "委托受理",
     "desc": "万事屋接了第一单，虽然报酬还是零", "hint": "记下第一个待办", "rarity": "common",
     "fn": lambda s: int(s["stats"].get("todosCreated") or 0) >= 1},
    {"id": "todo10", "group": "业务", "ico": "✅", "name": "靠谱万事屋",
     "desc": "十件事办完，信誉值 +10", "hint": "完成 10 个待办", "rarity": "rare",
     "fn": lambda s: _done_todos(s) >= 10},
    {"id": "todo50", "group": "业务", "ico": "🏆", "name": "万事屋主",
     "desc": "五十件事，你才是这家店真正的老板", "hint": "完成 50 个待办", "rarity": "epic",
     "fn": lambda s: _done_todos(s) >= 50},
    {"id": "due5", "group": "业务", "ico": "⏰", "name": "时间管理大师",
     "desc": "连提醒时间都安排得明明白白", "hint": "给 5 个待办设过提醒时间", "rarity": "rare",
     "fn": lambda s: _due_todos(s) >= 5},
    {"id": "dueDone5", "group": "业务", "ico": "🎯", "name": "说到做到",
     "desc": "五个定了时间的委托，全部按时勾掉", "hint": "完成 5 个带提醒时间的待办", "rarity": "epic",
     "fn": lambda s: _done_due_todos(s) >= 5},
    {"id": "todoClear", "group": "业务", "ico": "🧹", "name": "清空柜台",
     "desc": "一件不剩。银时难得夸你一句", "hint": "完成 5 个待办且当前没有未完成的", "rarity": "rare",
     "fn": lambda s: _done_todos(s) >= 5 and not any(not t["done"] for t in s["todos"])},
    {"id": "overdue", "group": "业务", "ico": "😅", "name": "拖延症晚期",
     "desc": "到点了还没动。这种事谁也别说谁", "hint": "有 1 个已过期未完成的待办", "rarity": "common",
     "fn": lambda s: _has_overdue(s)},


    {"id": "todo3", "group": "业务", "ico": "📋", "name": "三单在办",
     "desc": "万事屋同时接了三单，柜台终于热闹起来", "hint": "记下 3 个待办", "rarity": "common",
     "fn": lambda s: int(s["stats"].get("todosCreated") or 0) >= 3},
    {"id": "todo5", "group": "业务", "ico": "🗒️", "name": "五张委托单",
     "desc": "五张委托单压在柜台上，他说别急，一件件来", "hint": "记下 5 个待办", "rarity": "common",
     "fn": lambda s: int(s["stats"].get("todosCreated") or 0) >= 5},
    {"id": "todo25", "group": "业务", "ico": "🏅", "name": "二十五件",
     "desc": "二十五件委托办完，江户的口碑就是这么攒的", "hint": "完成 25 个待办", "rarity": "rare",
     "fn": lambda s: _done_todos(s) >= 25},
    {"id": "todo100", "group": "业务", "ico": "🎖️", "name": "百件委托",
     "desc": "一百件事。你才是万事屋里最忙的那个", "hint": "完成 100 个待办", "rarity": "legend",
     "fn": lambda s: _done_todos(s) >= 100},
    {"id": "due1", "group": "业务", "ico": "⏳", "name": "定个时间",
     "desc": "终于有件事被写上了时间", "hint": "给 1 个待办设过提醒时间", "rarity": "common",
     "fn": lambda s: _due_todos(s) >= 1},
    {"id": "due25", "group": "业务", "ico": "📅", "name": "排期二十五次",
     "desc": "二十五次安排得明明白白，连银时都挑不出错", "hint": "给 25 个待办设过提醒时间", "rarity": "epic",
     "fn": lambda s: _due_todos(s) >= 25},
    {"id": "todoBusy", "group": "业务", "ico": "🔥", "name": "手上三件",
     "desc": "三件事同时在办，而且一件都没拖——难得", "hint": "有 3 个未完成待办且当前没有过期", "rarity": "rare",
     "fn": lambda s: _pending_todos(s) >= 3 and not _has_overdue(s)},
    {"id": "todoAllDue", "group": "业务", "ico": "🧭", "name": "从不失约",
     "desc": "凡是定了时间的委托，一件都没有食言", "hint": "设过 3 个以上提醒且全部完成", "rarity": "epic",
     "fn": lambda s: _due_todos(s) >= 3 and _done_due_todos(s) == _due_todos(s)},

    # ============ 亲密度 ============
    {"id": "lv3", "group": "亲密度", "ico": "✨", "name": "有点熟了",
     "desc": "他开始记得你的说话习惯", "hint": "等级达到 3 级", "rarity": "common",
     "fn": lambda s: get_level(s) >= 3},
    {"id": "lv5", "group": "亲密度", "ico": "⚔️", "name": "武士之魂",
     "desc": "他开始把你当伙伴，而不只是委托人", "hint": "等级达到 5 级", "rarity": "rare",
     "fn": lambda s: get_level(s) >= 5},
    {"id": "lv10", "group": "亲密度", "ico": "🗡️", "name": "洞爷湖",
     "desc": "十级。他把那把木刀借你摸了一下", "hint": "等级达到 10 级", "rarity": "epic",
     "fn": lambda s: get_level(s) >= 10},
    {"id": "lv15", "group": "亲密度", "ico": "🛡️", "name": "万事屋的守护",
     "desc": "十五级。有事他一定会站在你这边", "hint": "等级达到 15 级", "rarity": "epic",
     "fn": lambda s: get_level(s) >= 15},
    {"id": "lv20", "group": "亲密度", "ico": "🌟", "name": "灵魂同频",
     "desc": "二十级封顶。无需多言，一个眼神就懂", "hint": "达到最高等级 Lv.20", "rarity": "legend",
     "fn": lambda s: get_level(s) >= 20},
    {"id": "stageMo", "group": "亲密度", "ico": "🤝", "name": "心照不宣",
     "desc": "一个开头就知道你接下来要说什么", "hint": "亲密度阶段达到「默契」", "rarity": "rare",
     "fn": lambda s: _stage_idx(s) >= 3},


    {"id": "lv4", "group": "亲密度", "ico": "🌤️", "name": "说得上话了",
     "desc": "四级。他终于不用「喂」来称呼你了", "hint": "等级达到 4 级", "rarity": "common",
     "fn": lambda s: get_level(s) >= 4},
    {"id": "lv6", "group": "亲密度", "ico": "🍀", "name": "自然而然",
     "desc": "六级。找他已经不需要理由了", "hint": "等级达到 6 级", "rarity": "rare",
     "fn": lambda s: get_level(s) >= 6},
    {"id": "lv8", "group": "亲密度", "ico": "🧭", "name": "老交情",
     "desc": "八级。他嘴上嫌弃，有事还是第一个到", "hint": "等级达到 8 级", "rarity": "rare",
     "fn": lambda s: get_level(s) >= 8},
    {"id": "lv12", "group": "亲密度", "ico": "🏔️", "name": "并肩的人",
     "desc": "十二级。他把你算进「要保护的东西」里了", "hint": "等级达到 12 级", "rarity": "epic",
     "fn": lambda s: get_level(s) >= 12},
    {"id": "stageFriend", "group": "亲密度", "ico": "🤗", "name": "一声朋友",
     "desc": "阶段到了「信任」——这个词他轻易不说", "hint": "亲密度阶段达到「信任」", "rarity": "rare",
     "fn": lambda s: _stage_idx(s) >= 2},
    {"id": "stageSoul", "group": "亲密度", "ico": "🌌", "name": "无需多言",
     "desc": "灵魂同频。有些话到这一步反而不用说了", "hint": "亲密度阶段达到「灵魂同频」", "rarity": "legend",
     "fn": lambda s: _stage_idx(s) >= 5},

    # ============ 冷门彩蛋 ============
    {"id": "night1", "group": "彩蛋", "ico": "🌃", "name": "深夜的第一句",
     "desc": "凌晨还在找银时——你今晚有事吧", "hint": "凌晨 0-4 点聊过 1 次", "rarity": "common",
     "fn": lambda s: _hour_hits(s, (0, 1, 2, 3)) >= 1},
    {"id": "night5", "group": "彩蛋", "ico": "👺", "name": "夜半委托",
     "desc": "凌晨五点前聊过五回的人，多半有心事", "hint": "凌晨 0-4 点聊满 5 次", "rarity": "rare",
     "fn": lambda s: _hour_hits(s, (0, 1, 2, 3)) >= 5},
    {"id": "early5", "group": "彩蛋", "ico": "🐔", "name": "早起委托人",
     "desc": "五点到七点，比定春起得还早", "hint": "早上 5-7 点聊满 5 次", "rarity": "common",
     "fn": lambda s: _hour_hits(s, (5, 6)) >= 5},
    {"id": "noon10", "group": "彩蛋", "ico": "🍱", "name": "午休偷懒",
     "desc": "中午不睡觉跑来找银时吐槽，这是同谋", "hint": "中午 12-13 点聊满 10 次", "rarity": "rare",
     "fn": lambda s: _hour_hits(s, (12, 13)) >= 10},
    {"id": "long200", "group": "彩蛋", "ico": "✍️", "name": "长篇大论",
     "desc": "一口气说了两百多字，银时居然看完了", "hint": "单条消息超过 200 字", "rarity": "common",
     "fn": lambda s: int(s["stats"].get("longest") or 0) >= 200},
    {"id": "long500", "group": "彩蛋", "ico": "📜", "name": "论文级委托",
     "desc": "五百字的单条消息。他边看边喝了两盒草莓牛奶", "hint": "单条消息超过 500 字", "rarity": "epic",
     "fn": lambda s: int(s["stats"].get("longest") or 0) >= 500},
    {"id": "day3", "group": "彩蛋", "ico": "☕", "name": "一日三聊",
     "desc": "一天来三趟，你比送报纸的还勤", "hint": "一天之内聊满 3 轮", "rarity": "common",
     "fn": lambda s: _max_day_msgs(s) >= 3},
    {"id": "day10", "group": "彩蛋", "ico": "🌪️", "name": "话匣子暴走",
     "desc": "一天十条，银时都被你说得插不上话", "hint": "一天之内聊满 10 轮", "rarity": "rare",
     "fn": lambda s: _max_day_msgs(s) >= 10},
    {"id": "emoji10", "group": "彩蛋", "ico": "😄", "name": "表情包战士",
     "desc": "光靠表情就能聊天的人", "hint": "10 条消息里带表情符号", "rarity": "common",
     "fn": lambda s: _emoji_hits(s) >= 10},
    {"id": "ask10", "group": "彩蛋", "ico": "❓", "name": "十万个为什么",
     "desc": "十句话里全是问题，银时头都大了", "hint": "10 条消息里带问号", "rarity": "rare",
     "fn": lambda s: _kw_hits(s, ("?", "？")) >= 10},
    {"id": "thanks3", "group": "彩蛋", "ico": "🙏", "name": "有礼貌的人",
     "desc": "跟一个吃白饭的武士说谢谢，你还真客气", "hint": "说过 3 次谢谢", "rarity": "common",
     "fn": lambda s: _kw_hits(s, ("谢谢", "感谢", "多谢")) >= 3},
    {"id": "callName10", "group": "彩蛋", "ico": "🍓", "name": "直呼其名",
     "desc": "十次叫出「银时」，他已经懒得吐槽了", "hint": "10 条消息里提到「银时」", "rarity": "common",
     "fn": lambda s: _kw_hits(s, ("银时", "銀時", "坂田")) >= 10},

    {"id": "mayo5", "group": "彩蛋", "ico": "🥚", "name": "蛋黄酱爱好者",
     "desc": "五次提到蛋黄酱——土方知道了会很欣慰", "hint": "提到「蛋黄酱」或「土方」5 次", "rarity": "rare",
     "fn": lambda s: _kw_hits(s, ("蛋黄酱", "土方")) >= 5},
    {"id": "sadaharu3", "group": "彩蛋", "ico": "🐕", "name": "定春的朋友",
     "desc": "提到定春三次。它记住了你，它记性很好的", "hint": "提到「定春」3 次", "rarity": "common",
     "fn": lambda s: _kw_hits(s, ("定春",)) >= 3},
    {"id": "goodnight10", "group": "彩蛋", "ico": "🌙", "name": "睡前十次晚安",
     "desc": "十次道晚安。说完那句才舍得关窗口", "hint": "说过 10 次「晚安」", "rarity": "rare",
     "fn": lambda s: _kw_hits(s, ("晚安",)) >= 10},
    {"id": "tired10", "group": "彩蛋", "ico": "🫖", "name": "累了就说",
     "desc": "十次说累，他每次都没有说「那就别做了」", "hint": "说过 10 次「累」", "rarity": "rare",
     "fn": lambda s: _kw_hits(s, ("累了", "好累", "心累", "累死", "太累", "有点累", "疲惫", "乏了")) >= 10},
    {"id": "file1", "group": "彩蛋", "ico": "📎", "name": "第一份资料",
     "desc": "第一次把文件丢进对话里，他居然真的读了", "hint": "上传 1 个附件", "rarity": "common",
     "fn": lambda s: _file_count(s) >= 1},
    {"id": "file10", "group": "彩蛋", "ico": "🗂️", "name": "十份资料",
     "desc": "十份资料堆在桌上，万事屋快变成事务所了", "hint": "上传过 10 个附件", "rarity": "rare",
     "fn": lambda s: _file_count(s) >= 10},
    {"id": "img5", "group": "彩蛋", "ico": "🖼️", "name": "截图党",
     "desc": "五张图丢给他。虽然他现在还看不清，但收下了", "hint": "上传过 5 张图片", "rarity": "rare",
     "fn": lambda s: _file_count(s, "image") >= 5},
    {"id": "complex1", "group": "彩蛋", "ico": "🧰", "name": "第一件技术活",
     "desc": "第一单复杂委托，直接转给了小玉", "hint": "触发 1 次复杂任务分流", "rarity": "common",
     "fn": lambda s: _complex_hits(s) >= 1},
    {"id": "tool1", "group": "彩蛋", "ico": "🔧", "name": "他真的动手了",
     "desc": "第一次看见他调工具干活——不是嘴上说说", "hint": "AI 调用过 1 次工具", "rarity": "common",
     "fn": lambda s: _tool_hits(s) >= 1},
    {"id": "long1000", "group": "彩蛋", "ico": "📜", "name": "千字长文",
     "desc": "单条一千字。他一口气读完，还回了一句「嗯」", "hint": "单条消息超过 1000 字", "rarity": "legend",
     "fn": lambda s: int(s["stats"].get("longest") or 0) >= 1000},
]

# ================= 「角色」分组：与每个人格各自相关的里程碑 =================
# ★ 设计原则（她两轮打磨后的要求）：**每位角色的成就都要不一样，而且要有意思**。
#   不是"四个角色各发一套同样的次数阶梯"，而是各拿各的梗：
#     话题梗 / 名场面与弱点 / 角色自己的动作 / 应用内的真实行为 / 单日强度 / 人物关系
#   每人固定 6 条：初见（有画面的一句）+ 专属时段（她要求保留）+ 4 条这个人独有的。
#
# 数据来源（都不新增埋点）：
#   · 归属：**用户消息不带 pid**，所以按「同一会话里紧随其后的那条 AI 回复」归到角色头上
#   · AI 消息自带 pid / ts / tools，可直接按角色统计
#
# 判据规则（rule）：
#   ("kw", 关键词元组, n)      —— 和这个角色聊到这些话题 n 次（归属后的用户消息）
#   ("ai_text", 字符串元组, n) —— 这个角色自己的回复里出现这些标记 n 次（他的动作/口癖）
#   ("tools", n)               —— 这个角色调用工具 n 次
#   ("complex", n)             —— 把 n 件复杂任务交给他/她
#   ("daymax", n)              —— 同一天里跟他/她聊满 n 轮
#   ("turns", n)               —— 这个角色回复你 n 次
#   ("dayhours", n)            —— 这个角色在 n 个不同小时里回过你
PERSONA_ACH = {
    "gintoki": {
        "hours": (0, 1, 2, 3, 4, 5), "hour_ico": "🌃", "hour_hint": "凌晨 0-5 点",
        "hour": ("陪他熬夜", "凌晨 0-5 点他还回过你几次——两个夜猫子"),
        "first": ("他刚睡醒", "第一次跟银时说话。他好像还没睡醒"),
        "own": [
            ("糖分补给", "跟他聊到甜食、草莓牛奶、芭菲这些——他的燃料", "🍓", "common",
             ("kw", ("草莓牛奶", "芭菲", "甜", "糖", "蛋糕", "布丁", "巧克力", "零食"), 10)),
            ("别拿鬼吓他", "鬼、恐怖、灵异——白夜叉唯一的软肋之一", "👻", "rare",
             ("kw", ("鬼", "恐怖", "害怕", "灵异", "吓"), 2)),
            ("深夜长谈", "同一天里跟他聊到停不下来", "🌙", "epic", ("daymax", 15)),
            ("房租警告", "钱、房租、穷——他懂，他比谁都懂", "💸", "common",
             ("kw", ("房租", "欠钱", "穷", "工资", "省钱"), 10)),
        ],
    },
    "kagura": {
        "hours": (12, 13, 14, 15, 16), "hour_ico": "🌂", "hour_hint": "中午 12 点到下午 4 点",
        "hour": ("午后的神乐", "中午到下午她最有精神，拌嘴都带着劲"),
        "first": ("第一次听见阿鲁", "她说话带着点异邦腔调，句尾总多一个音"),
        "own": [
            ("投喂成功", "跟她聊吃的、饿、零食——她一件都不会放过", "🍚", "common",
             ("kw", ("吃", "饭", "昆布", "肉", "饿", "好吃", "零食", "点心"), 15)),
            ("夜兔体质", "力气、打架、搬重物——她在这上头很自信", "💪", "rare",
             ("kw", ("力气", "打架", "打人", "搬", "重", "拆"), 5)),
            ("定春的伙伴", "提到她那条大白狗", "🐕", "rare",
             ("kw", ("定春", "狗", "狛神"), 5)),
            ("爸爸和哥哥", "神威、星海坊主——她嘴上不提，其实都在意", "🌌", "epic",
             ("kw", ("神威", "星海坊主", "爸爸", "哥哥"), 3)),
        ],
    },
    "shinpachi": {
        "hours": (6, 7, 8, 9, 10, 11), "hour_ico": "☀️", "hour_hint": "早上 6-11 点",
        "hour": ("清晨的吐槽", "早上 6-11 点他就在了，比定春起得还早"),
        "first": ("先看到的是一副眼镜", "第一次搭话，你先注意到的不是脸"),
        "own": [
            ("眼镜的共鸣", "跟他聊到眼镜——他 95% 就是那副眼镜", "👓", "common",
             ("kw", ("眼镜", "推眼镜", "镜片"), 5)),
            ("吐槽担当", "他一边说话一边推眼镜（他自己的招牌动作）", "🗯️", "epic",
             ("ai_text", ("（推眼镜）", "推了推眼镜", "镜片反光"), 15)),
            ("95% 是眼镜", "笑着问他「你是谁」「看不见你」之类的话", "❓", "rare",
             ("kw", ("存在感", "看不到你", "看不见你", "你是谁"), 2)),
            ("姐姐的话题", "志村妙、道场——他的软肋与骄傲", "🏠", "rare",
             ("kw", ("姐姐", "妙", "道场"), 5)),
        ],
    },
    "tama": {
        "hours": (17, 18, 19, 20, 21, 22, 23), "hour_ico": "🌆", "hour_hint": "傍晚 5 点到夜里 11 点",
        "hour": ("傍晚的帮手", "傍晚到夜里她都在，礼貌得让人不好意思"),
        "first": ("她记进了数据里", "第一次搭话，她认真地把这件事存了下来"),
        "own": [
            ("技术担当", "把复杂任务交给她——这是她在这个家的分内事", "🧰", "common",
             ("complex", 3)),
            ("动手的女仆", "她不是嘴上答应，而是真的去调工具干活", "🔧", "rare",
             ("tools", 10)),
            ("RESET 键", "聊到关机、重启、断电——那是她的名台词", "🔄", "epic",
             ("kw", ("RESET", "reset", "关机", "重启", "断电"), 2)),
            ("数据与记忆", "跟她聊数据、内存、备份——她会认真记进内存", "💾", "rare",
             ("kw", ("数据", "内存", "备份", "记录"), 5)),
        ],
    },
}
# 新增人格没有专属配置时用这套兜底（保证「只加数据就能跑」）
PERSONA_ACH_FALLBACK = {
    "hours": tuple(range(24)), "hour_ico": "⏰", "hour_hint": "任意时段",
    "hour": ("{name}的时段", "{name} 在专属时段回过你几次"),
    "first": ("初识{name}", "第一次和{name}说上话"),
    "own": [
        ("和{name}聊得多了", "已经和 {name} 聊过不少次", "💬", "common", ("turns", 10)),
        ("和{name}的老交情", "和 {name} 的来往已经很自然了", "🍶", "rare", ("turns", 50)),
        ("和{name}常来常往", "跟 {name} 已经熟得像自己人", "🏆", "epic", ("turns", 200)),
        ("一整天里都有 {name}", "六个不同的小时里都有 {name} 的回复", "🕰️", "rare",
         ("dayhours", 6)),
    ],
}
HOUR_HITS = 3                     # 专属时段：够 3 次就算（太严会变成解不开的摆设）


def _persona_turns(s, pid):
    """这位角色回过你多少次（含首次登场的开场白）。"""
    return sum(1 for m in s["chats"]
               if m.get("role") == "ai" and m.get("pid") == pid)


def _persona_hours(s, pid):
    """这位角色在哪些小时里回过你。"""
    return {_hour_of(m) for m in s["chats"]
            if m.get("role") == "ai" and m.get("pid") == pid} - {-1}


def _persona_user_texts(s, pid):
    """把**用户消息**归到「接下来回他/她的那个角色」头上。

    ⚠️ 用户消息本身不带 pid（只有 AI 消息带），所以这里按会话顺序找
       「同一会话里紧随其后的第一条 AI 回复」，那条回复属于谁，这句话就算跟谁聊的。
       没有回复的最后一句就不计入（没有归属，不猜）。
    """
    by_conv: dict = {}
    for m in s["chats"]:
        by_conv.setdefault(m.get("conv") or "", []).append(m)
    out = []
    for msgs in by_conv.values():
        for i, m in enumerate(msgs):
            if m.get("role") != "user":
                continue
            for nxt in msgs[i + 1:]:
                if nxt.get("role") == "ai":
                    if nxt.get("pid") == pid:
                        out.append(str(m.get("text") or ""))
                    break                      # 只看紧随其后的第一条回复
    return out


def _persona_kw_hits(s, pid, kws):
    return sum(1 for t in _persona_user_texts(s, pid) if any(k in t for k in kws))


def _persona_text_hits(s, pid, needles):
    """这个角色自己的回复里，出现这些标记（动作/口癖）多少次。"""
    n = 0
    for m in s["chats"]:
        if m.get("role") != "ai" or m.get("pid") != pid:
            continue
        t = str(m.get("text") or "")
        if any(x in t for x in needles):
            n += 1
    return n


def _persona_tool_hits(s, pid):
    return sum(1 for m in s["chats"]
               if m.get("role") == "ai" and m.get("pid") == pid and m.get("tools"))


def _persona_daily_max(s, pid):
    """这位角色「同一天最多回过你多少轮」——用来做「聊到停不下来」这类成就。"""
    days: dict = {}
    for m in s["chats"]:
        if m.get("role") != "ai" or m.get("pid") != pid:
            continue
        try:
            day = datetime.fromtimestamp(int(m.get("ts") or 0) / 1000).strftime("%Y-%m-%d")
        except Exception:
            continue
        days[day] = days.get(day, 0) + 1
    return max(days.values()) if days else 0


def _own_fn(pid, rule):
    """把规则描述变成判据函数。

    ★ 第一个参数必须是 s —— 框架是按 fn(state) 调的。
      写成 `lambda kws=kws: ...` 会让 state 被当成关键词传进去，
      而且异常会被调用方的 try/except 吞掉，只表现成"这条成就永远解不开"。
    """
    kind = rule[0]
    if kind == "kw":
        kws, n = rule[1], rule[2]
        return lambda s, pid=pid, kws=kws, n=n: _persona_kw_hits(s, pid, kws) >= n
    if kind == "ai_text":
        nails, n = rule[1], rule[2]
        return lambda s, pid=pid, nails=nails, n=n: _persona_text_hits(s, pid, nails) >= n
    if kind == "tools":
        n = rule[1]
        return lambda s, pid=pid, n=n: _persona_tool_hits(s, pid) >= n
    if kind == "complex":
        n = rule[1]
        return lambda s, n=n: _complex_hits(s) >= n
    if kind == "daymax":
        n = rule[1]
        return lambda s, pid=pid, n=n: _persona_daily_max(s, pid) >= n
    if kind == "dayhours":
        n = rule[1]
        return lambda s, pid=pid, n=n: len(_persona_hours(s, pid)) >= n
    n = rule[1]                                # turns
    return lambda s, pid=pid, n=n: _persona_turns(s, pid) >= n


def _own_hint(name, rule):
    """把规则写成一句人看得懂的达成条件。"""
    kind = rule[0]
    if kind == "kw":
        kws, n = rule[1], rule[2]
        return "和%s聊到「%s」这类话题 %d 次" % (name, "／".join(kws[:3]), n)
    if kind == "ai_text":
        nails, n = rule[1], rule[2]
        return "%s说过「%s」这类话 %d 次" % (name, nails[0].strip("（）"), n)
    if kind == "tools":
        return "%s动手用过工具 %d 次" % (name, rule[1])
    if kind == "complex":
        return "把 %d 件复杂任务交给%s" % (rule[1], name)
    if kind == "daymax":
        return "同一天里和%s聊满 %d 轮" % (name, rule[1])
    if kind == "dayhours":
        return "在 %d 个不同小时里和%s聊过" % (rule[1], name)
    return "%s回复你 %d 次" % (name, rule[1])


def _build_persona_badges():
    """按人格库生成「角色」分组。新增人格不用改这里。"""
    from yorozuya.personas import PERSONAS
    out = []
    pids = [pid for pid in (PERSONAS or {})]
    for pid in pids:
        p = PERSONAS.get(pid) or {}
        name = p.get("name") or pid
        f = PERSONA_ACH.get(pid) or PERSONA_ACH_FALLBACK
        hours = tuple(f.get("hours") or PERSONA_ACH_FALLBACK["hours"])
        cap = pid[:1].upper() + pid[1:]
        # ① 初见（入口，每人都有；名字写成有画面的一句）
        first = f.get("first") or PERSONA_ACH_FALLBACK["first"]
        out.append({
            "id": "meet" + cap, "group": "角色", "ico": "👋",
            "name": first[0].format(name=name), "desc": first[1].format(name=name),
            "hint": "让%s回复你 1 次" % name, "rarity": "common",
            "fn": (lambda s, pid=pid: _persona_turns(s, pid) >= 1),
        })
        # ② 专属时段（她要求保留）
        out.append({
            "id": "night" + cap, "group": "角色", "ico": f.get("hour_ico") or "⏰",
            "name": f["hour"][0].format(name=name),
            "desc": f["hour"][1].format(name=name),
            "hint": "在（%s）被%s回复 %d 次" % (f.get("hour_hint") or "任意时段", name, HOUR_HITS),
            "rarity": "rare",
            "fn": (lambda s, pid=pid, hours=hours:
                   sum(1 for m in s["chats"]
                       if m.get("role") == "ai" and m.get("pid") == pid
                       and _hour_of(m) in hours) >= HOUR_HITS),
        })
        # ③ 四件这个人独有的
        own = f.get("own") or PERSONA_ACH_FALLBACK["own"]
        for i, item in enumerate(own):
            nm, ds, ico, rar, rule = item
            out.append({
                "id": "%s%d" % (_kind_slug(rule[0]), i + 1) + cap,
                "group": "角色", "ico": ico,
                "name": nm.format(name=name), "desc": ds.format(name=name),
                "hint": _own_hint(name, rule), "rarity": rar,
                "fn": _own_fn(pid, rule),
            })
    # 全部角色都登场过：万事屋全员到齐
    out.append({
        "id": "crewAll", "group": "角色", "ico": "🏮", "name": "万事屋全员到齐",
        "desc": "四个人都跟你说过话了。这店总算像个店了",
        "hint": "让四个人格都回复过你", "rarity": "rare",
        "fn": (lambda s, pids=pids: bool(pids)
               and all(_persona_turns(s, x) >= 1 for x in pids)),
    })
    return out


def _kind_slug(kind):
    """规则类型 → id 前缀（id 只用字母数字、全局唯一、看得出是哪类判据）。"""
    return {"kw": "topic", "ai_text": "habit", "tools": "tools", "complex": "task",
            "daymax": "burst", "dayhours": "allday", "turns": "with"}.get(kind, "own")


def _hour_of(m):
    """消息所在的小时（本地时间）；时间戳异常时返回 -1（不会命中任何时段）。"""
    try:
        return datetime.fromtimestamp(int(m.get("ts") or 0) / 1000).hour
    except Exception:
        return -1


BADGES += _build_persona_badges()

BADGE_MAP = {b["id"]: b for b in BADGES}
RARITY_LABEL = {"common": "普通", "rare": "稀有", "epic": "史诗", "legend": "传说"}
BADGE_GROUPS = ["交流", "记忆", "陪伴", "业务", "亲密度", "角色", "彩蛋"]
MAX_POP_BADGES = 4   # 单次结算最多弹几枚，避免一次性刷屏


def badge_view(b, unlocked=True, unlocked_at=None):
    return {
        "id": b["id"], "group": b.get("group", ""),
        "ico": b["ico"], "name": b["name"], "desc": b["desc"],
        "hint": b["hint"], "rarity": b["rarity"], "rarityLabel": RARITY_LABEL[b["rarity"]],
        "got": unlocked, "unlockedAt": unlocked_at,
    }


def sync_badges(s, user_id=None):
    """结算里程碑：把新达成的成就记进 stats.unlocked，并返回本次新解锁的列表。

    每枚成就只会出现在 newBadges 里一次（解锁时间已落库），
    所以前端弹窗不会重复。单次最多返回 MAX_POP_BADGES 枚，剩余的留到下次结算。
    """
    stats = s["stats"]
    unlocked = stats.setdefault("unlocked", {})
    pending = []
    for b in BADGES:
        if b["id"] in unlocked:
            continue
        try:
            ok = bool(b["fn"](s))
        except Exception:
            ok = False
        if ok:
            pending.append(b)
    chosen = pending[:MAX_POP_BADGES]
    now = now_ms()
    for b in chosen:
        unlocked[b["id"]] = now
    if user_id and DB is not None:
        DB.save_stats(user_id, stats)
    return [badge_view(b, True, now) for b in chosen]


def days_together(s):
    c = s["stats"].get("createdAt")
    if not c:
        return 1
    return max(1, (now_ms() - c) // 86400000 + 1)


def streak_days(s):
    days = set(s["stats"].get("days", {}).keys())
    if not days:
        return 0
    d = datetime.now().date()
    streak = 0
    if d.isoformat() not in days:  # 今天还没聊，从昨天起算
        d -= timedelta(days=1)
    while d.isoformat() in days and streak < 366:
        streak += 1
        d -= timedelta(days=1)
    return streak


def get_xp(s):
    return (s["stats"]["totalUser"] * 10 + s["stats"]["totalAI"] * 5
            + len(s["memories"]) * 20 + min(days_together(s), 365) * 5)


def xp_for_level(lv):
    return 60 * (lv - 1) ** 2


def get_level(s):
    xp, lv = get_xp(s), 1
    while xp_for_level(lv + 1) <= xp and lv < MAX_LEVEL:
        lv += 1
    return lv


def get_stage(lv):
    """把 1..MAX_LEVEL 均匀铺到 6 个亲密度阶段上。"""
    idx = int((max(1, min(lv, MAX_LEVEL)) - 1) * len(STAGES) / MAX_LEVEL)
    return STAGES[min(idx, len(STAGES) - 1)]


def build_badges(s):
    unlocked = s["stats"].get("unlocked", {}) or {}
    out = []
    for b in BADGES:
        try:
            ok = b["id"] in unlocked or bool(b["fn"](s))
        except Exception:
            ok = False
        out.append(badge_view(b, ok, unlocked.get(b["id"])))
    return out


def growth_view(s):
    lv, xp = get_level(s), get_xp(s)
    cur = xp_for_level(lv)
    maxed = lv >= MAX_LEVEL
    nxt = cur if maxed else xp_for_level(lv + 1)
    stage = get_stage(lv)
    badges = build_badges(s)
    return {
        "level": lv, "maxLevel": MAX_LEVEL, "maxed": maxed,
        "stage": stage, "stageDesc": STAGE_DESCS.get(stage, ""),
        "xp": xp, "xpCur": cur, "xpNext": nxt,
        "pct": 1.0 if maxed else min(1, (xp - cur) / max(1, nxt - cur)),
        "days": days_together(s), "streak": streak_days(s),
        "msgs": s["stats"]["totalUser"] + s["stats"]["totalAI"],
        "mems": len(s["memories"]),
        "createdAt": s["stats"].get("createdAt"),
        "todosMade": int(s["stats"].get("todosCreated") or 0),
        "badges": badges,
        "badgeGot": sum(1 for b in badges if b["got"]),
        "badgeTotal": len(badges),
    }
