# -*- coding: utf-8 -*-
r"""Yorozuya · 路径锚点：全项目**唯一**的数据根 `appdata/`

为什么单独一个模块（这是踩过的坑，改之前先读）：
· 从前「打包版」和「源码运行」各算各的目录 —— exe 用 `sys.executable` 同目录，源码用项目根，
  于是 `webview-data/`、`zhiban-data/` 会各出现两份（exe 旁边一份、项目根一份），
  登录状态和 agent 数据还会随启动方式悄悄"搬家"。
· 现在所有落盘路径只在这里定义一次：**不管怎么启动，都是 `<项目根>\appdata\...` 这一份**。

数据根解析顺序（`data_dir()`）：
  1. 环境变量 `YOROZUYA_DATA_DIR` —— 换机器 / 想把数据放别的盘时用（绝对路径优先）
  2. `<项目根>/appdata`
     项目根：源码运行 = 仓库根；打包运行 = 从 exe 目录**往上**找带项目标记的那一层
     （打包产物就在项目根，所以第一层通常就命中；exe 被单独拷走时才需要往上找）
  3. 兜底 = 程序落脚目录（把 exe 单独拷到别处时的合理落点，仍是**一份**）

目录约定：
    appdata/
      webview-data/       WebView2 档案（localStorage 里的登录 token 就在这儿）
      zhiban-data/        agent 内核数据（blobs/artifacts/trash/bg/demo-workspace）+ 附件上传
      last_persona.json   上次使用的人格
      yorozuya-start.log  启动日志
    <项目根>/db_config.json   MySQL 配置（仍挨着程序放，方便直接改）
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

DATA_DIR_NAME = "appdata"
ENV_DATA_DIR = "YOROZUYA_DATA_DIR"

# 判定"这一层就是项目根"的标记：任一条命中即可
def _looks_like_project(d: Path) -> bool:
    try:
        if (d / "app" / "desktop.py").is_file():
            return True
        if (d / "renderer").is_dir() and (d / "db_config.json").is_file():
            return True
        if (d / "yorozuya.spec").is_file() and (d / "app").is_dir():
            return True
    except OSError:
        return False
    return False


def is_frozen() -> bool:
    """是否跑在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


@lru_cache(maxsize=1)
def app_dir() -> Path:
    """程序落脚目录：打包 = exe 所在目录；源码 = 项目根。

    ★ 只用来找**挨着程序放的东西**（如 `vendor/rg.exe`），不用于落盘数据。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def project_dir() -> Path:
    """项目根：源码 = 仓库根；打包 = 从 exe 目录往上找，找不到就退回 exe 目录。"""
    if not is_frozen():
        return Path(__file__).resolve().parents[1]
    base = app_dir()
    for d in (base, *list(base.parents)[:3]):        # 自己 + 往上三层
        if _looks_like_project(d):
            return d
    return base


def data_dir() -> Path:
    """全项目唯一的数据根（`appdata/`）——要落盘的东西一律走这里。"""
    env = (os.environ.get(ENV_DATA_DIR) or "").strip()
    if env:
        return Path(env).expanduser()
    return project_dir() / DATA_DIR_NAME


def webview_dir() -> Path:
    """WebView2 用户档案目录（登录状态存在里面，别随便删）。"""
    return data_dir() / "webview-data"


def zhiban_data_dir() -> Path:
    """agent 内核数据 + 附件的总目录。"""
    return data_dir() / "zhiban-data"


def agent_data_dir() -> Path:
    """agent 内核数据（blobs / artifacts / trash / bg / demo-workspace 的父目录）。"""
    return zhiban_data_dir() / "agent"


def upload_dir() -> Path:
    """对话附件落盘目录（按 user_key 再分子目录隔离）。"""
    return zhiban_data_dir() / "uploads"


def persona_file() -> Path:
    """上次使用的人格缓存（启动画面抽签用）。"""
    return data_dir() / "last_persona.json"


def log_file() -> Path:
    """启动日志：打包后没有控制台，全靠它留痕。"""
    return data_dir() / "yorozuya-start.log"


def config_file() -> Path:
    """MySQL 配置 `db_config.json`：唯一一份，放项目根（挨着程序，方便直接改）。"""
    return project_dir() / "db_config.json"


def ensure_dirs() -> Path:
    """把要用的目录先建出来（幂等），返回数据根。"""
    root = data_dir()
    for d in (root, webview_dir(), zhiban_data_dir(), agent_data_dir(), upload_dir()):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return root


def describe() -> str:
    """一行说明当前用的是哪份数据（写进启动日志，排查"数据跑哪去了"最省事）。"""
    return "数据根：%s（frozen=%s，项目根=%s%s）" % (
        data_dir(), is_frozen(), project_dir(),
        "，环境变量 %s 覆盖" % ENV_DATA_DIR if (os.environ.get(ENV_DATA_DIR) or "").strip() else "",
    )
