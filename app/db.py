# -*- coding: utf-8 -*-
"""Yorozuya · MySQL 数据层
用户账号（注册/登录/会话）+ 按用户隔离的数据（对话/记忆/待办/设置/成长统计）
配置：db_config.json（不存在则用默认值并自动生成）
"""
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

import pymysql

# ★ 路径一律问 yorozuya/paths.py（全项目唯一的数据根），别再在各自文件里推算。
#   单独 `python app/server.py` 调试时本文件先于 sys.path 设置被 import，所以这里自己兜一次。
if not getattr(sys, "frozen", False):
    _ROOT = Path(__file__).resolve().parents[1]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
from yorozuya import paths        # noqa: E402

CONFIG_FILE = paths.config_file()          # <项目根>/db_config.json（唯一一份）
DATA_DIR = paths.data_dir()                # <项目根>/appdata（唯一一份）
# 旧名保留：历史代码里 `db.BASE_DIR` 的语义就是"落盘数据目录"（last_persona.json 等）
BASE_DIR = DATA_DIR

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "zhiban",
}

_lock = threading.RLock()
_conn = None          # 兼容旧引用：新代码请用 conn()（按线程持有）

SESSION_DAYS = 30
PBKDF2_ITERS = 200_000


# ---------------- 配置 ----------------

def get_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
        except Exception:
            pass
    # 环境变量优先（ZHIBAN_DB_HOST / ZHIBAN_DB_PORT / ZHIBAN_DB_USER / ZHIBAN_DB_PASS / ZHIBAN_DB_NAME）
    env = os.environ
    if env.get("ZHIBAN_DB_HOST"):
        cfg["host"] = env["ZHIBAN_DB_HOST"]
    if env.get("ZHIBAN_DB_PORT"):
        try:
            cfg["port"] = int(env["ZHIBAN_DB_PORT"])
        except ValueError:
            pass
    if env.get("ZHIBAN_DB_USER"):
        cfg["user"] = env["ZHIBAN_DB_USER"]
    if env.get("ZHIBAN_DB_PASS"):
        cfg["password"] = env["ZHIBAN_DB_PASS"]
    if env.get("ZHIBAN_DB_NAME"):
        cfg["database"] = env["ZHIBAN_DB_NAME"]
    return cfg


# ---------------- 连接 ----------------

# ★ 连接必须「按线程」持有：pymysql 的连接不是线程安全的。
#   实测症状：多个请求并发时（例如一条消息里带几张附件图，前端并发取回）
#   会在服务端抛 `pymysql.err.InternalError: Packet sequence number wrong - got 2 expected 5`，
#   表现成随机 500。之前只有单用户桌面端、并发少，所以侥幸没暴露。
_local = threading.local()


def _connect(with_db=True):
    cfg = get_config()
    kw = dict(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"],
              password=cfg["password"], charset="utf8mb4",
              autocommit=True, cursorclass=pymysql.cursors.DictCursor)
    if with_db:
        kw["database"] = cfg["database"]
    return pymysql.connect(**kw)


