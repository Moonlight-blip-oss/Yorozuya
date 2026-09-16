# -*- mode: python ; coding: utf-8 -*-
# Yorozuya 打包配置：单文件、无控制台窗口
# ★ 产物落在**项目根**（不再用 dist/）→ 打包时带上 --distpath .（见 build/verify/pack_exe.bat）
from PyInstaller.utils.hooks import collect_all

datas = [("renderer", "renderer"), ("icon/Yorozuya.ico", "icon")]
binaries = []
hiddenimports = []

# 这些包有动态导入/数据文件/原生 DLL，需整体收集
for pkg in ("pywebview", "clr_loader", "pythonnet", "uvicorn", "fastapi",
            "pymysql", "httpx", "httpcore", "anyio", "h11", "yorozuya"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# 去重
seen = set()
def _uniq(items):
    out = []
    for it in items:
        key = it[0] if isinstance(it, tuple) else it
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
datas = _uniq(datas)
binaries = _uniq(binaries)
hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    ["app/desktop.py"],
    pathex=[".", "app"],          # "." = 项目根（yorozuya 包 / renderer），"app" = server.py 与 db.py
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Yorozuya",
    icon="icon/Yorozuya.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # 不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
