# -*- coding: utf-8 -*-
"""内核存储：复用现有 MySQL（库 zhiban），新增 8 张 `agent_*` 表

为什么复用而不是新引一套库：账号体系、多用户隔离、迁移写法都是现成的；
内核数据与陪伴数据在**同一库、不同表**，靠 user_id 隔离。
快照 blobs 与大输出 artifacts 落磁盘（不进库），避免库体积失控。

★ 工程记忆放**独立表** `agent_memories`（不是给 memories 加 scope 列）：
  与陪伴记忆物理隔离 —— 这样「陪伴记忆被注入工程上下文」在结构上就不可能发生，
  而且完全不动现有的记忆页与记忆注入逻辑。
"""
from __future__ import annotations

import json
import time

import pymysql

from . import common

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS agent_workspaces (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  name VARCHAR(60) NOT NULL DEFAULT '',
  root_path VARCHAR(400) NOT NULL,
  test_cmd VARCHAR(300) NOT NULL DEFAULT '',
  verify_cmds TEXT,
  config_json TEXT,
  created_at BIGINT NOT NULL,
  last_used_at BIGINT NOT NULL DEFAULT 0,
  UNIQUE KEY uq_ws (user_id, root_path(180)),
  INDEX idx_ws_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_runs (
  id VARCHAR(12) PRIMARY KEY,
  user_id INT NOT NULL,
  workspace_id INT NOT NULL DEFAULT 0,
  persona_id VARCHAR(20) NOT NULL DEFAULT '',
  todo_id VARCHAR(24) NOT NULL DEFAULT '',
  goal TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'running',
  model VARCHAR(100) NOT NULL DEFAULT '',
  steps INT NOT NULL DEFAULT 0,
  tool_calls INT NOT NULL DEFAULT 0,
  tokens_in INT NOT NULL DEFAULT 0,
  tokens_out INT NOT NULL DEFAULT 0,
  seconds FLOAT NOT NULL DEFAULT 0,
  verification TEXT,
  summary MEDIUMTEXT,
  error TEXT,
  created_at BIGINT NOT NULL,
  ended_at BIGINT NOT NULL DEFAULT 0,
  INDEX idx_run_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(12) NOT NULL,
  seq INT NOT NULL,
  type VARCHAR(30) NOT NULL,
  payload MEDIUMTEXT,
  ts BIGINT NOT NULL,
  UNIQUE KEY uq_seq (run_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_tool_calls (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(12) NOT NULL,
  step INT NOT NULL DEFAULT 0,
  name VARCHAR(60) NOT NULL,
  args MEDIUMTEXT,
  risk VARCHAR(10) NOT NULL DEFAULT '',
  decision VARCHAR(10) NOT NULL DEFAULT '',
  ok TINYINT NOT NULL DEFAULT 0,
  ms INT NOT NULL DEFAULT 0,
  summary VARCHAR(400) NOT NULL DEFAULT '',
  ts BIGINT NOT NULL,
  INDEX idx_tc_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_approvals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(12) NOT NULL,
  tool_name VARCHAR(60) NOT NULL DEFAULT '',
  decision VARCHAR(10) NOT NULL DEFAULT '',
  scope VARCHAR(10) NOT NULL DEFAULT 'once',
  timeout_flag TINYINT NOT NULL DEFAULT 0,
  ts BIGINT NOT NULL,
  INDEX idx_ap_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_checkpoints (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(12) NOT NULL,
  step INT NOT NULL DEFAULT 0,
  label VARCHAR(120) NOT NULL DEFAULT '',
  files MEDIUMTEXT,
  ts BIGINT NOT NULL,
  INDEX idx_ck_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_artifacts (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(12) NOT NULL,
  step INT NOT NULL DEFAULT 0,
  kind VARCHAR(20) NOT NULL DEFAULT '',
  path VARCHAR(400) NOT NULL DEFAULT '',
  size INT NOT NULL DEFAULT 0,
  preview VARCHAR(500) NOT NULL DEFAULT '',
  ts BIGINT NOT NULL,
  INDEX idx_ar_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_memories (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  workspace_id INT NOT NULL DEFAULT 0,
  text VARCHAR(300) NOT NULL,
  source VARCHAR(16) NOT NULL DEFAULT 'user',
  ts BIGINT NOT NULL,
  INDEX idx_am (user_id, workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS agent_settings (
  user_id INT NOT NULL,
  skey VARCHAR(60) NOT NULL,
  value_json TEXT,
  ts BIGINT NOT NULL,
  PRIMARY KEY (user_id, skey)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def _ts() -> int:
    return int(time.time() * 1000)


# ★ 老库补列（`CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不做，加字段必须单独走一遍）。
#   为什么要写成「先查 information_schema 再 ALTER」而不是 `ADD COLUMN IF NOT EXISTS`：
#   后者是 MariaDB 的方言，MySQL 上会语法报错 —— 这里要两边都能跑。
#   整个迁移是**幂等**的：列已存在就跳过，重复调用无副作用。
MIGRATIONS = [
    ("agent_runs", "todo_id", "ALTER TABLE agent_runs ADD COLUMN todo_id VARCHAR(24) NOT NULL DEFAULT '' AFTER persona_id"),
    # ★ 为什么非要单独一列：原先「120 秒超时的自动拒绝」和「用户点了拒绝」在库里**长得一模一样**
    #   （decision='deny'），于是一个「审批卡没出现在用户眼前」的致命 bug 藏了一整天，
    #   只能靠审批记录之间 124 秒 ≈ 120 秒超时的时间差把它反推出来。
    #   有了这一列，`timeout_flag=1 AND decision='deny'` 就是「没人回应」，能直接查。
    ("agent_approvals", "timeout_flag",
     "ALTER TABLE agent_approvals ADD COLUMN timeout_flag TINYINT NOT NULL DEFAULT 0 AFTER scope"),
    # ★ 任务留档目录（工作区里的 `.yorozuya/runs/<时间>-<runId>/`，相对工作区根的路径）。
    #   单独一列而不是"按 runId 猜路径"：① 删除运行记录时要能**精确**清掉那个文件夹
    #   ② 时间戳目视可读（用户要在资源管理器里找）③ 目录名以后想改规则也不用迁历史数据。
    ("agent_runs", "task_dir",
     "ALTER TABLE agent_runs ADD COLUMN task_dir VARCHAR(255) NOT NULL DEFAULT '' AFTER todo_id"),
]


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        for stmt in SCHEMA:
            cur.execute(stmt)
        for table, column, ddl in MIGRATIONS:
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                        (table, column))
            row = cur.fetchone() or {}
            if int(row.get("n") or 0) == 0:
                cur.execute(ddl)


def connect(db_name: str | None = None, ensure: bool = True):
    cfg = dict(common.db_config())
    if db_name:
        cfg["database"] = db_name
    conn = pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"],
                           password=cfg["password"], database=cfg["database"],
                           charset="utf8mb4", autocommit=True,
                           cursorclass=pymysql.cursors.DictCursor)
    if ensure:
        ensure_schema(conn)
    return conn


def create_database(db_name: str) -> None:
    """验收用：建一个临时库（不动生产库）。"""
    cfg = common.db_config()
    conn = pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"],
                           password=cfg["password"], charset="utf8mb4", autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` DEFAULT CHARACTER SET utf8mb4")
    conn.close()


# ---------------- 工作区 ----------------

def upsert_workspace(conn, user_id: int, name: str, root_path: str, cfg: dict) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM agent_workspaces WHERE user_id=%s AND root_path=%s",
                    (user_id, root_path))
        row = cur.fetchone()
        now = _ts()
        if row:
            # ★ 更新时必须**连 test_cmd / verify_cmds 两列一起写**。
            #   踩过：原先只更 name/config_json/last_used_at，而 `_ws_row()` 读的是**列**不是
            #   config_json → 用户改了验证命令，接口回的还是旧值（界面显示"没生效"，
            #   而内核读的是 agent.config.json，其实已经生效了 —— 两边不一致，最难查）。
            cur.execute("UPDATE agent_workspaces SET name=%s, test_cmd=%s, verify_cmds=%s,"
                        " config_json=%s, last_used_at=%s WHERE id=%s",
                        (name, cfg.get("test_cmd", ""),
                         json.dumps(cfg.get("verify_cmds", []), ensure_ascii=False),
                         json.dumps(cfg, ensure_ascii=False), now, row["id"]))
            return row["id"]
        cur.execute("INSERT INTO agent_workspaces(user_id, name, root_path, test_cmd, verify_cmds,"
                    " config_json, created_at, last_used_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (user_id, name, root_path, cfg.get("test_cmd", ""),
                     json.dumps(cfg.get("verify_cmds", []), ensure_ascii=False),
                     json.dumps(cfg, ensure_ascii=False), now, now))
        return cur.lastrowid


def list_workspaces(conn, user_id: int) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_workspaces WHERE user_id=%s ORDER BY last_used_at DESC",
                    (user_id,))
        return cur.fetchall()


def get_workspace(conn, user_id: int, ws_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_workspaces WHERE user_id=%s AND id=%s", (user_id, ws_id))
        return cur.fetchone()


def delete_workspace(conn, user_id: int, ws_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM agent_workspaces WHERE user_id=%s AND id=%s", (user_id, ws_id))


# ---------------- 运行 / 事件 ----------------

def create_run(conn, run_id: str, user_id: int, workspace_id: int, goal: str,
               model: str, persona: str, todo_id: str = "") -> None:
    """todo_id：这次运行是「从哪条待办派出去的」（空 = 不是从待办起的）。

    为什么要落库而不是只放前端内存：跑完要自动勾掉那条待办、刷新/回放时也要显示
    「这条运行对应哪条待办」—— 前端一刷新内存就没了，只有落库才对得上。
    """
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_runs(id, user_id, workspace_id, persona_id, todo_id, goal,"
                    " status, model, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, user_id, workspace_id, persona, todo_id or "", goal, "running", model,
                     _ts()))


def finish_run(conn, run_id: str, status: str, *, steps=0, tool_calls=0, tokens_in=0, tokens_out=0,
               seconds=0.0, summary="", error="", verification=None) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_runs SET status=%s, steps=%s, tool_calls=%s, tokens_in=%s,"
                    " tokens_out=%s, seconds=%s, summary=%s, error=%s, verification=%s, ended_at=%s"
                    " WHERE id=%s",
                    (status, steps, tool_calls, tokens_in, tokens_out, seconds,
                     common.redact(summary), common.redact(error),
                     json.dumps(verification or {}, ensure_ascii=False), _ts(), run_id))


def get_run(conn, run_id: str):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_runs WHERE id=%s", (run_id,))
        return cur.fetchone()


def list_runs(conn, user_id: int, limit: int = 30) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT r.*, w.name AS ws_name FROM agent_runs r "
                    "LEFT JOIN agent_workspaces w ON w.id=r.workspace_id "
                    "WHERE r.user_id=%s ORDER BY r.created_at DESC LIMIT %s", (user_id, limit))
        return cur.fetchall()


def set_task_dir(conn, run_id: str, rel: str) -> None:
    """记下这次运行的留档目录（相对工作区根）。删除记录时据此精确清理。"""
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_runs SET task_dir=%s WHERE id=%s", (str(rel or "")[:255], run_id))


# 一次运行在库里留下的所有痕迹（删除时按这个顺序清；顺序无所谓，但列全很重要 ——
# 漏一张表就会留下"孤儿行"：列表里没有了，但事件/审批还占着，成本统计也会算错）
_RUN_TABLES = ("agent_events", "agent_tool_calls", "agent_approvals", "agent_checkpoints",
               "agent_artifacts")


def delete_run(conn, user_id: int, run_id: str) -> dict:
    """删除一次运行（**只允许删自己的**）。

    返回 {ok, deleted:{表:行数}, task_dir, artifacts}；不属于该用户则 ok=False。
    为什么带 user_id 校验而不是只认 id：run_id 是 12 位随机串，但仍然没有任何理由允许越权删。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, task_dir FROM agent_runs WHERE id=%s AND user_id=%s",
                    (run_id, user_id))
        row = cur.fetchone()
    if not row:
        return {"ok": False, "error": "没有这次运行（或不属于你）"}
    deleted = {}
    with conn.cursor() as cur:
        for table in _RUN_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE run_id=%s", (run_id,))
            deleted[table] = cur.rowcount
        cur.execute("DELETE FROM agent_runs WHERE id=%s AND user_id=%s", (run_id, user_id))
        deleted["agent_runs"] = cur.rowcount
    return {"ok": True, "deleted": deleted, "task_dir": row.get("task_dir") or ""}


def purge_runs(conn, user_id: int, run_ids: list) -> dict:
    """批量删除（界面上「清空当前筛选结果」用它）。返回 {deleted: n, runs: [...]}"""
    ids = [str(x) for x in (run_ids or []) if str(x).strip()][:500]
    out = {"deleted": 0, "runs": [], "task_dirs": [], "artifacts": []}
    for rid in ids:
        got = delete_run(conn, user_id, rid)
        if got.get("ok"):
            out["deleted"] += 1
            out["runs"].append(rid)
            if got.get("task_dir"):
                out["task_dirs"].append(got["task_dir"])
    return out


def add_event(conn, run_id: str, etype: str, payload: dict) -> int:
    """事件带单调递增的 seq —— 断线续传（?after=）与回放都靠它。"""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM agent_events WHERE run_id=%s", (run_id,))
        seq = int(cur.fetchone()["s"]) + 1
        cur.execute("INSERT INTO agent_events(run_id, seq, type, payload, ts) VALUES(%s,%s,%s,%s,%s)",
                    (run_id, seq, etype, common.redact(json.dumps(payload, ensure_ascii=False)), _ts()))
        return seq


def list_events(conn, run_id: str, after: int = 0) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_events WHERE run_id=%s AND seq>%s ORDER BY seq",
                    (run_id, after))
        return cur.fetchall()


def add_tool_call(conn, run_id: str, step: int, name: str, args: dict, risk: str,
                  decision: str, ok: bool, ms: int, summary: str) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_tool_calls(run_id, step, name, args, risk, decision, ok, ms,"
                    " summary, ts) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, step, name, common.redact(json.dumps(args, ensure_ascii=False)),
                     risk, decision, 1 if ok else 0, ms, summary[:400] or "", _ts()))


def list_tool_calls(conn, run_id: str) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_tool_calls WHERE run_id=%s ORDER BY id", (run_id,))
        return cur.fetchall()


def add_approval(conn, run_id: str, tool_name: str, decision: str, scope: str,
                 timed_out: bool = False) -> None:
    """记一条审批裁决。

    `timed_out=True` 表示**不是用户点的**，是 120 秒没人回应自动按拒绝处理 ——
    这两件事在界面上、在库里、在给模型的话术里都必须能分开（见 MIGRATIONS 的注释）。
    """
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_approvals(run_id, tool_name, decision, scope, timeout_flag, ts)"
                    " VALUES(%s,%s,%s,%s,%s,%s)",
                    (run_id, tool_name, decision, scope, 1 if timed_out else 0, _ts()))


def add_checkpoint(conn, run_id: str, step: int, label: str, files: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_checkpoints(run_id, step, label, files, ts)"
                    " VALUES(%s,%s,%s,%s,%s)",
                    (run_id, step, label, json.dumps(files, ensure_ascii=False), _ts()))


def list_checkpoints(conn, run_id: str) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_checkpoints WHERE run_id=%s ORDER BY id", (run_id,))
        return cur.fetchall()


def add_artifact(conn, run_id: str, step: int, kind: str, path: str, size: int, preview: str) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_artifacts(run_id, step, kind, path, size, preview, ts)"
                    " VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, step, kind, path, size, preview[:500], _ts()))


def list_artifacts(conn, run_id: str) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_artifacts WHERE run_id=%s ORDER BY id", (run_id,))
        return cur.fetchall()


# ---------------- 工程记忆（与陪伴记忆物理隔离） ----------------

def add_memory(conn, user_id: int, workspace_id: int, text: str, source: str = "user") -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_memories(user_id, workspace_id, text, source, ts)"
                    " VALUES(%s,%s,%s,%s,%s)", (user_id, workspace_id, text[:300], source, _ts()))


def list_memories(conn, user_id: int, workspace_id: int | None = None) -> list:
    with conn.cursor() as cur:
        if workspace_id is None:
            cur.execute("SELECT * FROM agent_memories WHERE user_id=%s ORDER BY ts DESC LIMIT 50",
                        (user_id,))
        else:
            cur.execute("SELECT * FROM agent_memories WHERE user_id=%s AND workspace_id=%s"
                        " ORDER BY ts DESC LIMIT 50", (user_id, workspace_id))
        return cur.fetchall()


# ---------------- 用户级 agent 设置（免得每加一个开关就改表结构） ----------------

def get_setting(conn, user_id: int, key: str, default=None):
    with conn.cursor() as cur:
        cur.execute("SELECT value_json FROM agent_settings WHERE user_id=%s AND skey=%s",
                    (user_id, key))
        row = cur.fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default


def set_setting(conn, user_id: int, key: str, value) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_settings(user_id, skey, value_json, ts) VALUES(%s,%s,%s,%s)"
                    " ON DUPLICATE KEY UPDATE value_json=VALUES(value_json), ts=VALUES(ts)",
                    (user_id, key, json.dumps(value, ensure_ascii=False), _ts()))


def stats(conn, user_id: int) -> dict:
    """成本与步数仪表：一次聚合出所有要显示的数字（不在前端做 N 次请求）。"""
    out = {"runs": 0, "done": 0, "verifyFailed": 0, "error": 0, "stopped": 0, "budget": 0,
           "steps": 0, "toolCalls": 0, "tokensIn": 0, "tokensOut": 0, "seconds": 0.0,
           "approvals": 0, "denied": 0, "undos": 0, "filesTouched": 0, "workspaces": 0}
    with conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS runs,
                              COALESCE(SUM(steps),0) AS steps,
                              COALESCE(SUM(tool_calls),0) AS tool_calls,
                              COALESCE(SUM(tokens_in),0) AS tokens_in,
                              COALESCE(SUM(tokens_out),0) AS tokens_out,
                              COALESCE(SUM(seconds),0) AS seconds,
                              COALESCE(SUM(status='done'),0) AS done,
                              COALESCE(SUM(status='verify_failed'),0) AS verify_failed,
                              COALESCE(SUM(status='error'),0) AS error,
                              COALESCE(SUM(status='stopped'),0) AS stopped,
                              COALESCE(SUM(status='budget'),0) AS budget
                       FROM agent_runs WHERE user_id=%s""", (user_id,))
        r = cur.fetchone() or {}
        out.update({"runs": int(r.get("runs") or 0), "steps": int(r.get("steps") or 0),
                    "toolCalls": int(r.get("tool_calls") or 0),
                    "tokensIn": int(r.get("tokens_in") or 0),
                    "tokensOut": int(r.get("tokens_out") or 0),
                    "seconds": round(float(r.get("seconds") or 0), 1),
                    "done": int(r.get("done") or 0),
                    "verifyFailed": int(r.get("verify_failed") or 0),
                    "error": int(r.get("error") or 0), "stopped": int(r.get("stopped") or 0),
                    "budget": int(r.get("budget") or 0)})
        cur.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(a.decision='deny'),0) AS denied
                       FROM agent_approvals a JOIN agent_runs r ON r.id=a.run_id
                       WHERE r.user_id=%s""", (user_id,))
        a = cur.fetchone() or {}
        out["approvals"] = int(a.get("n") or 0)
        out["denied"] = int(a.get("denied") or 0)
        cur.execute("""SELECT COUNT(*) AS n FROM agent_events e JOIN agent_runs r ON r.id=e.run_id
                       WHERE r.user_id=%s AND e.type='run.undo'""", (user_id,))
        out["undos"] = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute("""SELECT COUNT(DISTINCT ck.run_id) AS n FROM agent_checkpoints ck
                       JOIN agent_runs r ON r.id=ck.run_id WHERE r.user_id=%s""", (user_id,))
        out["filesTouched"] = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute("SELECT COUNT(*) AS n FROM agent_workspaces WHERE user_id=%s", (user_id,))
        out["workspaces"] = int((cur.fetchone() or {}).get("n") or 0)
    return out
