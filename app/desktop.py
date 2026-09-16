# -*- coding: utf-8 -*-
"""Yorozuya · 桌面壳（pywebview 包 FastAPI 后端）

启动后先显示启动画面，等本地服务真正就绪再把窗口切到应用页面，
避免"服务还没起来 → WebView 报 127.0.0.1 拒绝连接"的竞态。

开发期双击「启动 Yorozuya.bat」，发布后用 PyInstaller 打包成 Yorozuya.exe。
"""
import io
import socket
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from pathlib import Path

# ★ 本文件在 app/ 子目录里，但 renderer/、icon/、db_config.json 都在**项目根**：
#   · 先把项目根挂到 sys.path —— `import db` / `from server import app` / `import yorozuya…` 都靠它；
#   · 再把项目根记成 _ROOT —— 只用来找**源码运行时的资源**（renderer/、icon/）；
#   · ★ 要**落盘的数据**一律问 yorozuya/paths.py（唯一数据根 appdata/），
#     打包版和源码版共用同一份，不再各写各的目录。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from yorozuya import paths        # noqa: E402  （路径锚点：appdata/ 唯一数据根）

HOST = "127.0.0.1"
MIN_SPLASH = 2.5      # 开场动画的表演时长（秒）：随机一位人格登场，正好排满 2.5s
PORT_CANDIDATES = (8902, 8904, 8905, 8906)
IS_FROZEN = getattr(sys, "frozen", False)

# 打包后没有控制台，一旦卡住完全看不到原因 → 每一步都写日志文件（统一在 appdata/ 下）
LOG_FILE = paths.log_file()
LOG_MAX = 400 * 1024          # 日志别无限涨
_FALLBACK_LOG = Path(tempfile.gettempdir()) / "yorozuya-start.log"
_REAL_STDOUT = sys.stdout     # 双击启动时可能是 None，先记下来


def _append_log(msg):
    """写日志；目标目录不可写（比如放进 Program Files）时退到临时目录，别把唯一的线索弄丢。"""
    for path in (LOG_FILE, _FALLBACK_LOG):
        try:
            if path.exists() and path.stat().st_size > LOG_MAX:
                path.write_text("%s （已达上限，重新计数）\n" % time.strftime("%H:%M:%S"), "utf-8")
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
            return
        except Exception:
            continue


def log(msg):
    """启动日志：没控制台也要留痕，卡住时能一眼看到卡在哪一步。"""
    _append_log(msg)
    if _REAL_STDOUT is not None:
        try:
            print(msg, flush=True)
        except Exception:
            pass


class _LogStream(io.TextIOBase):
    """无控制台（console=False / 双击启动）时 sys.stdout、sys.stderr 是 None，
    而 uvicorn 的 Config 初始化会调用 sys.stdout.isatty() → 直接崩溃。

    这里给标准输出/错误装一个替身：所有内容写进启动日志，并提供 isatty / encoding
    等属性，让依赖这些的库（uvicorn、click、logging）正常工作。
    """

    def __init__(self, tag):
        self.tag = tag
        self.name = "<%s>" % tag

    # 注意：io.TextIOBase 的 encoding / errors 是只读属性，
    # 在 __init__ 里赋值会直接抛 AttributeError（整个模块都导入不了），只能用属性覆盖。
    @property
    def encoding(self):
        return "utf-8"

    @property
    def errors(self):
        return "replace"

    def write(self, s):
        text = s if isinstance(s, str) else s.decode("utf-8", "replace")
        text = text.rstrip("\r\n")
        if text:
            _append_log("[%s] %s" % (self.tag, text[:2000]))
        return len(s)

    # 极少数库会写 sys.stdout.buffer（字节流），也给它一个落点
    @property
    def buffer(self):
        return self

    def write_bytes(self, data):
        self.write(data.decode("utf-8", "replace"))
        return len(data)

    def flush(self):
        pass

    def isatty(self):
        return False

    def writable(self):
        return True

    def close(self):
        pass


