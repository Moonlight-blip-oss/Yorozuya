# -*- coding: utf-8 -*-
"""Yorozuya · 工程内核（agent 层）

一句话：让 Yorozuya 从「陪你说话」长成「陪你说话 + 替你干活」。

模块地图
    common      常量、脱敏、裁剪、路径
    classifier  ★ 任务复杂度判定（simple / complex）—— 人格分流的判据
    router      ★ 人格路由（闲聊归三人、复杂任务归小玉；三人格会推脱）
    workspace   工作区 + 路径 jail
    storage     MySQL（agent_* 表）
    snapshot    内容寻址快照 + undo
    tools/      工具注册表与实现（读 4 / 写 2 / 执行 1 / 工程记忆 2）
    permissions 权限引擎（allow/ask/deny + 信任档位 + 命令黑名单）
    context     上下文装配（分层 + 预算 + 反注入）
    model       模型适配（OpenAI 兼容 + 脚本化假模型）
    kernel      Agent Loop + ★ 验证闸门
    report      运行报告导出
"""
from . import classifier, common, context, kernel, model, permissions, report, router, snapshot, storage  # noqa: F401
from .kernel import Kernel, RunResult  # noqa: F401
from .router import TASK_PERSONA, Decision, route  # noqa: F401
from .workspace import Workspace, WorkspaceConfig  # noqa: F401

__all__ = ["Kernel", "RunResult", "Workspace", "WorkspaceConfig", "route", "Decision",
           "TASK_PERSONA", "classifier", "router", "kernel", "storage", "snapshot", "report",
           "common", "context", "model", "permissions"]
