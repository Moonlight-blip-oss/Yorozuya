# -*- coding: utf-8 -*-
"""任务留档：跑完一次委托，在工作区里给它包一个文件夹

目录形状（`<工作区>/.yorozuya/`）：

    .yorozuya/
    ├── README.md                     ← 这个目录是什么、能不能删、git 里怎么忽略
    └── runs/
        └── 20260916-143015-6dac6bd47a6b/
            ├── README.md             ← 「这次干了什么」人话摘要（先看这个）
            ├── REPORT.md             ← 完整报告（工具调用表 + 事件流）
            ├── REPORT.json           ← 机器可读（含审批记录，便于事后审计）
            ├── changes.patch         ← 改动前 → 现在的统一 diff
            ├── verify.log            ← 验证命令、输出、结论
            └── files.txt             ← 改动清单（新建 / 修改 / 已移走）

三条纪律：
· **跑完之后才写**（内核线程结束时），所以它不会被算进「这次改动的文件」，
  也不会污染验证闸门（`_touched` 里没有它）。
· 目录名带**时间 + runId**：时间给人看、runId 给机器对账（回放、删除都靠它）。
· 幂等：同一次运行重复写只**覆盖**，不会再建一个文件夹；路径记在 `agent_runs.task_dir`，
  删除运行记录时据此精确清掉（见 `remove_task_folder` 的安全检查）。
"""
from __future__ import annotations

import difflib
import json
import time
from pathlib import Path

from . import common, report as report_mod, storage

ROOT_NAME = common.TASK_DIR_NAME          # .yorozuya
RUNS_SUBDIR = "runs"

ROOT_README = """# {root}/ ：工作台的任务留档

这个目录是**万事屋工作台**自动写的，不是你项目的一部分。

- `runs/<时间>-<运行号>/`：**每完成一次委托就有一份**，含这次的目标、改了哪些文件、
  验证结论、完整报告与可回放的 JSON。
- 想清理：**整个目录直接删掉**即可，删了不影响项目本身，也不影响已经提交的代码。
- 放进 `.gitignore` 更省心（下面这行加进你项目的 `.gitignore`）：

      {root}/

工作台「运行记录」里删除某次运行时，只会删掉它对应的那个子目录（不会碰这里别的东西）。
"""

RUN_README = """# 这次干了什么

- **目标**：{goal}
- **结果**：{status_text}（{status}）
- **工作区**：`{workspace}`
- **时间**：{started} → {ended}（耗时 {seconds}s）
- **规模**：{steps} 步 / {tool_calls} 次工具调用 / token {tokens_in}+{tokens_out}

## 改了哪些文件

{files}

## 有没有被证明「没弄坏」

{verify}

## 这个文件夹里有什么

| 文件 | 内容 |
|---|---|
| `README.md` | 就是这份人话摘要 |
| `REPORT.md` | 完整报告：每一步调了什么工具、参数、裁决、结果 |
| `REPORT.json` | 机器可读的全量数据（事件流 + 审批记录），便于事后审计 |
| `changes.patch` | 改动前 → 现在的统一 diff（可以 `git apply` 回看，也能直接看） |
| `verify.log` | 验证命令、原始输出与结论 |
| `files.txt` | 改动清单（纯文本，方便 `grep`） |

> 这些是**那一刻的留档**：之后你又改过文件的话，`changes.patch` 与工作区现状会不一致。
> 想撤销这次改动，用工作台「回滚这次改动」（快照还在，与这个文件夹无关）。
"""


def runs_root(ws) -> Path:
    return Path(ws.root) / ROOT_NAME / RUNS_SUBDIR