def install_std_streams():
    """把缺失的标准流补上——必须在 import uvicorn / server 之前调用。"""
    fixed = []
    if sys.stdout is None:
        sys.stdout = _LogStream("stdout")
        fixed.append("stdout")
    if sys.stderr is None:
        sys.stderr = _LogStream("stderr")
        fixed.append("stderr")
    if sys.stdin is None:
        sys.stdin = io.StringIO("")
        fixed.append("stdin")
    if fixed:
        _append_log("检测到无控制台，已为 %s 装上替身流" % "/".join(fixed))
    return fixed


install_std_streams()

SPLASH_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
/* 配色全部来自「上次使用的人格」主题（见 splash_html()），与进入应用后一致 */
body{display:flex;align-items:center;justify-content:center;
  background:__SP_BG__;
  color:__SP_INK__;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  user-select:none;-webkit-user-select:none;transition:background .45s ease}
body.leaving{background:__SP_BGFLAT__}
.stage{display:flex;flex-direction:column;align-items:center;gap:16px;
  transition:transform .42s ease,opacity .42s ease}
body.leaving .stage{transform:scale(1.06);opacity:0}

/* ① 随机一位人格登场：徽圈双描 → 头像弹出 → 光泽扫过 → 名字浮起 → 光环呼吸
     每次开机随机抽一位（--d 弹出 / --r 描圈 / --s 扫光 / --c 名字 / --g 呼吸，
     时间点由 Python 侧算好后写进来）。
     整条动画排满 2.5s：0.12→1.08s 依次落定，扫光到 2.20s 收尾，留 0.3s 余韵 */
.hero{display:flex;flex-direction:column;align-items:center;gap:12px}
.hero .ringwrap{position:relative;width:132px;height:132px}
.hero svg{position:absolute;inset:-10px;width:calc(100% + 20px);height:calc(100% + 20px);overflow:visible}
.hero .ring{fill:none;stroke:__SP_INK__;stroke-width:2.8;stroke-linecap:round;opacity:.78;
  stroke-dasharray:660;stroke-dashoffset:660;
  animation:draw 1s cubic-bezier(.55,.06,.25,1) var(--r) forwards}
.hero .ring2{fill:none;stroke:__SP_ACCENT__;stroke-width:1.4;stroke-linecap:round;opacity:.5;
  stroke-dasharray:610;stroke-dashoffset:610;
  animation:draw 1s cubic-bezier(.55,.06,.25,1) calc(var(--r) + .16s) forwards}
@keyframes draw{to{stroke-dashoffset:0}}
.hero .tile{position:absolute;inset:12px;border-radius:30px;overflow:hidden;
  background:__SP_ACCENT_GRAD__;box-shadow:0 14px 30px __SP_GLOW__;
  display:flex;align-items:center;justify-content:center;
  color:__SP_ON_ACCENT__;font-size:44px;font-weight:700;
  opacity:0;transform:scale(.74) rotate(-8deg);
  animation:pop .78s cubic-bezier(.16,1.42,.42,1) var(--d) forwards,
            breathe 2.6s ease-in-out var(--g) infinite alternate}
.hero .tile img{width:100%;height:100%;object-fit:cover;display:block}
@keyframes pop{to{opacity:1;transform:scale(1) rotate(0)}}
@keyframes breathe{
  from{box-shadow:0 14px 30px __SP_GLOW__}
  to{box-shadow:0 18px 40px __SP_GLOW__,0 0 0 6px __SP_SOFT__}}
.hero .tile::after{content:"";position:absolute;top:-70%;left:-80%;width:52%;height:240%;
  transform:rotate(18deg);pointer-events:none;
  background:linear-gradient(90deg,rgba(255,255,255,0),rgba(255,255,255,.5),rgba(255,255,255,0));
  animation:sweep 1.05s cubic-bezier(.4,.1,.5,1) var(--s) forwards}
@keyframes sweep{to{left:135%}}
.hero .cap{font-size:13px;font-weight:600;letter-spacing:.1em;color:__SP_ACCENT_DEEP__;
  opacity:0;transform:translateY(7px);
  animation:up .5s cubic-bezier(.2,.9,.3,1) var(--c) forwards}