def conn():
    """取本线程的连接（断线自动重连）。"""
    c = getattr(_local, "c", None)
    if c is not None:
        try:
            c.ping(reconnect=True)
            return c
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            _local.c = None
    with _lock:                      # 建连本身加锁，避免同一线程被并发进来创建两条
        c = _connect()
        _local.c = c
        return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  pass_hash CHAR(64) NOT NULL,
  salt CHAR(32) NOT NULL,
  created_at BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sessions (
  token CHAR(64) PRIMARY KEY,
  user_id INT NOT NULL,
  created_at BIGINT NOT NULL,
  expires_at BIGINT NOT NULL,
  INDEX idx_session_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- 会话（历史对话）：一次「新建对话」= 一条会话，消息挂在会话下
CREATE TABLE IF NOT EXISTS conversations (
  id VARCHAR(20) PRIMARY KEY,
  user_id INT NOT NULL,
  title VARCHAR(60) NOT NULL DEFAULT '',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  INDEX idx_conv_user (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chats (
  id VARCHAR(20) PRIMARY KEY,
  user_id INT NOT NULL,
  conv VARCHAR(20) NOT NULL DEFAULT '',
  ts BIGINT NOT NULL,
  role VARCHAR(10) NOT NULL,
  text MEDIUMTEXT,
  persona VARCHAR(20) NOT NULL DEFAULT '',
  meta TEXT,
  INDEX idx_chat_user (user_id, ts),
  INDEX idx_chat_conv (user_id, conv, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS memories (
  id VARCHAR(20) PRIMARY KEY,
  user_id INT NOT NULL,
  ts BIGINT NOT NULL,
  text VARCHAR(200) NOT NULL,
  type VARCHAR(10) NOT NULL DEFAULT '事实',
  pinned TINYINT NOT NULL DEFAULT 0,
  source VARCHAR(16) NOT NULL DEFAULT 'user',
  INDEX idx_mem_user (user_id, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS todos (
  id VARCHAR(20) PRIMARY KEY,
  user_id INT NOT NULL,
  text VARCHAR(200) NOT NULL,
  due BIGINT NULL,
  done TINYINT NOT NULL DEFAULT 0,
  reminded TINYINT NOT NULL DEFAULT 0,
  created_at BIGINT NOT NULL,
  INDEX idx_todo_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_settings (
  user_id INT PRIMARY KEY,
  api_url VARCHAR(300) NOT NULL DEFAULT 'https://api.openai.com/v1',
  api_key VARCHAR(300) NOT NULL DEFAULT '',
  model VARCHAR(100) NOT NULL DEFAULT 'gpt-4o-mini',
  persona_id VARCHAR(20) NOT NULL DEFAULT 'gintoki',
  persona_name VARCHAR(50) NOT NULL DEFAULT '银时',
  user_name VARCHAR(50) NOT NULL DEFAULT '',
  persona_prompt TEXT,
  auto_extract TINYINT NOT NULL DEFAULT 1,
  tools_enabled TINYINT NOT NULL DEFAULT 1,
  show_think TINYINT NOT NULL DEFAULT 1,
  conv_id VARCHAR(20) NOT NULL DEFAULT '',
  route_mode VARCHAR(10) NOT NULL DEFAULT 'ask',
  classifier_mode VARCHAR(10) NOT NULL DEFAULT 'rules',
  ui_mode VARCHAR(12) NOT NULL DEFAULT 'auto',
  lang VARCHAR(12) NOT NULL DEFAULT 'zh-CN'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_stats (
  user_id INT PRIMARY KEY,
  stats JSON NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _uid():
    """本地 ID 生成（与 yorozuya.common.uid 同格式）。
    db.py 刻意不 import yorozuya，避免 data 层反向依赖业务层造成循环导入。"""
    return uuid.uuid4().hex[:12]


def init_db():
    """建库（如不存在）+ 建表。失败抛出异常。"""
    cfg = get_config()
    with _lock:
        c = _connect(with_db=False)
        try:
            with c.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        finally:
            c.close()
        # 本线程的连接作废（其它线程的靠 ping(reconnect=True) 自愈）
        old = getattr(_local, "c", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
            _local.c = None
        c = _connect()
        with c.cursor() as cur:
            for stmt in SCHEMA.strip().split(";"):
                if stmt.strip():
                    cur.execute(stmt)
        _migrate(c)
        # 顺手清理过期会话
        with c.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at < %s", (int(time.time() * 1000),))


def _column_exists(cur, table, column):
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                (table, column))
    return cur.fetchone()["n"] > 0


def _migrate(c):
    """轻量迁移：给老库补字段。"""
    with c.cursor() as cur:
        # chats.persona —— 记录这条回复是哪个性格说的（切换人格后历史头像不变）
        if not _column_exists(cur, "chats", "persona"):
            cur.execute("ALTER TABLE chats ADD COLUMN persona VARCHAR(20) NOT NULL DEFAULT ''")
            # 人格系统上线前的历史回复都出自银时，补登记以免头像出错
            cur.execute("UPDATE chats SET persona='gintoki' WHERE role='ai' AND (persona='' OR persona IS NULL)")
        # user_settings.persona_id —— 记住上次选的人格（否则重启/重新登录会退回默认）
        if not _column_exists(cur, "user_settings", "persona_id"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN persona_id VARCHAR(20) NOT NULL DEFAULT 'gintoki'")
            # 老数据只有中文名，按名字回填 id
            for name, pid in (("银时", "gintoki"), ("神乐", "kagura"),
                              ("新八", "shinpachi"), ("小玉", "tama")):
                cur.execute("UPDATE user_settings SET persona_id=%s WHERE persona_name=%s", (pid, name))
        # chats.meta —— 存一条回复的思考过程与工具调用记录（JSON）
        if not _column_exists(cur, "chats", "meta"):
            cur.execute("ALTER TABLE chats ADD COLUMN meta TEXT")
        # 工具调用 / 思考过程开关
        if not _column_exists(cur, "user_settings", "tools_enabled"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN tools_enabled TINYINT NOT NULL DEFAULT 1")
        if not _column_exists(cur, "user_settings", "show_think"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN show_think TINYINT NOT NULL DEFAULT 1")
        # memories.source —— 记忆的来源角色：user(只从用户原话提取) / assistant(AI 显式写入) / legacy(旧数据，来源不可考)
        # 引入原因：此前自动提取把 AI 回复一起喂给提取器，导致 AI 编造/串台的内容被当成「用户的事实」写进长期记忆。
        if not _column_exists(cur, "memories", "source"):
            cur.execute("ALTER TABLE memories ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'user'")
            # 老数据无法判定来源，如实标成 legacy —— 不假装它是用户说的，便于之后单独审计/清理
            cur.execute("UPDATE memories SET source='legacy'")
        # 会话（历史对话）：settings 记住当前会话，chats 记住消息归属
        if not _column_exists(cur, "user_settings", "conv_id"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN conv_id VARCHAR(20) NOT NULL DEFAULT ''")
        # agent：人格任务分流（route_mode=ask|auto；classifier_mode=rules|hybrid|llm）
        if not _column_exists(cur, "user_settings", "route_mode"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN route_mode VARCHAR(10) NOT NULL DEFAULT 'ask'")
        if not _column_exists(cur, "user_settings", "classifier_mode"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN classifier_mode VARCHAR(10) NOT NULL DEFAULT 'rules'")
        # ★ 外观主题 / 界面语言：设置页上写着「点选即刻生效并记住」，但这两列**从来就没有**，
        #   save_settings 的 SQL 里也没有它们 —— 于是只在服务进程的内存里有效，
        #   重启（或换台机器）就回到 auto / zh-CN。实测：PUT uiMode=dark 之后重启，GET /api/state 变回 auto。
        if not _column_exists(cur, "user_settings", "ui_mode"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN ui_mode VARCHAR(12) NOT NULL DEFAULT 'auto'")
        if not _column_exists(cur, "user_settings", "lang"):
            cur.execute("ALTER TABLE user_settings ADD COLUMN lang VARCHAR(12) NOT NULL DEFAULT 'zh-CN'")
        if not _column_exists(cur, "chats", "conv"):
            cur.execute("ALTER TABLE chats ADD COLUMN conv VARCHAR(20) NOT NULL DEFAULT ''")
        # 把「有消息但没会话归属」的老数据收进一条会话里 ——
        # 否则这些历史消息在新界面里会一条都看不到（等于被隐藏）。
        # 幂等：只会给「还存在 conv='' 消息」的用户补建，补完条件自然不再成立。
        cur.execute("SELECT DISTINCT user_id FROM chats WHERE conv='' OR conv IS NULL")
        orphan_users = [r["user_id"] for r in cur.fetchall()]
        for ouid in orphan_users:
            cur.execute("SELECT text FROM chats WHERE user_id=%s AND role='user' ORDER BY ts LIMIT 1", (ouid,))
            first = cur.fetchone()
            cur.execute("SELECT MIN(ts) AS a, MAX(ts) AS b FROM chats WHERE user_id=%s", (ouid,))
            span = cur.fetchone()
            title = (first["text"] if first and first["text"] else "")[:40].strip() or "旧对话"
            cid = _uid()
            cur.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (cid, ouid, title, span["a"] or 0, span["b"] or 0))
            cur.execute("UPDATE chats SET conv=%s WHERE user_id=%s AND (conv='' OR conv IS NULL)", (cid, ouid))
            cur.execute("UPDATE user_settings SET conv_id=%s WHERE user_id=%s AND (conv_id='' OR conv_id IS NULL)",
                        (cid, ouid))
            print(f"[migrate] 已把 user={ouid} 的历史消息归入会话 {cid}（{title}）")


# ---------------- 密码与会话 ----------------

def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERS).hex()


_USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5]{2,20}$")


def validate_credentials(username, password):
    if not username or not _USERNAME_RE.match(username):
        return "用户名需 2-20 位（中文、字母、数字、下划线）"
    if not password or len(password) < 6 or len(password) > 64:
        return "密码长度需在 6-64 位之间"
    return None


def create_user(username, password):
    with conn().cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            raise ValueError("用户名已被占用")
        salt = secrets.token_hex(16)
        cur.execute(
            "INSERT INTO users (username, pass_hash, salt, created_at) VALUES (%s,%s,%s,%s)",
            (username, _hash_pw(password, salt), salt, int(time.time() * 1000)))
        uid_ = cur.lastrowid
    return uid_


def verify_login(username, password):
    with conn().cursor() as cur:
        cur.execute("SELECT id, pass_hash, salt FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
    if not row or row["pass_hash"] != _hash_pw(password, row["salt"]):
        return None
    return row["id"]


def create_session(user_id):
    token = secrets.token_hex(32)
    now = int(time.time() * 1000)
    with conn().cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (%s,%s,%s,%s)",
            (token, user_id, now, now + SESSION_DAYS * 86400000))
    return token


def resolve_token(token):
    if not token:
        return None
    with conn().cursor() as cur:
        cur.execute(
            "SELECT s.user_id, u.username FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=%s AND s.expires_at > %s", (token, int(time.time() * 1000)))
        row = cur.fetchone()
    return {"userId": row["user_id"], "username": row["username"]} if row else None


def delete_session(token):
    with conn().cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token=%s", (token,))


def change_password(user_id, old_pw, new_pw):
    with conn().cursor() as cur:
        cur.execute("SELECT pass_hash, salt FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        if not row or row["pass_hash"] != _hash_pw(old_pw, row["salt"]):
            raise ValueError("原密码不正确")
        salt = secrets.token_hex(16)
        cur.execute("UPDATE users SET pass_hash=%s, salt=%s WHERE id=%s",
                    (_hash_pw(new_pw, salt), salt, user_id))
    # 修改密码后踢掉其他会话
    with conn().cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE user_id=%s AND token<>%s", (user_id, ""))


# ---------------- 用户数据读写 ----------------

def load_user_data(user_id):
    c = conn()
    data = {"settings": None, "memories": [], "chats": [], "todos": [], "stats": None,
            "conversations": []}
    with c.cursor() as cur:
        cur.execute("SELECT * FROM user_settings WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        if row:
            data["settings"] = {
                "apiUrl": row["api_url"], "apiKey": row["api_key"], "model": row["model"],
                "personaId": row.get("persona_id") or "gintoki",
                "personaName": row["persona_name"], "userName": row["user_name"],
                "personaPrompt": row["persona_prompt"] or "", "autoExtract": bool(row["auto_extract"]),
                "toolsEnabled": bool(row.get("tools_enabled", 1)),
                "showThink": bool(row.get("show_think", 1)),
                "uiMode": (row.get("ui_mode") if hasattr(row, "get") else None) or "auto",
                "lang": (row.get("lang") if hasattr(row, "get") else None) or "zh-CN",
                "convId": (row.get("conv_id") if hasattr(row, "get") else "") or "",
                # ★ 人格任务分流：ask=先推脱+一键切换 / auto=直接转交小玉；分类器默认纯规则（不烧 token）
                "routeMode": (row.get("route_mode") if hasattr(row, "get") else None) or "ask",
                "classifierMode": (row.get("classifier_mode") if hasattr(row, "get") else None) or "rules",
            }
        cur.execute("SELECT * FROM memories WHERE user_id=%s ORDER BY ts", (user_id,))
        for r in cur.fetchall():
            data["memories"].append({"id": r["id"], "ts": r["ts"], "text": r["text"],
                                     "type": r["type"], "pinned": bool(r["pinned"]),
                                     "source": (r.get("source") if hasattr(r, "get") else None) or "legacy"})
        cur.execute("SELECT * FROM chats WHERE user_id=%s ORDER BY ts", (user_id,))
        for r in cur.fetchall():
            meta = {}
            try:
                meta = json.loads(r.get("meta") or "{}") or {}
            except Exception:
                meta = {}
            data["chats"].append({"id": r["id"], "ts": r["ts"], "role": r["role"],
                                  "text": r["text"] or "", "pid": r.get("persona") or "",
                                  "conv": (r.get("conv") if hasattr(r, "get") else "") or "",
                                  "think": meta.get("think", ""), "tools": meta.get("tools", []),
                                  "toolsOff": bool(meta.get("toolsOff")),
                                  "route": meta.get("route", {}),
                                  "taskLevel": meta.get("taskLevel", ""),
                                  "switchedTo": meta.get("switchedTo", ""),
                                  "agent": meta.get("agent", {}),
                                  "files": meta.get("files", [])})
        cur.execute("SELECT * FROM conversations WHERE user_id=%s ORDER BY updated_at DESC", (user_id,))
        for r in cur.fetchall():
            data["conversations"].append({"id": r["id"], "title": r["title"] or "",
                                          "createdAt": r["created_at"], "updatedAt": r["updated_at"]})
        cur.execute("SELECT * FROM todos WHERE user_id=%s", (user_id,))
        for r in cur.fetchall():
            data["todos"].append({"id": r["id"], "text": r["text"], "due": r["due"],
                                  "done": bool(r["done"]), "reminded": bool(r["reminded"]),
                                  "createdAt": r["created_at"]})
        cur.execute("SELECT stats FROM user_stats WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        if row:
            try:
                data["stats"] = json.loads(row["stats"])
            except Exception:
                pass
    return data


def save_settings(user_id, st):
    with conn().cursor() as cur:
        cur.execute(
            "REPLACE INTO user_settings (user_id, api_url, api_key, model, persona_id, "
            "persona_name, user_name, persona_prompt, auto_extract, tools_enabled, show_think, conv_id, "
            "route_mode, classifier_mode, ui_mode, lang) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (user_id, st.get("apiUrl", ""), st.get("apiKey", ""), st.get("model", ""),
             st.get("personaId", "gintoki"),
             st.get("personaName", ""), st.get("userName", ""),
             st.get("personaPrompt", ""), 1 if st.get("autoExtract") else 0,
             1 if st.get("toolsEnabled", True) else 0,
             1 if st.get("showThink", True) else 0,
             st.get("convId", "") or "",
             st.get("routeMode", "ask") or "ask",
             st.get("classifierMode", "rules") or "rules",
             # ★ 外观主题 / 语言：漏了这两列 = 设置页说"会记住"但重启就丢（曾经就是这样）
             st.get("uiMode", "auto") or "auto",
             st.get("lang", "zh-CN") or "zh-CN"))


def save_stats(user_id, stats):
    with conn().cursor() as cur:
        cur.execute("REPLACE INTO user_stats (user_id, stats) VALUES (%s,%s)",
                    (user_id, json.dumps(stats, ensure_ascii=False)))


def insert_chat(user_id, msg):
    meta = {}
    if msg.get("think"):
        meta["think"] = msg["think"]
    if msg.get("tools"):
        meta["tools"] = msg["tools"]
    # ★ 任务分流：把「这条回复是谁、什么级别、要不要给一键切换卡」一起留下，
    #    这样刷新页面后提示卡还在（不然只在当前这次流里看得到）。
    if msg.get("route"):
        meta["route"] = msg["route"]
    if msg.get("taskLevel"):
        meta["taskLevel"] = msg["taskLevel"]
    if msg.get("switchedTo"):
        meta["switchedTo"] = msg["switchedTo"]
    if msg.get("files"):
        meta["files"] = msg["files"]        # 对话附件：跟着消息存，刷新后仍在
    if msg.get("agent"):
        meta["agent"] = msg["agent"]        # 工作台结论：记住它是哪次运行留下的（刷新后还认得出）
    if msg.get("toolsOff"):
        # ★ 这句是「工具开关关着」那段时间说的 —— 必须留痕。打开工具后回灌历史时，
        #   history_for_persona 会给它加「只在当时成立」的前缀；不留痕的话模型会拿自己的
        #   旧话继续推托（明明开着也不去查，甚至继续编数字）。
        meta["toolsOff"] = True
    with conn().cursor() as cur:
        cur.execute("INSERT INTO chats (id, user_id, conv, ts, role, text, persona, meta) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (msg["id"], user_id, msg.get("conv", "") or "", msg["ts"], msg["role"], msg["text"],
                     msg.get("pid", "") or "",
                     json.dumps(meta, ensure_ascii=False) if meta else None))


def clear_chats(user_id):
    with conn().cursor() as cur:
        cur.execute("DELETE FROM chats WHERE user_id=%s", (user_id,))


def clear_chat_conv(user_id, conv_id):
    """只清空某一条会话的消息（有了历史对话之后，清空不再等于把过去全删掉）。"""
    with conn().cursor() as cur:
        cur.execute("DELETE FROM chats WHERE user_id=%s AND conv=%s", (user_id, conv_id))


# ---------------- 会话 / 历史对话 ----------------

def insert_conversation(user_id, c):
    with conn().cursor() as cur:
        cur.execute("INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (c["id"], user_id, (c.get("title") or "")[:60], c["createdAt"], c["updatedAt"]))


def update_conversation(user_id, c):
    with conn().cursor() as cur:
        cur.execute("UPDATE conversations SET title=%s, updated_at=%s WHERE id=%s AND user_id=%s",
                    ((c.get("title") or "")[:60], c["updatedAt"], c["id"], user_id))


def delete_conversation(user_id, cid):
    """删会话连同它名下的消息（只有用户主动删除才会走到这里）。"""
    with conn().cursor() as cur:
        cur.execute("DELETE FROM chats WHERE user_id=%s AND conv=%s", (user_id, cid))
        cur.execute("DELETE FROM conversations WHERE id=%s AND user_id=%s", (cid, user_id))


def add_memory(user_id, m):
    with conn().cursor() as cur:
        cur.execute(
            "INSERT INTO memories (id, user_id, ts, text, type, pinned, source) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (m["id"], user_id, m["ts"], m["text"], m.get("type", "事实"),
             1 if m.get("pinned") else 0, m.get("source") or "user"))


def update_memory(user_id, m):
    with conn().cursor() as cur:
        cur.execute("UPDATE memories SET text=%s, pinned=%s WHERE id=%s AND user_id=%s",
                    (m["text"], 1 if m.get("pinned") else 0, m["id"], user_id))


def delete_memory(user_id, mid):
    with conn().cursor() as cur:
        cur.execute("DELETE FROM memories WHERE id=%s AND user_id=%s", (mid, user_id))


def add_todo(user_id, t):
    with conn().cursor() as cur:
        cur.execute(
            "INSERT INTO todos (id, user_id, text, due, done, reminded, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (t["id"], user_id, t["text"], t.get("due"), 1 if t.get("done") else 0,
             1 if t.get("reminded") else 0, t["createdAt"]))


def update_todo(user_id, t):
    with conn().cursor() as cur:
        cur.execute("UPDATE todos SET done=%s, reminded=%s WHERE id=%s AND user_id=%s",
                    (1 if t.get("done") else 0, 1 if t.get("reminded") else 0, t["id"], user_id))


def delete_todo(user_id, tid):
    with conn().cursor() as cur:
        cur.execute("DELETE FROM todos WHERE id=%s AND user_id=%s", (tid, user_id))


def replace_user_data(user_id, data):
    """导入 / 重置：整体覆盖某用户的数据。"""
    c = conn()
    with c.cursor() as cur:
        cur.execute("DELETE FROM chats WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM conversations WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM memories WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM todos WHERE user_id=%s", (user_id,))
    for cv in data.get("conversations", [])[:500]:
        insert_conversation(user_id, cv)
    for m in data.get("memories", [])[:2000]:
        add_memory(user_id, m)
    for t in data.get("todos", [])[:2000]:
        add_todo(user_id, t)
    for m in data.get("chats", [])[-2000:]:
        insert_chat(user_id, m)
    save_settings(user_id, data["settings"])
    save_stats(user_id, data["stats"])