def _folder_name(ws, run_id: str, when_ms=None) -> str:
    """时间 + runId：时间给人看，runId 给机器对账（回放 / 删除都靠它）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime((when_ms or time.time() * 1000) / 1000))
    return f"{stamp}-{run_id}"


def _find_existing(ws, run_id: str) -> Path | None:
    """同一次运行已经写过就复用它（避免重复运行/重试时建出两个文件夹）。"""
    try:
        for d in sorted(runs_root(ws).glob(f"*-{run_id}")):
            if d.is_dir():
                return d
    except Exception:
        pass
    return None


def _rel(ws, p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(Path(ws.root).resolve()).as_posix()
    except Exception:
        return str(p)


def _status_text(status: str) -> str:
    return {"done": "完成（并且验证通过）",
            "unverified": "做完了，但**没有东西能证明它没弄坏**",
            "verify_failed": "改动做完了，**验证没通过**",
            "stopped": "被你中途停掉",
            "blocked": "卡住了（审批一直没人回应，已自动停下）",
            "budget": "触及预算上限（步数 / 时长）",
            "error": "出错了"}.get(status, status or "未知")


def _unified(before: str, after: str, rel: str) -> str:
    if before == after:
        return ""
    return "\n".join(list(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile="a/" + rel, tofile="b/" + rel,
        lineterm="", n=3))[:600])


def collect_changes(ws, run_id: str, conn, snaps=None) -> dict:
    """改动清单 + patch 文本。旧内容从**快照 blob** 里取（内容寻址，一定有）。"""
    cks = []
    try:
        cks = storage.list_checkpoints(conn, run_id)
    except Exception:
        pass
    old: dict[str, dict] = {}
    for ck in cks:                                  # 倒序看：最早的检查点才是"改动前"
        try:
            files = json.loads(ck["files"] or "{}")
        except Exception:
            continue
        for rel, rec in files.items():
            old[rel] = rec                          # 后面的覆盖前面的 = 最终取最早那次

    rows, patch, created, modified, gone = [], [], [], [], []
    for rel in sorted(old):
        rec = old[rel]
        p = Path(ws.root) / rel
        was = ""
        if rec.get("existed") and snaps is not None and rec.get("hash"):
            try:
                was = snaps.blob_path(rec["hash"]).read_text("utf-8", "replace")
            except Exception:
                was = ""
        now = ""
        if p.exists() and p.is_file():
            try:
                now = p.read_text("utf-8", errors="replace")
            except Exception:
                now = ""
        if not rec.get("existed"):
            created.append(rel)
            rows.append(f"- `{rel}`（本次新建）")
            patch.append(_unified("", now, rel))
        elif not p.exists():
            gone.append(rel)
            rows.append(f"- `{rel}`（已被移走/删除）")
            patch.append(_unified(was, "", rel))
        elif was != now:
            modified.append(rel)
            adds = sum(1 for l in _unified(was, now, rel).splitlines()
                       if l.startswith("+") and not l.startswith("+++"))
            dels = sum(1 for l in _unified(was, now, rel).splitlines()
                       if l.startswith("-") and not l.startswith("---"))
            rows.append(f"- `{rel}`（修改 +{adds} -{dels}）")
            patch.append(_unified(was, now, rel))
        elif p.exists():
            rows.append(f"- `{rel}`（内容与改动前一致）")
    return {"rows": rows, "patch": "\n".join(x for x in patch if x),
            "created": created, "modified": modified, "gone": gone}


def write_task_folder(conn, run, ws, snaps=None, when_ms=None) -> dict:
    """把一次运行打包成文件夹。返回 {ok, dir, rel, files}（失败也**不抛**，交给调用方提示）。"""
    run_id = str(run.get("id") or "")
    if not run_id:
        return {"ok": False, "error": "run 没有 id"}
    try:
        folder = _find_existing(ws, run_id) or (runs_root(ws) / _folder_name(ws, run_id, when_ms))
        folder.mkdir(parents=True, exist_ok=True)
        _write_root_readme(ws)

        changes = collect_changes(ws, run_id, conn, snaps)
        files_list = "\n".join(changes["rows"]) or "- （这次没有改动任何文件）"
        v = run.get("verification") or {}
        if isinstance(v, str):
            try:
                v = json.loads(v or "{}")
            except Exception:
                v = {}
        if v.get("ran"):
            verdict = ("通过" if v.get("ok") else
                       ("未通过（但改动前它本来就是红的 → 不是你这次弄坏的）"
                        if v.get("baseline_failed") else "**未通过**"))
            verify_md = (f"- 命令：`{v.get('cmd')}`\n- 改动前基线：{'通过' if v.get('baseline_ok') else '未通过'}\n"
                         f"- 结论：**{verdict}**")
        else:
            verify_md = ("- 这次**没有跑验证命令**：" + str(v.get("note") or "这个工作区没配验证命令") + "\n"
                         "- 也就是说：「做完了」不等于「证明没弄坏」。想让它能自证，"
                         "在工作台「工作区」里给这个项目配一条验证命令。")
        (folder / "README.md").write_text(common.redact(RUN_README.format(
            goal=run.get("goal") or "", status=run.get("status") or "",
            status_text=_status_text(str(run.get("status") or "")),
            workspace=run.get("root_path") or ws.root,
            started=_fmt(run.get("created_at")), ended=_fmt(run.get("ended_at")),
            seconds=run.get("seconds") or 0, steps=run.get("steps") or 0,
            tool_calls=run.get("tool_calls") or 0,
            tokens_in=run.get("tokens_in") or 0, tokens_out=run.get("tokens_out") or 0,
            files=files_list, verify=verify_md)), encoding="utf-8")

        # 完整报告：直接复用现有的 report 模块（同一份口径，不另写一套）
        data = report_mod.build_report(conn, run_id)
        (folder / "REPORT.md").write_text(common.redact(report_mod.to_markdown(data)), encoding="utf-8")
        (folder / "REPORT.json").write_text(
            common.redact(json.dumps(data, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
        (folder / "changes.patch").write_text(common.redact(changes["patch"] or
                                                           "（这次没有产生文件改动）\n"), encoding="utf-8")
        (folder / "verify.log").write_text(common.redact(
            _verify_log(run, v)), encoding="utf-8")
        (folder / "files.txt").write_text(
            "\n".join([f"新建: {x}" for x in changes["created"]] +
                      [f"修改: {x}" for x in changes["modified"]] +
                      [f"移走/删除: {x}" for x in changes["gone"]]) or "（无改动）",
            encoding="utf-8")
        rel = _rel(ws, folder)
        try:
            storage.set_task_dir(conn, run_id, rel)
        except Exception:
            pass
        return {"ok": True, "dir": str(folder), "rel": rel,
                "files": sorted(p.name for p in folder.iterdir())}
    except Exception as e:                                          # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _verify_log(run, v: dict) -> str:
    lines = [f"# 验证留档 · {run.get('id')}", "",
             f"- 状态：{run.get('status')}",
             f"- 时间：{_fmt(run.get('created_at'))} → {_fmt(run.get('ended_at'))}"]
    if v.get("ran"):
        lines += [f"- 命令：{v.get('cmd')}",
                  f"- 改动前基线：{'通过' if v.get('baseline_ok') else '未通过'}",
                  f"- 第 {v.get('round')} 轮结论：{'通过' if v.get('ok') else '未通过'}",
                  "", "## 原始输出", "", "```", str(v.get("output") or "")[-4000:], "```"]
    else:
        lines += ["- 没有自动验证：" + str(v.get("note") or "未配置验证命令")]
    return "\n".join(lines) + "\n"


def _write_root_readme(ws) -> None:
    p = Path(ws.root) / ROOT_NAME / "README.md"
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ROOT_README.format(root=ROOT_NAME), encoding="utf-8")


def remove_task_folder(ws, rel: str) -> tuple:
    """删掉某次运行的留档目录（删除运行记录时用）。返回 **(ok, 原因)**。

    ★ 安全检查：只允许删 `<工作区>/.yorozuya/runs/` 下的一个子目录 ——
      传进来的路径不对就**什么都不做**（宁可删不掉，也不能删错东西）。

    ⚠️ 一定要把失败原因带出来：第一版写成"失败就返回 False"，结果界面上只看到
      "删了记录但文件夹还在"，完全不知道卡在哪（真实教训，与那个"函数静默返回 None"同类）。
    """
    try:
        base = runs_root(ws).resolve()
        target = (Path(ws.root) / str(rel or "")).resolve()
        if not rel:
            return False, "这次运行没有留档目录"
        if not str(target).startswith(str(base)):
            return False, f"拒绝：{rel} 不在 {base} 之下（安全起见不动）"
        if target == base:
            return False, "拒绝：不能删 runs 目录本身"
        if not target.is_dir():
            return False, "目录已经不在了（可能已被清理）"
        # ★ 一律**移入回收站**，不做真删除（与项目既定约定一致：删除一律进 trash）：
        #   ① 她随时能捞回来 ② 绕开"受限环境拦截真删除"这一类问题
        #   （实测踩到：沙箱里 shutil.rmtree 直接抛 SAFE_DELETE_FAIL_CLOSED，
        #    界面上表现为"记录删了、文件夹还在"，而失败原因是静默的 —— 所以现在一律"移"。）
        from .snapshot import move_to_trash      # 同级模块（曾写成 ..snapshot，直接 ModuleNotFoundError）
        dst = move_to_trash(target)
        if dst is None:
            return False, "移入回收站失败（未做真删除，目录还在原处）"
        # 空掉就把 runs/ 也收掉（留着空的也无所谓，顺手好看点）
        try:
            if base.is_dir() and not any(base.iterdir()):
                base.rmdir()
        except Exception:
            pass
        return True, f"已移入回收站：{dst}"
    except Exception as e:                                          # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _fmt(ms) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