/* ② 文案依次上浮（接在头像收尾之后，整体 2.5s 内落定） */
h1{font-size:20px;font-weight:700;letter-spacing:.05em;opacity:0;transform:translateY(9px);
  animation:up .55s cubic-bezier(.2,.9,.3,1) __SP_H1__ forwards}
.sub{font-size:12.5px;color:__SP_INK3__;opacity:0;transform:translateY(9px);
  animation:up .55s cubic-bezier(.2,.9,.3,1) __SP_SUB__ forwards}
body.ready .sub{color:__SP_ACCENT_DEEP__}
@keyframes up{to{opacity:1;transform:none}}

/* ③ 进度 */
.bar{margin-top:4px;width:214px;height:3px;border-radius:999px;background:__SP_LINE__;overflow:hidden}
.bar i{display:block;height:100%;width:0%;border-radius:999px;
  background:__SP_ACCENT_GRAD__;transition:width .45s ease}
.pct{margin-top:-8px;font-size:10.5px;color:__SP_INK3__;letter-spacing:.08em;opacity:0;
  animation:up .5s ease __SP_PCT__ forwards}

/* ④ 兜底：重试 / 提示 */
#retry{margin-top:18px;padding:8px 22px;border:1px solid __SP_LINE__;background:__SP_PANEL__;
  border-radius:999px;font:13px inherit;color:__SP_ACCENT_DEEP__;cursor:pointer;
  opacity:0;pointer-events:none;transition:opacity .4s}
#retry.show{opacity:1;pointer-events:auto}
#tip{margin-top:10px;font-size:11px;color:__SP_INK3__;opacity:0;transition:opacity .4s}
#tip.show{opacity:1}
</style></head><body>
<div class="stage">
  <div class="hero">__SP_HERO__</div>
  <h1>Yorozuya · 万事屋</h1>
  <p class="sub" id="sub">__SP_SUB_INIT__</p>
  <div class="bar"><i id="fill"></i></div>
  <div class="pct" id="pct">0%</div>
  <button id="retry">重新连接</button>
  <div id="tip">也可以用浏览器打开 127.0.0.1:__SP_PORT__</div>
</div>
<script>
var fill = document.getElementById('fill');
var pct  = document.getElementById('pct');
var sub  = document.getElementById('sub');
var v = 0, done = false;
function tick(){
  if (done) return;
  v += (92 - v) * 0.006;                    /* 渐进逼近，永远不会自己跑到 100 */
  fill.style.width = v.toFixed(1) + '%';
  pct.textContent  = Math.min(99, Math.round(v)) + '%';
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

/* 服务就绪 → 补满进度 → 整页淡出（Python 之后才 load_url） */
window.__setReady = function(){
  if (done) return;
  done = true;
  fill.style.width = '100%';
  pct.textContent  = '100%';
  sub.textContent  = __SP_SUB_READY_JS__;
  document.body.classList.add('ready');
  setTimeout(function(){ document.body.classList.add('leaving'); }, 200);
};

setTimeout(function(){
  document.getElementById('retry').classList.add('show');
  document.getElementById('tip').classList.add('show');
}, 9000);

document.getElementById('retry').onclick = function(){
  this.textContent = '正在重试…';
  done = false; v = 8; requestAnimationFrame(tick);
  pywebview.api.retry();
};
</script>
</body></html>"""


# ---------------- 启动画面：每次开机随机抽一位人格登场 ----------------
# 启动瞬间数据库/服务都还没就绪，没法查库 → 头像直接以 base64 内联，不依赖服务。
# 四个 id 是"抽签池"，名字/配色一律从 personas.py 取，这里不重复定义。
CREW = ("gintoki", "kagura", "shinpachi", "tama")
PERSONA_CACHE = "last_persona.json"


def last_persona_id():
    """读上次使用的人格 id（现在只写进日志，画面本身改成随机抽一位）。"""
    try:
        import json
        f = paths.persona_file()
        pid = (json.loads(f.read_text("utf-8")) or {}).get("personaId") or ""
        return pid
    except Exception:
        return ""


def _avatar_b64(pid):
    """角色头像 → base64（缺图返回空串，调用方会退回文字徽章）。"""
    import base64
    try:
        f = base_dir() / "renderer" / "assets" / "avatar" / (pid + "_avatar.png")
        return base64.b64encode(f.read_bytes()).decode("ascii")
    except Exception:
        return ""


# ---- 登场时间轴（秒）：整条表演排满 MIN_SPLASH，改时长要连着这里一起改 ----
# ① 0.12 双描圈（各 1.0s）→ ② 0.30 头像弹出（0.78s）→ ③ 0.70 名字浮起（0.5s）
# ④ 1.15 光泽扫过（1.05s）→ ⑤ 1.00/1.18/1.35 标题·副标题·进度依次上浮
# ⑥ 1.30 起光环呼吸（无限，一直延续到切页）
# 收尾时刻 = 1.15 + 1.05 = 2.20s，与 MIN_SPLASH=2.5s 之间留 0.3s 静止余韵。
HERO_RING = 0.12
HERO_TILE = 0.30
HERO_CAP = 0.70
HERO_H1 = 1.00
HERO_SUB = 1.18
HERO_PCT = 1.35
HERO_SWEEP = 1.15
HERO_BREATHE = 1.30


def splash_html(port, pid=None):
    """随机一位人格登场的启动画面。返回 (html, 窗口底色, 说明)。

    · 每次开机从四个人格里**随机抽一位**登场（不再是四人依次亮相）
    · 配色取**同一位**人格的主题 —— 头像 / 名字 / 底色 / 点缀色永远是一套，
      不会出现"小玉的头像配银时的银灰底"这种错配
    · 整条动画 0.12→2.20s 落定，与 MIN_SPLASH=2.5s 对齐
    头像以 base64 内联：启动画面不能依赖还没起来的本地服务；
    任何一步失败都退回中文首字徽章，绝不让启动画面开天窗。
    """
    import json
    import random
    from yorozuya.personas import THEMES, PERSONAS, DEFAULT_PERSONA_ID

    pids = [p for p in CREW if p in PERSONAS] or [DEFAULT_PERSONA_ID]
    if pid not in pids:                        # 默认随机抽一位；传了合法的 pid 就按指定的来
        pid = random.choice(pids)              # ★ 随机抽一位
    t = THEMES.get(pid) or THEMES.get(DEFAULT_PERSONA_ID) or {}
    name = (PERSONAS.get(pid) or {}).get("name") or pid

    b64 = _avatar_b64(pid)
    inner = (('<img src="data:image/png;base64,' + b64 + '" alt="">') if b64
             else (name[:1] or "屋"))
    hero = (
        '<div class="slot" style="--r:{r:.2f}s;--d:{d:.2f}s;--c:{c:.2f}s;'
        '--s:{s:.2f}s;--g:{g:.2f}s">'
        '<div class="ringwrap">'
        '<svg viewBox="0 0 172 172">'
        '<circle class="ring" cx="86" cy="86" r="82"></circle>'
        '<circle class="ring2" cx="86" cy="86" r="74"></circle>'
        '</svg>'
        '<div class="tile">{inner}</div></div>'
        '<span class="cap">{name}</span></div>'.format(
            r=HERO_RING, d=HERO_TILE, c=HERO_CAP, s=HERO_SWEEP, g=HERO_BREATHE,
            inner=inner, name=name))

    html = (SPLASH_HTML
            .replace("__SP_BG__", t.get("gradient") or "#faf7f2")
            .replace("__SP_BGFLAT__", t.get("bg") or "#faf7f2")
            .replace("__SP_PANEL__", t.get("panel") or "#ffffff")
            .replace("__SP_INK__", t.get("ink") or "#3a322c")
            .replace("__SP_INK3__", t.get("ink3") or "#8a7f74")
            .replace("__SP_LINE__", t.get("line") or "#e8dfd0")
            .replace("__SP_ACCENT__", t.get("accent") or "#c2603a")
            .replace("__SP_ACCENT_DEEP__", t.get("accentDeep") or "#8a5a30")
            .replace("__SP_ACCENT_GRAD__", t.get("accentGrad") or "linear-gradient(135deg,#e8833a,#c2603a)")
            .replace("__SP_ON_ACCENT__", t.get("onAccent") or "#ffffff")
            .replace("__SP_GLOW__", t.get("glow") or "rgba(200,110,60,.30)")
            .replace("__SP_SOFT__", t.get("soft") or "rgba(200,110,60,.16)")
            .replace("__SP_H1__", "%.2fs" % HERO_H1)
            .replace("__SP_SUB__", "%.2fs" % HERO_SUB)
            .replace("__SP_PCT__", "%.2fs" % HERO_PCT)
            .replace("__SP_HERO__", hero)
            .replace("__SP_SUB_INIT__", name + " 正在开门，请稍候…")
            .replace("__SP_SUB_READY_JS__", json.dumps(name + " 已经开门，欢迎回来", ensure_ascii=False))
            .replace("__SP_PORT__", str(port)))
    info = "随机到 %s（%s）" % (name, pid)
    return html, (t.get("bg") or "#faf7f2"), info



def base_dir():
    """打包后资源在 _MEIPASS，源码运行在**项目根**（renderer/ 与 icon/ 都在那儿）。"""
    if IS_FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _ROOT


def app_dir():
    """落盘数据目录 —— 就是 `paths.data_dir()`（唯一的 `appdata/`）。

    ★ 以前这里返回"exe 同目录 / 项目根"，于是打包版把数据写进 `dist\\`、
      源码版写进项目根，同一个东西出现两份（登录状态也跟着分裂）。
      现在无论怎么启动都指向同一处：<项目根>\\appdata
    """
    return paths.data_dir()


def pick_port():
    """挑一个空闲端口，避免与已运行的服务冲突。"""
    for port in PORT_CANDIDATES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((HOST, port)) != 0:
                return port
    raise RuntimeError("8902/8904-8906 都被占用，请先关掉旧的 Yorozuya 进程")


def wait_for_server(port, timeout=60.0):
    """轮询到本地服务真的能连上为止（打包后首次启动要解包，可能偏慢）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex((HOST, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def start_kwargs(webview_mod):
    """启动参数：窗口图标 + ★ 持久化（这是「每次打开都要重新登录」的根因所在）。

    · pywebview 的 `private_mode` **默认是 True**：WebView2 跑在内存档里，
      退出即清空 —— localStorage 里的登录 token 一并没了，所以每次都得重新登录。
      改成 False（并用固定的 storage_path）后，登录状态才会真的留在磁盘上。
    · 不同版本支持情况不一，这里按实际签名判断，拿不到就安静地用默认行为。
    """
    kw = {}
    try:
        import inspect
        params = inspect.signature(webview_mod.start).parameters
        icon = base_dir() / "icon" / "Yorozuya.ico"
        if "icon" in params and icon.exists():
            kw["icon"] = str(icon)
        if "private_mode" in params:
            kw["private_mode"] = False
        if "storage_path" in params:
            # 唯一数据根下的 webview-data：换包、换启动方式都不动它，登录状态才能一直保留
            sp = paths.webview_dir()
            try:
                sp.mkdir(parents=True, exist_ok=True)
                kw["storage_path"] = str(sp)
            except Exception:
                pass
    except Exception:
        pass
    return kw


def error_html(detail):
    d = (detail or "").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:#faf7f2;color:#3a322c;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;padding:32px;text-align:center}}
h1{{font-size:19px;margin:0 0 8px}}p{{font-size:13px;color:#8a7f74;margin:4px 0;max-width:520px;line-height:1.7}}
pre{{margin-top:14px;padding:10px 14px;background:#f1ebe0;border-radius:10px;font-size:11.5px;color:#7a6b60;
  max-width:640px;overflow:auto;text-align:left}}
</style></head><body>
<h1>未能启动本地服务</h1>
<p>Yorozuya 需要在本机起一个本地服务。请检查：</p>
<p>① 8902 / 8904-8906 端口是否被其它程序占用<br>
   ② 数据库配置 <b>db_config.json</b> 是否在项目根（与 app/ 同级）<br>
   ③ 本机 MySQL / MariaDB 是否已启动</p>
<pre>{d}</pre>
</body></html>"""


# ★ 当前窗口放在**模块级**变量里，绝不挂到 js_api 对象上。
#   原因（实测踩到，代价是"新版打开就未响应"）：pywebview 生成 JS 桥时会遍历 js_api 对象的
#   公开属性（webview/util.py 的 get_functions）：
#     可调用（方法/函数）→ 暴露成函数；**不可调用但带 __module__ → 递归展开**。
#   而 Window 实例的 `__module__` 是从类继承的（hasattr 为真），于是桥会去 dir(Window)，
#   里面含 `native`（.NET/WinForms 后端）等一堆东西 —— 这段又是在 window._expose_lock 里跑的，
#   结果注入脚本一直跑不完 → 页面拿不到 pywebview 桥 → 窗口"未响应"。
_WINDOW = None


class AppApi:
    """暴露给页面的原生能力（pywebview 会把它挂成 window.pywebview.api）。

    · retry()        —— 启动画面上的「重试」（真卡住时给用户一个出口）
    · pick_folder()  —— 登记工作区时的**原生目录选择器**：网页自己拿不到绝对路径，
                        必须由宿主进程弹系统对话框（资源管理器那种体验）。

    ⚠️ 这个类里**只允许放两类东西**：公开方法、以及**下划线开头**的私有属性
    （pywebview 会跳过下划线开头的名字）。任何公开的非可调用属性都会被递归展开 ——
    想放窗口/后端句柄这类对象，一律放模块级变量或私有属性。
    """

    def __init__(self):
        self._on_retry = None       # 下划线开头：pywebview 的桥会跳过

    def retry(self):
        cb = self._on_retry
        if cb is None:
            return False
        threading.Thread(target=cb, daemon=True).start()
        return True

    def pick_folder(self, initial: str = ""):
        """弹原生「选择文件夹」对话框，返回选中的绝对路径（取消 → 空字符串）。

        为什么返回值用**空字符串**而不是 None：pywebview 把返回值 JSON 序列化给页面，
        None 会变成 null，前端还得再判一次；空字符串前端一个 `if (picked)` 就够了。

        为什么整个包一层 try：目录选择器依赖宿主 GUI（WinForms/WKWebView…），
        在某些环境下会抛（例如没有桌面会话）。这时候**如实报错给前端**，
        让它提示用户改用「手输路径」，而不是静默什么都不发生。
        """
        win = _WINDOW
        if win is None:                       # 兜底：pywebview 自己维护的窗口列表
            try:
                import webview
                win = webview.windows[0] if getattr(webview, "windows", None) else None
            except Exception:
                win = None
        if win is None:
            return ""
        try:
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if kind is None:                       # 老版 pywebview
                kind = webview.FOLDER_DIALOG
            picked = win.create_file_dialog(kind, directory=initial or "")
        except Exception:
            log("pick_folder 失败：\n%s" % traceback.format_exc())
            raise
        if not picked:
            return ""
        # 不同后端返回的可能是 str / list / tuple，统一取第一个
        if isinstance(picked, (list, tuple)):
            return str(picked[0]) if picked else ""
        return str(picked)


def main():
    log("===== Yorozuya 启动 (frozen=%s) =====" % IS_FROZEN)
    paths.ensure_dirs()                     # 唯一数据根 appdata/（含 webview-data、zhiban-data）
    log(paths.describe())
    try:
        import uvicorn
        from server import app
        log("依赖导入完成")
    except ImportError as e:
        log("缺少依赖：%s" % e)
        print("请先运行：pip install -r requirements.txt")
        sys.exit(1)
    except Exception:
        log("导入 server 失败：\n%s" % traceback.format_exc())
        raise

    try:
        port = pick_port()
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    url = f"http://{HOST}:{port}"
    boot_error = {"detail": ""}
    t0 = time.time()

    def run_server():
        try:
            log("uvicorn 线程启动：%s" % url)
            uvicorn.run(app, host=HOST, port=port, log_level="warning")
            log("uvicorn 线程退出（服务结束了）")
        except Exception:
            boot_error["detail"] = traceback.format_exc()
            log("uvicorn 异常：\n%s" % boot_error["detail"])

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    try:
        import webview  # pywebview
    except ImportError:
        print("未安装 pywebview，改用默认浏览器打开。")
        if wait_for_server(port):
            webbrowser.open(url)
        t.join()
        return

    # 先给一个启动画面，等服务真的能连上再切过去
    # 启动画面 = 四个人格里随机抽一位登场（配色取同一位的主题，每次开机观感不同）
    splash, splash_bg, splash_info = splash_html(port)
    log("启动画面：%s（上次使用的人格：%s）" % (splash_info, last_persona_id() or "无"))
    api = AppApi()
    win = webview.create_window(
        "Yorozuya · 万事屋",
        html=splash,
        js_api=api,
        width=1080, height=760,
        min_size=(860, 600),
        background_color=splash_bg,
    )
    # ★ 只记到模块级变量：**不要**写 `api.window = win` —— Window 是"不可调用的公开属性"，
    #   pywebview 生成 JS 桥时会递归展开它（见 _WINDOW 上面的注释），启动就会卡成"未响应"。
    global _WINDOW
    _WINDOW = win

    def try_switch():
        """切到应用页面；成功返回 True。"""
        if not wait_for_server(port, timeout=45.0):
            log("服务在预期时间内未就绪")
            return False
        log("服务已就绪：%s（等 %.1fs）" % (url, time.time() - t0))
        for attempt in range(3):
            # 服务常常 1 秒就就绪，给开场动画留一点表演时间（只在首次尝试时等）
            if attempt == 0:
                wait = MIN_SPLASH - (time.time() - t0)
                if wait > 0:
                    time.sleep(wait)
            try:
                # 先让开场动画走完（进度补满 → 整页淡出），再切到应用页
                try:
                    win.evaluate_js("if (window.__setReady) window.__setReady()")
                    time.sleep(0.55)
                except Exception:
                    pass
                win.load_url(url)
                log("load_url 成功（第 %d 次）" % (attempt + 1))
                # 自检：真的跳过去了吗？（窗口标题未必随页面变，这里直接问页面）
                for _ in range(10):
                    time.sleep(1.0)
                    try:
                        info = win.evaluate_js("location.href + ' || ' + document.title")
                    except Exception:
                        info = None
                    if info:
                        log("页面自检：%s" % info)
                        break
                return True
            except Exception:
                log("load_url 第 %d 次失败：\n%s" % (attempt + 1, traceback.format_exc()))
                time.sleep(1.5)
        return False

    def fallback():
        log("回退：错误页 + 浏览器")
        try:
            win.load_html(error_html(boot_error["detail"] or "服务未能在预期时间内就绪"))
        except Exception:
            log("load_html 失败：\n%s" % traceback.format_exc())
        try:
            webbrowser.open(url)
        except Exception as exc:
            log("浏览器打不开：%s" % exc)

    def boot():
        if try_switch():
            return
        fallback()

    api._on_retry = boot         # 启动页上的「重试」按钮（私有名，不进 JS 桥）
    threading.Thread(target=boot, daemon=True).start()

    try:
        log("webview.start() 开始")
        webview.start(**start_kwargs(webview))
        log("webview.start() 返回（窗口已关闭）")
    except Exception:
        log("webview.start() 异常：\n%s" % traceback.format_exc())
        print("桌面窗口启动失败，已回退到浏览器：")
        traceback.print_exc()
        if wait_for_server(port):
            webbrowser.open(url)
        t.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        if not IS_FROZEN:
            input("出错了，按回车键关闭…")
