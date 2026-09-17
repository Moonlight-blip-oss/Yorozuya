/* ===== Yorozuya · 前端瘦客户端（核心逻辑在 Python 后端） ===== */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmtDT = (ts) => new Date(ts).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

/* 主题应用：CSS 变量交给 PersonaThemes（只改变量、不重绘结构），
   这里只更新必须换 DOM 内容的「角色标志物」（吉祥物 / 水印）。 */
const applyTheme = (theme) => {
  if (!theme) return;
  PersonaThemes.apply(theme);                       // ← 背景/主色/点缀色/边缘光/纹理/圆角
  const pid = theme.key || (S && S.settings && S.settings.personaId) || "gintoki";
  document.body.dataset.persona = pid;
  // 右下角小人由 FloatingMascot 组件按主题配置渲染，这里不再插手
  const wm = $("#chatWm");
  if (wm) wm.innerHTML = PERSONA_MARKS.mark(pid, 200);
};

/* 浅色 / 深色 / 跟随系统 / 跟随角色(auto)：同样交给主题提供者 */
function applyUiMode(mode) { PersonaThemes.applyMode(mode || "auto"); }

let S = null;          // /api/state 视图
let busy = false;
let todoFilter = "all";
let ME = null;         // 当前用户名
let IS_GUEST = false;  // 访客模式（免登录，无长期记忆）
const TOKEN_KEY = "zhiban-token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";

/* ---------- 鉴权 ---------- */
function authHeaders() { return TOKEN ? { Authorization: "Bearer " + TOKEN } : {}; }

function showGate(msg) {
  $("#loginGate").hidden = false;
  if (msg) $("#gateErr").textContent = msg;
  $("#gatePass").value = "";
  setTimeout(() => $("#gateUser").focus(), 50);
}
function hideGate() { $("#loginGate").hidden = true; $("#gateErr").textContent = ""; }

async function enterApp() {
  try {
    ME = await api("/api/auth/me");
    S = await api("/api/state");
  } catch (e) { return false; }
  const savedScroll = loadChatScroll();       // 上次离开聊天页时停在哪
  if (savedScroll) { chatStick = !!savedScroll.stick; chatSavedTop = savedScroll.top; }
  hideGate();
  IS_GUEST = !!ME.guest;
  $("#accountName").textContent = ME.username;
  applyGuestUI();
  applyTheme(S.settings.personaTheme);
  I18N.setLang(S.settings.lang || "zh-CN");   // 恢复上次选的语言（auto 则跟随浏览器）
  applyUiMode(S.settings.uiMode);
  updateAccountWidget();
  if (!enterApp._bound) { bindEvents(); enterApp._bound = true; }
  renderAll();
  restoreLastTab();            // 回到上次停留的页面与位置，而不是从头开始
  return true;
}

/* ---------- 访客模式界面 ---------- */
function applyGuestUI() {
  $("#guestBar").hidden = !IS_GUEST;
  $("#guestAccountHint").hidden = !IS_GUEST;
  $("#btnGuestUpgrade2").hidden = !IS_GUEST;
  $("#memoryGuestNote").hidden = !IS_GUEST;
  $("#memoryForm").hidden = IS_GUEST;          // 访客不能手动添加记忆
  $("#btnImport").hidden = IS_GUEST;           // 访客不支持导入
  $("#btnLogout").textContent = IS_GUEST ? "退出访客模式" : "退出登录";
  const sw = $("#setAutoExtract") ? $("#setAutoExtract").closest(".switch-row") : null;
  if (sw) sw.hidden = IS_GUEST;                // 访客没有记忆，开关无意义
  // 注意：副标题等「随人格变化」的文案不在这里设置 —— 见 renderPersonaChrome()，
  // 那里在每次 renderAll 时按 personaId 重新推导，避免只在启动时算一次导致状态失同步
}

/* 人格显示名：英文界面优先用后端的 nameEn，缺失则退回中文名。
   角色名与角色口中的台词属于「内容」而非「界面文案」，所以不放语言包，而是由后端数据提供英文。 */
function personaDisplayName(p) {
  if (!p) return "";
  return (I18N.isEnglish && p.nameEn) ? p.nameEn : (p.name || "");
}
/* 人格一句话身份（顶栏副标题）：同样优先取英文 */
function personaDisplayDesc(p) {
  if (!p) return "";
  return (I18N.isEnglish && p.descriptionEn) ? p.descriptionEn : (p.description || "");
}

/* ---------- 当前人格（唯一数据源：S.settings.personaId + S.personas） ---------- */
function currentPersona() {
  const id = (S && S.settings && S.settings.personaId) || "gintoki";
  return (((S && S.personas) || []).find(p => p.id === id)) || { id, name: "", description: "", tag: "", empty: "" };
}

/* 所有「跟着当前人格走」的界面文案 / 图标 / 标题，统一在这里推导。
   关键：它被 renderAll() 调用，所以切人格、刷新状态、登录后都会自动重算，
   不会再出现「下拉菜单换了人、副标题还是上一位」的状态失同步。 */
function renderPersonaChrome() {
  const p = currentPersona();
  if (!S || !S.settings) return;
  const name = personaDisplayName(p) || S.settings.personaName || "";

  // 标题 + 副标题（副标题绑定 description，绝不硬编码）
  const t = $("#chatTitle");
  if (t) t.textContent = name ? T("和{name}聊天", { name }) : T("开始聊天");
  const sub = $("#chatSub");
  if (sub) {
    const desc = personaDisplayDesc(p) || S.settings.personaTag || "";
    sub.textContent = IS_GUEST ? T("{desc}（访客模式：这次是临时委托，聊完就忘）", { desc }) : desc;
  }
  // 右下角 Q 版悬浮小人：形象 / 尺寸 / 动画参数 / 台词全部取自主题配置
  FloatingMascot.update(PersonaThemes.mascot(p.id));
  const fmBtn = $("#fmBtn");
  if (fmBtn) fmBtn.title = p.tag
    ? T("{name} · {tag} · 点我互动", { name, tag: p.tag })
    : T("{name} · 点我互动", { name });
  // 顶栏左侧的人格头像已按需求移除（.ph-left 只剩空占位，用于保持标题居中）

  // 左下角触发图标 / 默认头像也要跟着换（之前只在启动时算过一次）
  updateAccountWidget();
}

async function becomeGuest(silent) {
  try {
    const res = await fetch("/api/auth/guest", { method: "POST", headers: { "Content-Type": "application/json" } });
    if (!res.ok) throw new Error(T("访客通道不可用 ") + res.status);
    const data = await res.json();
    TOKEN = data.token;
    localStorage.setItem(TOKEN_KEY, TOKEN);
    await enterApp();
    if (!silent) toast(T("访客模式已开启"), T("随便聊，不用注册。记忆功能处于关闭状态。"));
  } catch (err) {
    // ★ 2026-09-17 修：原来这里按「hostname 不是 127.0.0.1/localhost」判定"预览代理环境"，
    //   提示去打开 http://127.0.0.1:8903（连端口都是硬编码的）。部署到公网后 hostname 就是
    //   域名/IP，于是**服务端只要有一次请求异常，就会弹出这个与站点毫无关系的本机地址**，
    //   把人指到本机去 —— 2026-09-17 在阿里云那台上真实发生过。
    //   改为如实报错：err.message 本身就足够区分"连不上"和"服务端报错"。
    $("#gateErr").textContent = T("无法连接服务器：") + err.message;
  }
}

function leaveGuest() {
  TOKEN = ""; ME = null; S = null; IS_GUEST = false;
  localStorage.removeItem(TOKEN_KEY);
  $("#guestBar").hidden = true;
  showGate(T("注册或登录后，TA 就能真正记住你的事了"));
}

async function doLogout() {
  const msg = IS_GUEST
    ? T("退出访客模式？（临时数据会立即清空，注册后数据就能保存）")
    : T("退出登录？（数据都保存在账号里，随时回来）");
  if (!confirm(msg)) return;
  if (!IS_GUEST) { try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { } }
  TOKEN = ""; ME = null; IS_GUEST = false;
  localStorage.removeItem(TOKEN_KEY);
  S = null;
  $("#guestBar").hidden = true;
  showGate(T("已退出，欢迎再来万事屋坐坐"));
}

function bindGate() {
  const gate = $("#loginGate");
  let mode = "login";
  gate.addEventListener("click", async (e) => {
    const tab = e.target.closest(".gate-tab");
    if (tab) {
      mode = tab.dataset.mode;
      $$(".gate-tab").forEach(t => t.classList.toggle("active", t === tab));
      $("#gatePass2Wrap").hidden = mode !== "register";
      $("#gateSubmit").textContent = mode === "login" ? T("登 录") : T("注 册");
      // 切换模式时清空确认密码并恢复隐藏状态
      const p2 = $("#gatePass2");
      p2.value = ""; p2.type = "password";
      $("#gateErr").textContent = "";
      return;
    }
    if (e.target.id === "gateGuest") { await becomeGuest(); return; }
    if (e.target.id !== "gateSubmit") return;
    const username = $("#gateUser").value.trim();
    const password = $("#gatePass").value;
    const errEl = $("#gateErr");
    errEl.textContent = "";
    if (mode === "register" && password !== $("#gatePass2").value) {
      errEl.textContent = T("两次输入的密码不一致"); return;
    }
    $("#gateSubmit").disabled = true;
    try {
      const res = await fetch(`/api/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { errEl.textContent = data.error || T("操作失败，请稍后再试"); return; }
      TOKEN = data.token;
      localStorage.setItem(TOKEN_KEY, TOKEN);
      await enterApp();
      toast(mode === "login" ? T("欢迎回来") : T("注册成功"),
        mode === "login" ? T("哟，又来了啊。") : T("新账号已就绪，开始你们的故事吧。"));
    } catch (err) {
      // ★ 2026-09-17 修：与 becomeGuest 同一处问题（见上面的注释）——
      //   别再把"非本机 hostname"当成预览代理环境，如实报错。
      errEl.textContent = T("无法连接服务器：") + err.message;
    } finally {
      $("#gateSubmit").disabled = false;
    }
  });
  $("#gatePass").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !$("#gateSubmit").disabled) $("#gateSubmit").click();
  });
  $("#gatePass2").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !$("#gateSubmit").disabled) $("#gateSubmit").click();
  });
  // 密码可视切换（事件委托，两个输入框共用）
  gate.addEventListener("click", (e) => {
    const eye = e.target.closest(".eye-btn");
    if (!eye) return;
    const input = document.getElementById(eye.dataset.target);
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.textContent = show ? "🙈" : "👁";
  });
}

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeaders() }
  });
  if (res.status === 401) {
    showGate(path === "/api/auth/me" ? "" : T("登录已过期，请重新登录"));
    throw new Error(T("未登录"));
  }
  if (!res.ok) {
    let msg = ""; try { msg = (await res.json()).error || ""; } catch (e) { }
    throw new Error(msg || res.status);
  }
  const data = await res.json();
  maybePopBadges(data);   // 有里程碑新解锁就弹窗
  return data;
}

/* ---------- Toast / 通知 ---------- */
function toast(title, body, warn) {
  const el = document.createElement("div");
  el.className = "toast" + (warn ? " warn" : "");
  el.innerHTML = `<b>${esc(title)}</b><p>${esc(body || "")}</p>`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; setTimeout(() => el.remove(), 400); }, 4200);
}
function notifyOS(title, body) {
  if ("Notification" in window && Notification.permission === "granted") new Notification(title, { body });
  else toast(title, body);
}
async function requestNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch (e) { }
  }
}

/* ---------- 里程碑解锁弹窗（Steam 风格 · 右下角） ---------- */
function achvChime(legend) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ac = achvChime._ac || (achvChime._ac = new Ctx());
    if (ac.state === "suspended") ac.resume();
    const notes = legend ? [523.25, 659.25, 783.99] : [587.33, 880];
    notes.forEach((f, i) => {
      const t0 = ac.currentTime + i * 0.1;
      const osc = ac.createOscillator(), g = ac.createGain();
      osc.type = "triangle";
      osc.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(0.14, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.5);
      osc.connect(g).connect(ac.destination);
      osc.start(t0); osc.stop(t0 + 0.55);
    });
  } catch (e) { /* 浏览器未授权音频时静默 */ }
}

function popBadge(b, delay) {
  const stack = $("#achvStack");
  if (!stack || !b) return;
  setTimeout(() => {
    while (stack.children.length >= 4) stack.firstElementChild.remove();  // 最多同时 4 条
    const el = document.createElement("div");
    el.className = `achv rar-${b.rarity || "common"}`;
    const time = new Date(b.unlockedAt || Date.now())
      .toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    el.innerHTML = `
      <div class="achv-ico">${b.ico || "🎖"}</div>
      <div class="achv-body">
        <div class="achv-kicker">${T("Achievement Unlocked · 成就已解锁")}</div>
        <div class="achv-name">${esc(b.name)}</div>
        <div class="achv-desc">${esc(b.desc)}</div>
        <span class="achv-rarity">${esc(b.rarityLabel || T("普通"))}</span>
      </div>
      <div class="achv-meta">${time}</div>
      <div class="achv-bar"><i></i></div>`;
    el.style.pointerEvents = "auto";
    stack.appendChild(el);
    requestAnimationFrame(() => el.classList.add("in"));
    achvChime(b.rarity === "legend");

    const kill = () => {
      el.classList.remove("in");
      el.classList.add("out");
      setTimeout(() => el.remove(), 480);
    };
    el._timer = setTimeout(kill, b.rarity === "legend" ? 8000 : 6400);
    el.addEventListener("click", () => { clearTimeout(el._timer); kill(); });
  }, delay || 0);
}

/* 服务端把「本次新解锁」放在 state.newBadges 里，只会给一次 */
function maybePopBadges(data) {
  const list = data && data.newBadges;
  if (!Array.isArray(list) || !list.length) return;
  list.slice(0, 8).forEach((b, i) => popBadge(b, i * 800));
}

/* ---------- 渲染：全局 ---------- */
function renderAll() {
  // 必须最先登记整套主题：下面 renderChat 要按「每条消息自己的角色」取头像配置，
  // 若 setAll 排在后面，首屏（尤其登录后人格不是默认值时）会取不到而回退成默认头像。
  PersonaThemes.setAll((S && S.personaThemes) || {});
  I18N.applyStatic();        // 静态文案按当前语言刷新（幂等，每次渲染都对齐一次）
  renderChat();
  renderMemories();
  renderTodos();
  renderGrowth();
  renderConversations();
  fillSettingsForm();
  updateModeHint();
  updateBadges();
  renderPersonaChrome();     // 标题/副标题/悬浮小人/左下角图标 —— 跟着当前人格重算
  PersonaSwitcher.update({
    personas: (S && S.personas) || [],
    currentPersona: S && S.settings.personaId,
    themes: (S && S.personaThemes) || {}
  });
}

function updateBadges() {
  const mb = $("#memoryBadge"), tb = $("#todoBadge");
  const mems = S.memories.length;
  mb.hidden = !mems; mb.textContent = mems > 99 ? "99+" : mems;
  const open = S.todos.filter(t => !t.done).length;
  tb.hidden = !open; tb.textContent = open > 99 ? "99+" : open;
  const g = S.growth;
  const lvChip = $("#lvChip");
  if (lvChip) lvChip.textContent = "Lv." + g.level + " · " + T(g.stage);   // 称号名随语言（后端数据作 key）
}

function updateModeHint() {
  const el = $("#modeHint");
  if (IS_GUEST) {
    el.textContent = S.settings.hasKey
      ? T("访客模式：已连接 {model} · 不保存记忆、不写入数据库", { model: S.settings.model })
      : T("访客模式 · 演示回复：未配置 API Key，回复由后端本地生成（访客也可以填自己的 Key，仅本次有效）");
    return;
  }
  el.textContent = S.settings.hasKey
    ? T("已连接：{model} · 记忆自动沉淀{state} · 数据保存在你的账号（云端 MySQL）",
        { model: S.settings.model, state: S.settings.autoExtract ? T("已开启") : T("已关闭") })
    : T("演示模式：未配置 API Key，回复由后端本地生成 · 前往设置页填入接口即可使用真实模型");
}

/* ---------- 思考过程 / 工具调用 的渲染片段 ---------- */
function thinkHTML(text, live) {
  if (!text) return "";
  return `<details class="think-box${live ? " live" : ""}"${live ? " open" : ""}>
      <summary><span class="tk-ico">🧩</span>${T("思考过程")}${live ? '<span class="tk-live">' + T("进行中") + '</span>' : ""}</summary>
      <div class="tk-body" id="${live ? "liveThink" : ""}">${esc(text)}</div>
    </details>`;
}
function toolsHTML(tools) {
  if (!tools || !tools.length) return "";
  return `<div class="tool-chips">${tools.map(t =>
    `<span class="tool-chip ${t.ok ? "" : "bad"}" title="${esc(t.name || "")}">${esc(t.label || t.name)}</span>`
  ).join("")}</div>`;
}

/* ★ 工作台结论小标：这条消息不是闲聊，是「一次工程委托跑完留下的结论」。
   点一下直接跳进工作台看那一次的全过程（第几步干了什么、改了哪些文件）。
   为什么用 data-run 而不是存 JS 对象：消息会被 renderChat 整段重建，监听只能委托到容器上。 */
function agentChipHTML(m) {
  const a = m && m.agent;
  if (!a || !a.runId) return "";
  const ok = a.status === "done";
  return `<span class="agent-chip ${ok ? "ok" : "warn"}" data-run="${esc(a.runId)}" role="button" tabindex="0"`
    + ` title="${T("点一下去工作台看这次委托的全过程")}">🛠 ${T("工作台结论")}</span>`;
}

/* 任务分级小标：日常 / 复杂任务（后端判定的结果，如实展示，不猜） */
function taskChipHTML(m) {
  if (!m || m.role === "user" || !m.taskLevel) return "";
  const complex = m.taskLevel === "complex";
  return `<span class="task-chip ${complex ? "complex" : "simple"}" title="${T("本句由任务分级器判定")}">`
    + (complex ? T("复杂任务") : T("日常")) + `</span>`;
}

/* ★ 推脱卡（需求 3）：复杂任务被当前人格推脱时，给一键交给小玉的入口 */
const ROUTE_HANDLED = new Set();     // 已经点过「让小玉来处理」的卡片（避免重复点）
function routeCardHTML(m) {
  const r = m && m.route;
  if (!r || r.action !== "deflect" || !r.suggestPid) return "";
  if (ROUTE_HANDLED.has(m.id)) return "";
  const name = personaDisplayName(((S.personas || []).find(x => x.id === r.suggestPid)) || {}) || T("小玉");
  const why = (r.reasons || []).slice(0, 2).join("、");
  return `<div class="route-card" data-suggest="${esc(r.suggestPid)}">
      <div class="rc-main">
        <span class="rc-ico">🧰</span>
        <div>
          <div class="rc-title">${T("这个交给{name}更合适", { name: esc(name) })}</div>
          <div class="rc-why">${T("判定为复杂任务")}${why ? "（" + esc(why) + "）" : ""}</div>
        </div>
      </div>
      <div class="rc-ops">
        <button class="btn-sm primary" data-act="handoff" data-pid="${esc(r.suggestPid)}" data-mid="${esc(m.id)}">${T("让小玉来处理")}</button>
        <!-- 工作台（M2）：转交只是换人回答；真要动文件得把工作区交出去 -->
        <button class="btn-sm" data-act="workbench" data-mid="${esc(m.id)}">${T("🛠 在工作台里开工")}</button>
        <button class="btn-sm" data-act="dismiss-route" data-mid="${esc(m.id)}">${T("就你来说")}</button>
      </div>
    </div>`;
}

/* ---------- 渲染：聊天 ---------- */

/* 角色标志物（内联 SVG）：银发 / 眼镜 / 红旗袍，头像与吉祥物共用一套 */
function personaBust(pid, size, opts) {
  const cur = (S && S.settings && S.settings.personaId) || "gintoki";
  return PERSONA_MARKS.bust(pid || cur, size, opts);
}

/* 消息头像是「说话时那个人」的，不随当前人格变化 */
const FALLBACK_EMOJI = { gintoki: "🥤", kagura: "🌂", shinpachi: "👓", tama: "🤖" };
const FALLBACK_NAME = { gintoki: "银时", kagura: "神乐", shinpachi: "新八", tama: "小玉" };
function personaOf(pid) {
  const p = ((S && S.personas) || []).find(x => x.id === pid);
  if (p) return { emoji: p.emoji, name: personaDisplayName(p) };
  const id = pid || (S && S.settings && S.settings.personaId) || "gintoki";
  return {
    emoji: FALLBACK_EMOJI[id] || "🥤",
    name: T(FALLBACK_NAME[id] || "银时")
  };
}

function renderChat() {
  // 标题由 renderPersonaChrome() 统一负责，这里不重复赋值（避免两个写入点导致状态不一致）
  const list = $("#chatList");
  if (!S.chats.length) {
    const cur = ((S.personas || []).find(x => x.id === S.settings.personaId)) || {};
    const curLabel = personaDisplayName(cur) || S.settings.personaName || "";
    // 空对话页的正文是「角色说的话」，属于内容：优先用后端数据，没有再退回通用文案
    const emptyText = cur.empty || T("委托内容随意——聊天、吐槽、记事、提醒，本日免费。");
    // 空对话页大图：用用户导入的 Q 版立绘（与右下角小人同一套主题配置），缺图时降级回内联 SVG 半身像
    const pid = S.settings.personaId;
    const bustSvg = personaBust(pid, 96, { milk: pid === "gintoki" });
    const mimg = (typeof PersonaThemes !== "undefined" && PersonaThemes.mascot(pid)) || {};
    list.innerHTML = `
      <div class="empty-chat">
        <div class="big-emoji">${mimg.image ? `<img class="empty-chibi" src="${esc(mimg.image)}" alt="" draggable="false">` : bustSvg}</div>
        <h3>${esc(curLabel ? T("这里是万事屋 · ") + curLabel : T("这里是万事屋"))}</h3>
        <p>${esc(emptyText)}</p>
      </div>`;
    const eImg = list.querySelector(".empty-chat .big-emoji img");
    if (eImg) eImg.onerror = () => {
      eImg.onerror = null;
      const box = list.querySelector(".empty-chat .big-emoji");
      if (box) box.innerHTML = bustSvg;
    };
    $("#quickChips").style.display = "flex";
    return;
  }
  $("#quickChips").style.display = "none";
  let html = "", lastDate = "";
  for (const m of S.chats) {
    const d = new Date(m.ts).toDateString();
    if (d !== lastDate) {
      lastDate = d;
      html += `<div class="date-sep"><span>${new Date(m.ts).toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "short" })}</span></div>`;
    }
    // ── 状态来源规范（务必遵守）────────────────────────────────
    //  · 组件的「当前人格」只能来自全局 S.settings.personaId（见 currentPersona()）
    //  · 每条消息的「发送者人格」只能来自该条消息自己的元数据 m.pid
    //    （落库在 chats.persona，重载后依然有效）
    //  两者绝不可混用：否则切一次人格，历史消息会集体换脸。
    const senderPid = m.pid || (S && S.settings && S.settings.personaId) || "";
    const who = m.role === "user" ? null : personaOf(senderPid);
    const myAvatar = (m.role === "user")
      ? (S.settings.avatar
          ? `<img class="avatar-img" src="${esc(S.settings.avatar)}" alt="${T("我")}">`
          : T("你"))
      : `<img class="avatar-img" src="${esc(PersonaThemes.avatar(senderPid))}" alt="${esc(who.name)}">`;
    html += `
      <div class="msg ${m.role === "user" ? "user" : "ai"}">
        <div class="avatar"${who ? ` title="${esc(who.name)}"` : ""}>${myAvatar}</div>
        <div>
          ${m.role === "user" ? filesHTML(m.files) : ""}
          ${m.role === "user" ? "" : thinkHTML(m.think, false)}
          <div class="bubble" data-mid="${m.id}">${esc(m.text)}</div>
          ${m.role === "user" ? "" : toolsHTML(m.tools)}
          ${m.role === "user" ? "" : routeCardHTML(m)}
          <div class="msg-ops">${m.greet ? '<span class="greet-tag">' + T("开场白") + '</span>' : ""}${taskChipHTML(m)}${agentChipHTML(m)}<button data-act="copy" data-mid="${m.id}">${T("复制")}</button></div>
        </div>
      </div>`;
  }
  list.innerHTML = html;
  hydrateFileMedia();                // 图片附件用带鉴权的 fetch 取回再显示
  applyChatScroll();
}
function scrollChat(force) {
  const el = $("#chatScroll");
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
  if (nearBottom || force) el.scrollTop = el.scrollHeight;
}

/* ---------- 位置记忆：标签页 + 聊天滚动 ---------- */
const TAB_KEY = "yorozuya-tab";
const SCROLL_KEY = "yorozuya-chat-scroll";
let chatStick = true;          // 是否跟在最新消息上
let chatSavedTop = 0;

function saveChatScroll() {
  const el = $("#chatScroll");
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  chatStick = nearBottom;
  chatSavedTop = el.scrollTop;
  try {
    localStorage.setItem(SCROLL_KEY, JSON.stringify({ top: el.scrollTop, stick: nearBottom }));
  } catch (e) { }
}

function loadChatScroll() {
  try {
    const raw = localStorage.getItem(SCROLL_KEY);
    if (!raw) return null;
    const o = JSON.parse(raw);
    return (o && typeof o.top === "number") ? o : null;
  } catch (e) { return null; }
}

/* 把聊天列表恢复到「上一次离开时的位置」 */
function applyChatScroll() {
  const el = $("#chatScroll");
  if (!el) return;
  const place = () => {
    if (chatStick) {
      el.scrollTop = el.scrollHeight;
    } else {
      const max = Math.max(0, el.scrollHeight - el.clientHeight);
      el.scrollTop = Math.min(chatSavedTop, max);
    }
  };
  place();
  // 布局（字体、emoji、长文本换行）可能还没稳定，下一帧再校准一次
  requestAnimationFrame(() => { place(); requestAnimationFrame(place); });
}

function switchTab(name) {
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + name));
  try { localStorage.setItem(TAB_KEY, name); } catch (e) { }
  if (name === "chat") applyChatScroll();   // 回到聊天页时恢复停住的位置
  requestNotifyPermission();
  // 工作台（agent 层）：外壳要在「页面」与「右侧抽屉」之间搬 —— 由它自己决定挂哪边
  if (typeof Workbench !== "undefined" && Workbench.onTab) Workbench.onTab(name);
}

function restoreLastTab() {
  let name = "chat";
  try { name = localStorage.getItem(TAB_KEY) || "chat"; } catch (e) { }
  if (!$("#tab-" + name)) name = "chat";
  switchTab(name);
}

/* ---------- 设置：模态弹窗（取代独立设置页） ---------- */
let settingsSection = "general";
function setSettingsSection(sec) {
  settingsSection = sec;
  $$("#settingsNav .sm-nav-item").forEach(b => b.classList.toggle("active", b.dataset.section === sec));
  $$("#settingsModal .sm-section").forEach(s => s.classList.toggle("active", s.dataset.section === sec));
}
function openSettings(section) {
  fillSettingsForm();                 // 重新拉取最新值（persona 网格 / 账号 / 接口）
  setSettingsSection(section || "general");
  const m = $("#settingsModal"); if (!m) return;
  m.hidden = false;
  requestAnimationFrame(() => m.classList.add("open"));   // 触发 0.2s 缩放淡入
}
function closeSettings() {
  const m = $("#settingsModal"); if (!m) return;
  m.classList.remove("open");
  // 外观主题 / 语言是「点击即生效并落库」的，这里不需要回滚（详见 setUiMode / setLangPref）
  setTimeout(() => { m.hidden = true; }, 200);
}

/* 语言：点一下立刻切换界面文案 + 立即落库。
   除了刷静态文案，还要让「动态渲染出来的文字」重来一遍 —— 用一个只重绘文本、
   不动表单输入值的刷新函数，避免把用户正在填的 API Key 之类冲掉。 */
async function setLangPref(pref) {
  I18N.setLang(pref);                              // 静态文案 + 广播 i18n:changed
  refreshI18nText();                               // 动态文案（不含设置表单的输入值）
  try {
    S = await api("/api/settings", { method: "PUT", body: JSON.stringify({ lang: pref }) });
  } catch (err) {
    toast(T("操作失败"), err.message, true);
    I18N.setLang(S ? S.settings.lang : "zh-CN");
    refreshI18nText();
  }
}

/* 只刷新「会被翻译影响」的动态区域；刻意不调用 fillSettingsForm()，
   否则用户在设置弹窗里没保存的输入（如 API Key）会被重置。 */
function refreshI18nText() {
  if (!S) return;
  I18N.applyStatic();
  updateModeHint();
  renderChat();
  renderMemories();
  renderTodos();
  renderGrowth();
  renderConversations();
  updateBadges();
  renderPersonaChrome();
  renderAccount();
  renderThinkCapHint();          // 这句提示也是文案，切语言要跟着重译
  PersonaSwitcher.update({
    personas: (S && S.personas) || [],
    currentPersona: S && S.settings.personaId,
    themes: (S && S.personaThemes) || {}
  });
  const sl = $("#setLang"); if (sl && sl.value !== (S.settings.lang || "zh-CN")) sl.value = S.settings.lang || "zh-CN";
  if (typeof Workbench !== "undefined" && Workbench.onLangChange) Workbench.onLangChange();
}

/* 外观主题：点一下立刻换肤 + 立即落库，不需要按保存（和人格切换同一套体验） */
async function setUiMode(mode) {
  applyUiMode(mode);                                                  // 先变，界面不等网络
  $$("#uiModeCards .ui-mode-card").forEach(c => c.classList.toggle("active", c.dataset.mode === mode));
  try {
    S = await api("/api/settings", { method: "PUT", body: JSON.stringify({ uiMode: mode }) });
  } catch (err) {
    toast("外观保存失败", err.message, true);
    applyUiMode(S ? S.settings.uiMode : "auto");                      // 存不住就退回已保存的值
    fillSettingsForm();
  }
}

/* ---------- ★ 对话附件：选择 / 拖拽 / 粘贴 ----------
   取舍说明：
   · 上传走「原始字节 + 文件名走 query」——后端不依赖 python-multipart，少一个打包风险。
   · 取文件（图片预览 / 打开文本）走带鉴权的 fetch → blob URL，
     不把 token 塞进 URL（`<img src>` 带不了 Authorization 头）。 */
const MAX_FILES = 3;
let pendingFiles = [];          // 待发送附件 [{id,name,size,kind,chars,status}]

function fmtSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function docIcon() {
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"></path>
    <path d="M14 3v5h5"></path></svg>`;
}

function renderAttachBar() {
  const bar = $("#attachBar");
  if (!bar) return;
  bar.hidden = !pendingFiles.length;
  bar.innerHTML = pendingFiles.map((f, i) => `
    <span class="attach-chip ${f.status === "bad" ? "bad" : ""} ${f.status === "up" ? "uploading" : ""}"
          data-idx="${i}" title="${esc(f.err || f.name)}">
      ${f.kind === "image" ? "🖼" : "📄"}
      <span class="ac-name">${esc(f.name)}</span>
      <span class="ac-meta">${f.status === "up" ? T("上传中…") : (f.status === "bad" ? T("失败") : fmtSize(f.size))}</span>
      <button class="ac-x" data-del="${i}" title="${T("移除")}" type="button">✕</button>
    </span>`).join("");
}

async function uploadFiles(list) {
  const files = Array.from(list || []);
  if (!files.length) return;
  for (const f of files) {
    if (pendingFiles.length >= MAX_FILES) {
      toast(T("最多 3 个附件"), T("先发出去再传新的"), true);
      break;
    }
    if (f.size > 8 * 1024 * 1024) {
      toast(T("文件太大"), `${f.name} · ${fmtSize(f.size)}（上限 8MB）`, true);
      continue;
    }
    const slot = { id: "", name: f.name, size: f.size, kind: "?", status: "up" };
    pendingFiles.push(slot);
    renderAttachBar();
    try {
      const res = await fetch("/api/files?name=" + encodeURIComponent(f.name), {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/octet-stream" },
        body: f
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 404) {
        // 给「界面是新的、后端是旧的」这种情况用（典型：打开了旧版打包）。
        // 与其让她对着「上传失败」猜，不如直接说清是版本问题。
        throw new Error(T("当前后端没有附件功能（可能是旧版本），请用新版的 Yorozuya"));
      }
      if (!res.ok || !data.file) throw new Error(data.error || ("HTTP " + res.status));
      Object.assign(slot, data.file, { status: "ok" });
    } catch (e) {
      slot.status = "bad";
      slot.err = e.message;
      toast(T("上传失败"), f.name + "：" + e.message, true);
    }
    renderAttachBar();
  }
}

function clearAttach() { pendingFiles = []; renderAttachBar(); }

function filesHTML(files) {
  if (!files || !files.length) return "";
  return `<div class="msg-files">${files.map(f => {
    const name = esc(f.name || "");
    const size = fmtSize(f.size || 0);
    const extra = (f.kind === "text" && f.chars) ? ` · ${f.chars} 字` : "";
    if (f.kind === "image") {
      return `<a class="file-card img" data-fid="${f.id}" href="javascript:void(0)"
                 title="${name} · ${size}"><img data-fid="${f.id}" alt="${name}"></a>`;
    }
    return `<a class="file-card" data-fid="${f.id}" href="javascript:void(0)" title="${name}">
        ${docIcon()}<span class="fc-name">${name}</span><span class="fc-meta">${size}${extra}</span></a>`;
  }).join("")}</div>`;
}

/* 图片用带鉴权的 fetch 取回来再显示；文本类的卡点一下「打开」 */
async function hydrateFileMedia() {
  for (const img of document.querySelectorAll(".file-card img[data-fid]:not([data-loaded])")) {
    img.dataset.loaded = "1";
    try {
      const res = await fetch(`/api/files/${img.dataset.fid}`, { headers: authHeaders() });
      if (!res.ok) continue;
      img.src = URL.createObjectURL(await res.blob());
    } catch (e) { /* 取不到就留空白，不打扰用户 */ }
  }
}

async function openFile(fid) {
  try {
    const res = await fetch(`/api/files/${fid}`, { headers: authHeaders() });
    if (!res.ok) { toast(T("打不开这个文件"), "HTTP " + res.status, true); return; }
    window.open(URL.createObjectURL(await res.blob()), "_blank");
  } catch (e) { toast(T("打不开这个文件"), e.message, true); }
}

/* ---------- 发送消息（SSE 流式） ---------- */
async function sendMessage(text) {
  text = (text || "").trim();
  const sent = pendingFiles.filter(f => f.status === "ok");
  if ((!text && !sent.length) || busy) return;
  if (pendingFiles.some(f => f.status === "up")) { toast(T("还有附件在上传"), T("稍等一下再发"), true); return; }
  const sendIds = sent.map(f => f.id);
  // 幂等键：同一条消息无论重试几次都是同一个 id —— 后端据此认出「这条已经处理过了」，
  // 不会把同一句话生成两遍（切人格 / 刷新 / 网络重试都可能把同一条再送一次）。
  const msgId = "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  busy = true;
  $("#btnSend").disabled = true;

  // 本地先渲染用户消息 + AI 占位
  const now = Date.now();
  const tmpMsg = { id: "tmp-u", ts: now, role: "user", text };
  if (sent.length) tmpMsg.files = sent.map(f => ({ ...f }));   // 先把附件画出来，不等服务端
  S.chats.push(tmpMsg);
  chatStick = true;            // 自己发消息，必然跟到最新
  renderChat();
  hydrateFileMedia();
  $("#quickChips").style.display = "none";
  const wrap = document.createElement("div");
  wrap.className = "msg ai";
  // 正在回复的就是「当前人格」，所以这里用全局 currentPersona 的头像；
  // 写成和 renderChat 一致的形式，避免流式结束前后头像形态跳变。
  wrap.innerHTML = `<div class="avatar"><img class="avatar-img" src="${esc(PersonaThemes.avatar(S.settings.personaId))}" alt=""></div>
    <div class="live-col">
      <div id="liveThinkWrap"></div>
      <div class="bubble" id="liveBubble"><span class="typing-cursor"></span></div>
      <div id="liveTools"></div>
    </div>`;
  $("#chatList").appendChild(wrap);
  scrollChat(true);

  // 任务分级：流式期间先记下来（结束时以服务端状态为准重绘，保证刷新后仍在）
  let liveTask = null;

  let acc = "", thinkAcc = "", liveTools = [];
  const showThink = !S.settings || S.settings.showThink !== false;
  const paintThink = (force) => {
    if (!showThink || (!thinkAcc && !force)) return;
    const el = $("#liveThinkWrap");
    if (!el) return;
    if (!el.dataset.on) { el.dataset.on = "1"; el.innerHTML = thinkHTML(thinkAcc, true); }
    const body = $("#liveThink");
    if (body) body.textContent = thinkAcc;
    scrollChat(false);
  };
  const paintTools = () => {
    const el = $("#liveTools");
    if (el) el.innerHTML = toolsHTML(liveTools);
    scrollChat(false);
  };
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ text, fileIds: sendIds, msgId })
    });
    if (res.status === 401) { showGate(T("登录已过期，请重新登录")); throw new Error(T("未登录")); }
    if (res.status === 409) {
      // 这条消息已经被回复过了 → 绝不生成第二遍。撤掉占位气泡，按服务端状态重绘即可。
      if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap);
      busy = false;
      $("#btnSend").disabled = false;
      S = await api("/api/state");
      renderAll();
      return;
    }
    if (!res.ok || !res.body) throw new Error(T("后端不可用 ") + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        const t = line.trim();
        if (!t.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(t.slice(5).trim()); } catch (e) { continue; }
        if (ev.error) throw new Error(ev.error);
        if (ev.notice) toast(T("提示"), ev.notice);
        // ★ 需求 5：auto 模式下服务端已切到小玉 —— 顶栏人格与主题立刻跟着走
        if (ev.switchPersona && S && S.settings) {
          S.settings.personaId = ev.switchPersona;
          const p = (S.personas || []).find(x => x.id === ev.switchPersona) || {};
          S.settings.personaName = p.name || S.settings.personaName;
          S.settings.personaEmoji = p.emoji || S.settings.personaEmoji;
          S.settings.personaTheme = (S.personaThemes || {})[ev.switchPersona] || S.settings.personaTheme;
          applyTheme(S.settings.personaTheme);
          renderPersonaChrome();
          renderChat();
        }
        if (ev.task) {
          liveTask = ev.task;
          const tw = $("#liveTools");     // 复用工具行容器：先给个分级小标，让你知道它怎么判的
          if (tw && !tw.dataset.taskChip) {
            tw.dataset.taskChip = "1";
            tw.insertAdjacentHTML("beforeend",
              `<span class="task-chip ${ev.task.level === "complex" ? "complex" : "simple"}">`
              + (ev.task.level === "complex" ? T("复杂任务") : T("日常")) + `</span>`);
          }
        }
        if (ev.think) { thinkAcc += ev.think; paintThink(false); }
        if (ev.tool) { liveTools.push(ev.tool); paintTools(); }
        if (ev.delta) {
          acc += ev.delta;
          const b = $("#liveBubble");
          if (b) b.innerHTML = esc(acc) + '<span class="typing-cursor"></span>';
          scrollChat(false);
        }
        if (ev.done) {
          acc = ev.reply || acc;
          thinkAcc = ev.think || thinkAcc;
          liveTools = (ev.tools && ev.tools.length) ? ev.tools : liveTools;
          paintThink(true); paintTools();
        }
      }
    }
  } catch (err) {
    toast(T("请求失败"), err.message, true);
    S.chats = S.chats.filter(m => m.id !== "tmp-u");
  }

  wrap.remove();
  // 重新拉取服务端状态（含新消息、成长、可能的新记忆）
  await refreshState();
  clearAttach();                     // 附件已随消息发出去，清空待发区
  busy = false;
  $("#btnSend").disabled = false;
  $("#chatInput").focus();
  // 记忆提取在后台异步进行，稍后再刷新一次角标
  setTimeout(() => refreshState(), 3000);
}

async function refreshState() {
  try {
    S = await api("/api/state");
    renderAll();
  } catch (e) { console.error(e); }
}

/* ---------- 渲染：记忆 ---------- */
function renderMemories() {
  const list = $("#memoryList");
  if (!S.memories.length) {
    list.innerHTML = IS_GUEST
      ? `<div class="card" style="justify-content:center;color:var(--ink-3)">${T("访客模式没有记忆——TA 记性就这样。注册账号后，会把你说的重要的事一条条记下来。")}</div>`
      : `<div class="card" style="justify-content:center;color:var(--ink-3)">${T("还没有记忆。聊天时{name}会自动记下重要的事，也可以在上方手动添加。", { name: esc(personaDisplayName(currentPersona()) || S.settings.personaName) })}</div>`;
  } else {
    list.innerHTML = S.memories.map(m => `
      <div class="card ${m.pinned ? "pinned" : ""}" data-id="${m.id}">
        <span class="tag ${m.type}">${m.type}</span>
        <div class="body">
          <div class="txt">${esc(m.text)}</div>
          <div class="meta">${new Date(m.ts).toLocaleDateString(I18N.dateLocale())}${m.pinned ? T(" · 置顶") : ""}</div>
        </div>
        <div class="ops">
          <button class="icon-btn" data-op="pin" title="${T("置顶")}">${m.pinned ? "📌" : "📍"}</button>
          <button class="icon-btn" data-op="del" title="${T("删除")}">🗑️</button>
        </div>
      </div>`).join("");
  }
  updateBadges();
}

/* ---------- 渲染：历史对话 ----------
   多会话：每段对话独立保存，切会话只是换 settings.convId；
   消息本身按 conv 归属（后端保证），前端只负责展示当前会话的消息。 */
let editingConv = null;      // 正在行内重命名的会话 id（null = 没有）

function convTitle(c) {
  const t = (c && c.title ? c.title : "").trim();
  return t || T("新对话");
}

function convTime(ms) {
  if (!ms) return "";
  const d = new Date(ms), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const opts = sameDay ? { hour: "2-digit", minute: "2-digit" }
                       : { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" };
  return d.toLocaleString(I18N.dateLocale(), opts);
}

/* focusEdit 只在「用户刚点了重命名」时为 true。
   ⚠️ 不能每次重绘都 focus：重命名是异步的，若期间用户把焦点移到了别处（如搜索框），
   一次过期的重绘会把焦点抢回输入框，用户接下来打的字就跑到输入框里去了。 */
function renderConversations(focusEdit) {
  const list = $("#convList");
  if (!list || !S) return;
  const all = S.conversations || [];
  const q = (($("#convSearch") || {}).value || "").trim().toLowerCase();
  const items = q
    ? all.filter(c => convTitle(c).toLowerCase().includes(q) || (c.preview || "").toLowerCase().includes(q))
    : all;

  const empty = $("#convEmpty");
  // 空提示的文案「每次都重设」：否则它在隐藏期间遇到切换语言会留下旧语言的文本，
  // 下次显示出来就变成中英混着的样子。
  if (empty) {
    empty.textContent = all.length
      ? T("没有找到匹配的对话")
      : T("还没有别的对话。点右上角「新建对话」开一段新的。");
    empty.hidden = items.length > 0;
  }
  if (!items.length) {
    list.innerHTML = "";
    return;
  }

  list.innerHTML = items.map(c => `
    <div class="card conv-item ${c.current ? "active" : ""}" data-cid="${c.id}">
      <span class="conv-mark">${c.current ? esc(T("当前")) : ""}</span>
      <div class="body">
        ${editingConv === c.id
          ? `<input class="conv-edit" type="text" maxlength="40" value="${esc(convTitle(c))}">`
          : `<div class="txt">${esc(convTitle(c))}</div>
             <div class="meta">${esc(convTime(c.updatedAt))} · ${esc(T(c.count === 1 ? "1 条消息" : "{n} 条消息", { n: c.count }))}</div>
             ${c.preview ? `<div class="conv-preview">${esc(c.preview)}</div>` : ""}`}
      </div>
      <div class="ops">
        <button class="icon-btn" data-op="rename" title="${esc(T("重命名"))}">✏️</button>
        <button class="icon-btn" data-op="del" title="${esc(T("删除"))}">🗑️</button>
      </div>
    </div>`).join("");

  const input = list.querySelector(".conv-edit");
  if (input && focusEdit) { input.focus(); input.select(); }
}

async function newConversation() {
  try {
    S = await api("/api/conversations", { method: "POST" });
    editingConv = null;
    renderAll();
    switchTab("chat");          // 新会话直接开聊
    toast(T("已新建对话"), T("上一段还在「历史」里，随时可以回去"));
  } catch (err) { toast(T("操作失败"), err.message, true); }
}

async function switchConversation(cid) {
  // 点当前这段＝回到对话页（列表里点自己也有反馈，不留"点了没反应"）
  if (!cid || cid === (S && S.settings.convId)) { switchTab("chat"); return; }
  try {
    S = await api(`/api/conversations/${encodeURIComponent(cid)}/switch`, { method: "POST" });
    editingConv = null;
    renderAll();
    switchTab("chat");
    toast(T("已切换对话"), T("接着上次的话头继续吧"));
  } catch (err) { toast(T("操作失败"), err.message, true); }
}

async function renameConversation(cid, title) {
  try {
    S = await api(`/api/conversations/${encodeURIComponent(cid)}`, {
      method: "PATCH", body: JSON.stringify({ title })
    });
    editingConv = null;
    renderConversations();
    toast(T("已重命名"), T("对话名已更新"));
  } catch (err) { toast(T("操作失败"), err.message, true); }
}

async function deleteConversation(cid, title) {
  if (!confirm(T("删除「{name}」？这段对话的消息会一起消失，无法恢复。", { name: title }))) return;
  try {
    S = await api(`/api/conversations/${encodeURIComponent(cid)}`, { method: "DELETE" });
    editingConv = null;
    renderAll();
    toast(T("已删除对话"), T("那段对话已经不在了"));
  } catch (err) { toast(T("操作失败"), err.message, true); }
}

/* ---------- 渲染：待办 ---------- */
function renderTodos() {
  const list = $("#todoList");
  const now = Date.now();
  let todos = [...S.todos];
  if (todoFilter === "open") todos = todos.filter(t => !t.done);
  if (todoFilter === "done") todos = todos.filter(t => t.done);
  if (!todos.length) {
    list.innerHTML = `<div class="card" style="justify-content:center;color:var(--ink-3)">${todoFilter === "all" ? T("没有待办。加一个吧，比如「记得交房租」（说的是他）") : T("这里空空如也。")}</div>`;
  } else {
    list.innerHTML = todos.map(t => {
      let due = "";
      if (t.due) {
        const diff = t.due - now;
        if (t.done) due = `<span class="due-label ok">${fmtDT(t.due)}</span>`;
        else if (diff < 0) due = `<span class="due-label">${T("已到期")}</span>`;
        else if (diff < 3600000) due = `<span class="due-label soon">${T("{n} 分钟后", { n: Math.max(1, Math.round(diff / 60000)) })}</span>`;
        else due = `<span class="due-label ok">${fmtDT(t.due)}</span>`;
      }
      return `
      <div class="card ${t.done ? "done" : ""}" data-id="${t.id}">
        <button class="todo-check ${t.done ? "on" : ""}" data-op="toggle">${t.done ? "✓" : ""}</button>
        <div class="body">
          <div class="txt">${esc(t.text)}</div>
          ${due ? `<div class="meta">${due}</div>` : ""}
        </div>
        <div class="ops"><button class="icon-btn" data-op="del">🗑️</button></div>
      </div>`;
    }).join("");
  }
  updateBadges();
}

/* ---------- 渲染：成长 ---------- */
function renderGrowth() {
  const g = S.growth;
  const C = 2 * Math.PI * 52;
  const ring = $("#ringFg");
  ring.style.strokeDasharray = C;
  ring.style.strokeDashoffset = C * (1 - g.pct);
  $("#growthLv").textContent = "Lv." + g.level;
  $("#growthStage").textContent = T(g.stage);
  $("#growthName").textContent = personaDisplayName(currentPersona()) || S.settings.personaName;
  $("#growthDesc").textContent = g.stageDesc;
  $("#statDays").textContent = g.days;
  $("#statMsgs").textContent = g.msgs;
  $("#statMems").textContent = g.mems;
  $("#statStreak").textContent = g.streak;
  $("#xpFill").style.width = (g.pct * 100).toFixed(1) + "%";
  $("#xpText").textContent = g.maxed
    ? T("已达最高等级 Lv.{level}（上限 Lv.{max}）· 剩下的 XP 就当白送他了",
        { level: g.level, max: g.maxLevel })
    : T("{xp} / {next} XP · 距下一级（Lv.{level} / {maxLevel}）还差 {remain} XP",
        { xp: g.xp - g.xpCur, next: g.xpNext - g.xpCur, level: g.level + 1,
          maxLevel: g.maxLevel, remain: Math.max(0, g.xpNext - g.xp) });
  const got = g.badgeGot || g.badges.filter(b => b.got).length;
  const total = g.badgeTotal || g.badges.length;
  $("#badgeCount").textContent = T("{got} / {total} 已解锁", { got, total });
  const GROUPS = ["交流", "记忆", "陪伴", "业务", "亲密度", "角色", "彩蛋"];
  const cell = (b) => `
    <div class="badge ${b.got ? "got" : "locked"}" data-bid="${b.id}" title="${b.got ? T("点击回放解锁弹窗") : T("达成条件：{hint}", { hint: esc(b.hint) })}">
      <div class="b-ico">${b.got ? b.ico : "🔒"}</div>
      <div class="b-name">${esc(b.name)}<span class="badge-rar r-${b.rarity}">${esc(b.rarityLabel || "")}</span></div>
      <div class="b-desc">${esc(b.got ? b.desc : T("达成条件：{hint}", { hint: b.hint }))}</div>
    </div>`;
  const rest = g.badges.filter(b => !GROUPS.includes(b.group));
  $("#badgeList").innerHTML = GROUPS.concat(rest.length ? ["其他"] : []).map(gp => {
    const list = g.badges.filter(b => (b.group || "其他") === gp);
    if (!list.length) return "";
    const n = list.filter(b => b.got).length;
    return `<div class="badge-group-title">${esc(T(gp))}<span>${n} / ${list.length}</span></div>`
      + `<div class="badge-row">${list.map(cell).join("")}</div>`;
  }).join("");
  updateBadges();
}

/* ============================================================
   PersonaSwitcher：顶边栏人格切换组件
   props = { personas, currentPersona, onSwitch }
     · 只负责「渲染 + 交互」，切谁由 onSwitch 回调处理
       —— 切换逻辑与主题联动（applyTheme）完全解耦
     · 无内部业务状态：每次 update() 全量重绘，
       新增人格时只需后端 personas 加一条数据，这里零改动
   ============================================================ */
const PersonaSwitcher = (() => {
  let root = null;
  let props = { personas: [], currentPersona: "", onSwitch: null };
  let opened = false;

  const $q = (sel) => (root ? root.querySelector(sel) : null);

  function mount(el, initial) {
    if (!el) return;
    root = el;
    update(initial);
    el.addEventListener("click", onClick);
    // 点击外部 / Esc 收起
    document.addEventListener("click", (e) => { if (opened && !el.contains(e.target)) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }

  function update(next) {
    props = Object.assign(props, next || {});
    render();
  }

  function current() {
    return props.personas.find(p => p.id === props.currentPersona) || props.personas[0] || {};
  }

  function setOpen(v) {
    opened = !!v;
    const m = $q(".ps-menu"), b = $q(".ps-btn");
    if (m) m.classList.toggle("open", opened);
    if (b) b.setAttribute("aria-expanded", opened ? "true" : "false");
  }

  function onClick(e) {
    if (e.target.closest(".ps-btn")) {
      e.stopPropagation();
      setOpen(!opened);
      return;
    }
    const item = e.target.closest(".ps-item");
    if (!item) return;
    e.stopPropagation();
    setOpen(false);                                     // 即时反馈：点完先收起
    if (item.classList.contains("active")) return;      // 点的就是当前这位，不折腾
    if (typeof props.onSwitch === "function") props.onSwitch(item.dataset.pid);
  }

  function render() {
    if (!root) return;
    const cur = current();
    const curLabel = personaDisplayName(cur) || cur.name || "";
    const curTheme = (props.themes || {})[props.currentPersona] || {};
    const tip = cur.name
      ? T("切换陪伴你的成员（当前：{name}{identity}）",
          { name: curLabel, identity: curTheme.identity ? " · " + curTheme.identity : "" })
      : T("切换陪伴你的成员（点击即生效）");
    root.innerHTML = `
      <button class="ps-btn" type="button" aria-haspopup="listbox" aria-expanded="${opened}" title="${esc(tip)}">
        <span class="ps-emoji"><img class="ps-avatar-img" src="${esc(PersonaThemes.avatar(props.currentPersona))}" alt=""></span>
        <span class="ps-name">${esc(curLabel || T("选择成员"))}</span>
        <span class="ps-caret">▼</span>
      </button>
      <div class="ps-menu${opened ? " open" : ""}" role="listbox" aria-label="${T("选择陪伴你的成员")}">
        <div class="ps-menu-head">${T("换个人陪你")}</div>
        ${props.personas.map(p => {
          const on = p.id === props.currentPersona;
          // 副标题一律取自数据源的 description（不硬编码），缺失时逐级回退
          const sub = personaDisplayDesc(p) || (p.title || "").split("·")[1] || p.tag || "";
          return `
          <button class="ps-item${on ? " active" : ""}" type="button" role="option"
                  aria-selected="${on}" data-pid="${p.id}">
            <span class="ps-i-emoji"><img class="ps-avatar-img" src="${esc(PersonaThemes.avatar(p.id))}" alt=""></span>
            <span class="ps-i-col">
              <span class="ps-i-name">${esc(personaDisplayName(p))}</span>
              <span class="ps-i-title">${esc(String(sub).trim())}</span>
            </span>
            <span class="ps-i-check">✓</span>
          </button>`;
        }).join("")}
      </div>`;
  }

  return { mount, update };
})();

/* ---------- 人格切换：点击即生效（不再依赖「保存」） ---------- */
let switchingPersona = false;

function switchPersonaFX(on) { document.documentElement.classList.toggle("persona-switching", !!on); }
function switchPersonaVeil(on) { const v = $("#personaVeil"); if (v) v.classList.toggle("on", !!on); }

async function switchPersona(pid) {
  if (!pid || switchingPersona || !S) return;
  if (pid === S.settings.personaId) return;
  switchingPersona = true;
  switchPersonaVeil(true);                 // 0.2s 转场遮罩：先盖住配色突变
  try {
    // 只提交 personaId，其余设置原样保留（后端按字段增量落库）
    S = await api("/api/settings", { method: "PUT", body: JSON.stringify({ personaId: pid }) });
  } catch (err) {
    switchPersonaVeil(false);
    switchingPersona = false;
    toast(T("切换失败"), err.message, true);
    return;
  }
  applyTheme(S.settings.personaTheme);     // 主题联动：背景 / 主色 / 圆角 / 吉祥物 / 水印
  switchPersonaFX(true);                   // 头像 0.3s 淡入淡出（先加类再重绘，新节点才会走动画）
  renderAll();
  setTimeout(() => { switchPersonaFX(false); switchPersonaVeil(false); }, 220);
  toast(T("已换人"), T("现在是「{name}」陪你",
    { name: personaDisplayName(currentPersona()) || S.settings.personaName }));
  switchingPersona = false;
}

/* ★ 需求 3 / 5：一键转交 —— 服务端切人格（含存档），前端把原问题再问一次
   为什么让前端再发一次：这样后续完全复用现有 /api/chat 的流式、人设守卫与记忆链路，
   不需要为「转交后回答」再开一条并行的生成路径（少一条路径 = 少一类不一致）。 */
async function doHandoff(pid, mid, btn) {
  if (!pid || busy) return;
  // 找到被推脱的那句原话（这张卡之前最近的一条用户消息）
  const idx = S.chats.findIndex(x => x.id === mid);
  let question = "";
  for (let i = idx; i >= 0; i--) {
    const m = S.chats[i];
    if (m.role === "user") { question = m.text; break; }
  }
  if (!question) { toast(T("找不到原问题了"), T("麻烦你再发一次"), true); return; }
  if (btn) { btn.disabled = true; btn.textContent = T("正在转交…"); }
  try {
    const r = await api("/api/agent/handoff", { method: "POST", body: JSON.stringify({ to: pid }) });
    if (r && r.state) { S = r.state; applyTheme(S.settings.personaTheme); renderAll(); }
    ROUTE_HANDLED.add(mid);
    renderChat();
    if (r && r.intro) toast(T("已转交小玉"), r.intro);
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = T("让小玉来处理"); }
    toast(T("转交失败"), err.message, true);
    return;
  }
  await sendMessage(question);          // 同一个人格口径下重问，这次由小玉回答
}

/* ---------- 渲染：账号状态 ---------- */
function renderAccount() {
  const g = S.growth;
  const reg = g.createdAt ? new Date(g.createdAt).toLocaleString("zh-CN",
    { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
  const openTodos = S.todos.filter(t => !t.done).length;
  const rows = [
    [T("登录账号"), esc(ME.username)],
    ["账号状态", IS_GUEST
      ? `<span class="acct-state guest">${T("访客（临时）")}</span>`
      : `<span class="acct-state">${T("正常")}</span>`],
    [T("注册时间"), IS_GUEST ? T("—（未注册）") : reg],
    [T("相伴天数"), T("{days} 天", { days: g.days })],
    ["当前等级", `Lv.${g.level} / ${g.maxLevel} · ${esc(g.stage)}`],
    [T("对话条数"), `${g.msgs}`],
    [T("长期记忆"), IS_GUEST ? T("未开启") : T("{n} 条", { n: g.mems })],
    [T("待办"), T("{open} 项未完成 / 共 {total} 项", { open: openTodos, total: S.todos.length })],
    [T("里程碑"), T("{got} / {total} 枚", { got: g.badgeGot, total: g.badgeTotal })],
    [T("客户端版本"), esc(S.build || "—")],
  ];
  const wide = IS_GUEST
    ? T("数据存储：仅保存在服务器内存中，关闭页面或重启服务即清空（注册账号后可持久保存）")
    : T("数据存储：云端 MySQL 数据库 · 密码以 PBKDF2-HMAC-SHA256 加盐哈希保存，不存明文");
  $("#acctGrid").innerHTML =
    rows.map(([k, v]) => `<div class="acct-item"><span>${k}</span><b>${v}</b></div>`).join("")
    + `<div class="acct-item wide"><span>${wide}</span></div>`;
}

function fillSettingsForm() {
  const s = S.settings;
  $("#setApiUrl").value = s.apiUrl;
  $("#setApiKey").value = "";
  $("#setApiKey").placeholder = s.hasKey ? T("已配置（留空保持不变）") : T("sk-…（留空则使用演示模式）");
  $("#setModel").value = s.model;
  $("#setUserName").value = s.userName;
  $("#setAutoExtract").checked = !!s.autoExtract;
  $("#setToolsEnabled").checked = !!s.toolsEnabled;
  $("#setShowThink").checked = !!s.showThink;
  renderThinkCapHint();
  // 外观主题（跟随角色/浅色/深色/跟随系统）+ 语言
  $$("#uiModeCards .ui-mode-card").forEach(c => c.classList.toggle("active", c.dataset.mode === (s.uiMode || "auto")));
  const sl = $("#setLang"); if (sl) sl.value = s.lang || "zh-CN";
  // ★ 任务分流：复杂任务交给小玉的方式（ask=先推脱等我点一下 / auto=自动转交）
  const rm = $("#setRouteMode"); if (rm) rm.value = s.routeMode || "ask";
  renderAccount();
}

async function saveSettings() {
  const body = {
    apiUrl: $("#setApiUrl").value.trim(),
    apiKey: $("#setApiKey").value.trim(),
    model: $("#setModel").value.trim(),
    userName: $("#setUserName").value.trim(),
    autoExtract: $("#setAutoExtract").checked,
    toolsEnabled: $("#setToolsEnabled").checked,
    showThink: $("#setShowThink").checked,
    routeMode: ($("#setRouteMode") || {}).value || "ask",
    // 外观主题 / 语言不在这里 —— 它们是「点击即生效并立即落库」（setUiMode / setLangPref）
  };
  S = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
  applyTheme(S.settings.personaTheme);
  applyUiMode(S.settings.uiMode);
  renderAll();
  toast(T("✅ 已保存"), T("设置已生效"));
  closeSettings();
}

/* ★「显示思考过程」的如实提示（她拍板：只如实提示，不自动换模型）。
   为什么需要：这个开关只控制**显示** —— 模型本身不返回 reasoning_content 时，
   打开它也什么都看不到，属于静默失效（用户只会觉得"我开了却没效果"）。
   判据来自后端 `thinkCapable`：先看"实测见过这个模型吐思考"（stats.thinkSeenModel），
   再看模型名是不是推理款；两者都不满足才提示。换完模型这里会跟着变，不用改代码。 */
function renderThinkCapHint() {
  const el = $("#thinkCapHint");
  if (!el || !S || !S.settings) return;
  const s = S.settings;
  if (s.thinkCapable) { el.hidden = true; el.textContent = ""; return; }
  el.hidden = false;
  el.textContent = T("⚠️ 当前模型 {model} 不输出思考过程 —— 这个开关打开也看不到东西。"
    + "想看到思考过程，请把「模型名称」换成推理模型（例如 deepseek-reasoner）。",
    { model: s.model || "—" });
}

/* ---------- 提醒轮询 ---------- */
setInterval(async () => {
  try {
    const { fired } = await api("/api/reminders");
    for (const t of fired) {
      notifyOS(T("⏰ {name}的提醒", { name: personaDisplayName(currentPersona()) || S.settings.personaName }), t.text);
      toast(T("⏰ 待办提醒"), t.text);
    }
    if (fired.length) {
      S = await api("/api/state");
      renderTodos(); updateBadges();
    }
  } catch (e) { /* 后端未启动时忽略 */ }
}, 15000);

/* ---------- 事件绑定 ---------- */
function bindEvents() {
  $("#nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-btn");
    if (!btn) return;
    switchTab(btn.dataset.tab);   // 侧栏现在只有 4 个页面入口；「设置」已并入账号面板
  });

  // 滚动时记住位置（节流到每帧一次）
  let scrollSaveTick = false;
  $("#chatScroll").addEventListener("scroll", () => {
    if (scrollSaveTick) return;
    scrollSaveTick = true;
    requestAnimationFrame(() => { scrollSaveTick = false; saveChatScroll(); });
  });
  window.addEventListener("beforeunload", saveChatScroll);

  const input = $("#chatInput");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(input.value); input.value = ""; input.style.height = "auto"; }
  });
  $("#btnSend").addEventListener("click", () => { sendMessage(input.value); input.value = ""; input.style.height = "auto"; });

  /* ---------- ★ 附件的四种入口：按钮选择 / 拖拽 / 粘贴 / 移除 ---------- */
  $("#btnAttach").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", async (e) => {
    await uploadFiles(e.target.files);
    e.target.value = "";                 // 复位：同一个文件再选一次也要能触发
  });
  $("#attachBar").addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) { pendingFiles.splice(Number(del.dataset.del), 1); renderAttachBar(); }
  });
  input.addEventListener("paste", async (e) => {
    const files = e.clipboardData && e.clipboardData.files;
    if (files && files.length) { e.preventDefault(); await uploadFiles(files); }
  });
  // 拖拽：整块对话区都能接，用计数避免子元素抖动导致遮罩闪烁
  const chatTab = $("#tab-chat");
  let dragDepth = 0;
  chatTab.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
    e.preventDefault(); dragDepth++; $("#dropVeil").hidden = false;
  });
  chatTab.addEventListener("dragover", (e) => { e.preventDefault(); });
  chatTab.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $("#dropVeil").hidden = true;
  });
  chatTab.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragDepth = 0; $("#dropVeil").hidden = true;
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      await uploadFiles(e.dataTransfer.files);
    }
  });
  $("#chatList").addEventListener("click", (e) => {
    const fc = e.target.closest(".file-card");
    if (fc && fc.dataset.fid) { openFile(fc.dataset.fid); return; }
    const copy = e.target.closest("[data-act='copy']");
    if (copy) {
      const m = S.chats.find(x => x.id === copy.dataset.mid);
      if (m) navigator.clipboard?.writeText(m.text).then(() => toast(T("已复制"), ""));
      return;
    }
    // ★ 需求 3 / 5：一键把小玉请过来 —— 服务端切人格，前端把原问题再问一次
    const ho = e.target.closest("[data-act='handoff']");
    if (ho) { doHandoff(ho.dataset.pid, ho.dataset.mid, ho); return; }
    const dis = e.target.closest("[data-act='dismiss-route']");
    if (dis) {
      const card = dis.closest(".route-card");
      if (card) card.classList.add("dismissed");
      return;
    }
    // ★ 工作台结论：点小标去工作台回放那一次委托
    const ac = e.target.closest(".agent-chip");
    if (ac && ac.dataset.run) {
      if (typeof Workbench !== "undefined" && Workbench.showRun) Workbench.showRun(ac.dataset.run);
      return;
    }
    // ★ M2：把这条委托带进工作台（转交只换人，真动手要显式交工作区）
    const wb = e.target.closest("[data-act='workbench']");
    if (wb) {
      const m = S.chats.find(x => x.id === wb.dataset.mid);
      if (typeof Workbench !== "undefined" && Workbench.openWithGoal) {
        Workbench.openWithGoal((m && m.text) || "");
      }
      return;
    }
  });
  $("#quickChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) { input.value = chip.dataset.text; sendMessage(chip.dataset.text); }
  });
  $("#btnClearChat").addEventListener("click", async () => {
    const ask = IS_GUEST ? T("清空当前对话？") : T("清空当前对话记录？（长期记忆会保留）");
    if (S.chats.length && confirm(ask)) {
      S = await api("/api/chat/clear", { method: "POST" });   // 只清当前这段（其它历史对话原样保留）
      renderAll();
      toast(T("已清空"), IS_GUEST ? T("对话已重置") : T("这段对话已清空，其它历史对话与记忆都还在"));
    }
  });

  $("#memoryForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (IS_GUEST) { toast(T("访客模式"), T("记忆功能未开启，注册后即可使用"), true); return; }
    const text = $("#memoryText").value.trim();
    if (!text) return;
    S = await api("/api/memories", { method: "POST", body: JSON.stringify({ text, type: $("#memoryType").value }) });
    $("#memoryText").value = "";
    renderMemories(); renderGrowth();
    toast(T("🧠 已添加"), T("这条记忆已写入"));
  });
  $("#memoryList").addEventListener("click", async (e) => {
    const card = e.target.closest(".card[data-id]");
    if (!card) return;
    const op = e.target.closest("[data-op]")?.dataset.op;
    const id = card.dataset.id;
    try {
      if (op === "pin") S = await api(`/api/memories/${id}`, { method: "PATCH", body: JSON.stringify({ pinned: !card.classList.contains("pinned") }) });
      if (op === "del") S = await api(`/api/memories/${id}`, { method: "DELETE" });
      if (op) { renderMemories(); renderGrowth(); }
    } catch (err) { toast(T("操作失败"), err.message, true); }
  });

  /* ---------- 历史对话：新建 / 切换 / 重命名 / 删除 ---------- */
  const btnNew = $("#btnNewConv");
  if (btnNew) btnNew.addEventListener("click", newConversation);
  const convSearch = $("#convSearch");
  if (convSearch) convSearch.addEventListener("input", renderConversations);

  const convList = $("#convList");
  if (convList) {
    convList.addEventListener("click", (e) => {
      const card = e.target.closest(".conv-item[data-cid]");
      if (!card) return;
      const cid = card.dataset.cid;
      const op = e.target.closest("[data-op]")?.dataset.op;
      const title = convTitle((S.conversations || []).find(c => c.id === cid) || {});
      if (op === "rename") { editingConv = cid; renderConversations(true); return; }
      if (op === "del") { deleteConversation(cid, title); return; }
      switchConversation(cid);
    });

    // 行内重命名：回车保存、Esc 取消、失焦保存（用 keydown/keyup 而不是 prompt()——
    // 桌面壳的 WebView2 对 window.prompt 支持不确定，不依赖它）
    convList.addEventListener("keydown", (e) => {
      const input = e.target.closest(".conv-edit");
      if (!input) return;
      const cid = input.closest(".conv-item").dataset.cid;
      if (e.key === "Enter") { e.preventDefault(); renameConversation(cid, input.value.trim()); }
      else if (e.key === "Escape") { e.preventDefault(); editingConv = null; renderConversations(); }
    });
    convList.addEventListener("focusout", (e) => {
      const input = e.target.closest(".conv-edit");
      const editing = editingConv;              // 抓一份快照：异步期间 editingConv 可能已被清空
      if (!input || editing === null) return;
      const cid = input.closest(".conv-item").dataset.cid;
      const v = input.value.trim();
      const cur = convTitle((S.conversations || []).find(c => c.id === cid) || {});
      if (!v) { editingConv = null; renderConversations(); return; }   // 空名 = 放弃修改
      if (v === cur) { editingConv = null; renderConversations(); return; }  // 没改动就别发请求
      renameConversation(cid, v);
    });
  }

  $("#todoForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#todoText").value.trim();
    if (!text) return;
    const dueStr = $("#todoDue").value;
    S = await api("/api/todos", { method: "POST", body: JSON.stringify({ text, due: dueStr ? new Date(dueStr).getTime() : null }) });
    $("#todoText").value = ""; $("#todoDue").value = "";
    renderTodos(); renderGrowth();
    requestNotifyPermission();
    toast(T("✅ 已添加"), dueStr
      ? T("将在 {time} 提醒你", { time: fmtDT(new Date(dueStr).getTime()) })
      : T("记得常回来看看"));
  });
  $("#todoList").addEventListener("click", async (e) => {
    const card = e.target.closest(".card[data-id]");
    if (!card) return;
    const op = e.target.closest("[data-op]")?.dataset.op;
    const id = card.dataset.id;
    try {
      if (op === "toggle") {
        S = await api(`/api/todos/${id}`, { method: "PATCH", body: JSON.stringify({ done: !card.classList.contains("done") }) });
        if (!card.classList.contains("done")) { const t = S.todos.find(x => x.id === id); if (t) toast(T("🎉 完成一件"), t.text); }
      }
      if (op === "del") S = await api(`/api/todos/${id}`, { method: "DELETE" });
      if (op) { renderTodos(); renderGrowth(); }
    } catch (err) { toast(T("操作失败"), err.message, true); }
  });
  $(".todo-filters").addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-btn");
    if (!btn) return;
    $$(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    todoFilter = btn.dataset.filter;
    renderTodos();
  });

  $("#btnSaveSettings").addEventListener("click", saveSettings);
  $("#btnLogout").addEventListener("click", doLogout);
  // 顶边栏人格切换：组件只上报 onSwitch，具体切换 + 主题联动交给 switchPersona
  PersonaSwitcher.mount($("#personaSwitcher"), {
    personas: (S && S.personas) || [],
    currentPersona: S && S.settings.personaId,
    themes: (S && S.personaThemes) || {},
    onSwitch: switchPersona
  });
  // 里程碑：点击已解锁徽章可回放弹窗
  $("#badgeList").addEventListener("click", (e) => {
    const card = e.target.closest(".badge");
    if (!card || !S) return;
    const b = S.growth.badges.find(x => x.id === card.dataset.bid);
    if (!b) return;
    if (b.got) popBadge(b, 0);
    else toast(T("尚未解锁"), T("达成条件：{hint}", { hint: b.hint }));
  });
  // 里程碑：预览解锁弹窗（只能预览已解锁的，不写库、不改进度）
  let previewIdx = 0;
  $("#btnPreviewBadge").addEventListener("click", async () => {
    const got = ((S && S.growth && S.growth.badges) || []).filter(b => b.got);
    if (!got.length) {
      toast(T("还没解锁任何里程碑"), T("先聊两句、记点东西，攒下第一枚再来回放"));
      return;
    }
    const b = got[previewIdx++ % got.length];
    try {
      const r = await fetch("/api/badges/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ id: b.id })
      });
      const data = await r.json();
      if (!r.ok) { toast(T("无法预览"), data.error || T("该里程碑尚未解锁")); return; }
      popBadge(data.badge, 0);
    } catch (e) { popBadge(b, 0); }
  });
  // 访客 → 注册/登录
  ["#btnGuestUpgrade", "#btnGuestUpgrade1", "#btnGuestUpgrade2"].forEach(sel => {
    const el = $(sel);
    if (el) el.addEventListener("click", leaveGuest);
  });
  $("#btnExport").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/export", { headers: authHeaders() });
      if (!res.ok) throw new Error(T("导出失败 ") + res.status);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `zhiban-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    } catch (err) { toast(T("导出失败"), err.message, true); }
  });
  $("#btnImport").addEventListener("click", () => $("#importFile").click());
  $("#importFile").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (!data.settings || !Array.isArray(data.chats)) throw new Error(T("格式不对"));
      if (!confirm(T("导入会覆盖当前所有数据，确定？"))) return;
      S = await api("/api/import", { method: "POST", body: JSON.stringify(data) });
      renderAll();
      toast(T("✅ 导入成功"), T("数据已恢复"));
    } catch (err) { toast(T("导入失败"), err.message, true); }
    e.target.value = "";
  });
  // ★ 清空：默认只清「对话 / 记忆 / 待办 / 成长」，**接口设置保留**（清完不用重新填 Key）。
  //   想连 Key 一起清，就在数据管理里勾那个框 —— 确认弹窗会照着实际范围说清楚，
  //   不再出现「文案说清数据、实际把 Key 也清了」这种事。
  $("#btnReset").addEventListener("click", async () => {
    const wipe = !!($("#setResetWipeKeys") || {}).checked;
    const msg = wipe
      ? T("确定清空所有数据（对话、记忆、待办、成长，以及接口设置 API Key / 模型）？此操作不可恢复！")
      : T("确定清空对话、记忆、待办与成长？接口设置（API Key / 模型 / 称呼）会保留。此操作不可恢复！");
    if (!confirm(msg)) return;
    if (!confirm(T("再次确认：真的全部清空？"))) return;
    S = await api("/api/reset", { method: "POST", body: JSON.stringify({ wipeSettings: wipe }) });
    renderAll();
    toast(T("已重置"), wipe ? T("一切从头开始") : T("对话与记忆已清空，接口设置已保留"));
  });

  // 左下角新入口：账号 / 接口 / 帮助 / 头像（折叠面板）
  bindSideBottom();

  // 右下角 Q 版悬浮小人：组件只负责渲染与按压反馈，点击行为在这里（回到对话 + 随机台词 + 聚焦输入）
  FloatingMascot.mount($("#floatingMascot"), {
    personaId: "gintoki",
    onClick: () => {
      switchTab("chat");
      const line = FloatingMascot.randomLine();
      if (line) FloatingMascot.say(line, 3200);
      const inp = $("#chatInput");
      if (inp) setTimeout(() => inp.focus(), 120);
    }
  });
}

/* ---------- 左下角入口（账户 / 接口 / 帮助 / 头像） ---------- */
function updateAccountWidget() {
  // 访客的 username 是后端固定占位「访客」，不是用户自取的昵称 → 按界面语言显示
  const name = (ME && ME.username && !ME.guest) ? ME.username : T("访客");
  const el = $("#acctName"); if (el) el.textContent = name;   // name 已按语言取好（见上方）
  const img = $("#acctAvatar"), def = $("#acctAvatarDef");
  const av = (S && S.settings && S.settings.avatar) || "";
  // 没有自定义头像时，用「当前人格」的配置头像（不再是写死的默认图）
  const personaAv = `<img class="avatar-img" src="${esc(PersonaThemes.avatar(S && S.settings && S.settings.personaId))}" alt="">`;
  if (av) { if (img) { img.src = av; img.hidden = false; } if (def) def.hidden = true; }
  else {
    if (img) img.hidden = true;
    if (def) { def.hidden = false; def.innerHTML = personaAv; }
  }
  const sw = $("#btnAcctSwitch");
  if (sw) sw.textContent = IS_GUEST ? T("登录 / 注册") : T("账号设置");
  // 紧凑触发图标（默认态唯一可见元素）：有头像显示头像，否则显示当前人格头像
  const tImg = $("#triggerAvatar"), tEm = $("#triggerEmoji");
  if (tImg && tEm) {
    if (av) { tImg.src = av; tImg.hidden = false; tEm.hidden = true; }
    else { tImg.hidden = true; tEm.hidden = false; tEm.innerHTML = personaAv; }
  }
  // 未登录（访客）红点提示
  const dot = $("#sideDot"); if (dot) dot.hidden = !IS_GUEST;
}
function fileToDataURL(file, maxPx) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(T("读取失败")));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error(T("图片解析失败")));
      img.onload = () => {
        const scale = Math.min(1, maxPx / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale)), h = Math.max(1, Math.round(img.height * scale));
        const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
        cv.getContext("2d").drawImage(img, 0, 0, w, h);
        resolve(cv.toDataURL("image/jpeg", 0.85));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}
async function onAvatarPicked(file) {
  if (!file) return;
  try {
    const dataUrl = await fileToDataURL(file, 256);
    S = await api("/api/settings", { method: "PUT", body: JSON.stringify({ avatar: dataUrl }) });
    updateAccountWidget();
    renderChat();
    toast(T("头像已更新"), IS_GUEST ? T("访客模式下仅本次有效，注册后长期保存") : T("头像已保存到你的账号"));
  } catch (err) { toast(T("头像上传失败"), err.message, true); }
}
function bindSideBottom() {
  const av = $("#btnAvatar"); if (av) av.addEventListener("click", () => { const i = $("#avatarInput"); if (i) i.click(); });
  const ai = $("#avatarInput"); if (ai) ai.addEventListener("change", (e) => { onAvatarPicked(e.target.files[0]); e.target.value = ""; });
  const sw = $("#btnAcctSwitch"); if (sw) sw.addEventListener("click", () => { if (IS_GUEST) showGate(); else openSettings("account"); });
  // 「设置」入口（原侧栏导航项，已并入本面板）
  const os = $("#btnOpenSettings"); if (os) os.addEventListener("click", () => openSettings("general"));
  const ap = $("#btnApiConfig"); if (ap) ap.addEventListener("click", () => openSettings("account"));
  const hb = $("#btnHelp"); if (hb) hb.addEventListener("click", () => { const m = $("#helpModal"); if (m) m.hidden = false; });
  const hc = $("#btnHelpClose"); if (hc) hc.addEventListener("click", () => { const m = $("#helpModal"); if (m) m.hidden = true; });
  const lc = $("#lvChip"); if (lc) lc.addEventListener("click", () => switchTab("growth"));

  // 扩展式折叠面板：点击触发图标向上展开/收起，点击面板内部或页面其它处收起
  const trigger = $("#sideTrigger"), pop = $("#sidePop");
  if (!trigger || !pop) return;
  const setOpen = (open) => {
    pop.classList.toggle("open", open);
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
  };
  trigger.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!pop.classList.contains("open")); });
  pop.addEventListener("click", (e) => { e.stopPropagation(); setOpen(false); });   // 面板内操作完即收起
  document.addEventListener("click", () => setOpen(false));                         // 点击外部收起
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });

  // 设置模态弹窗：开/关/导航/外观切换
  const sm = $("#settingsModal");
  if (sm) {
    const sc = $("#btnSettingsClose"), sx = $("#btnSettingsCancel");
    if (sc) sc.addEventListener("click", closeSettings);
    if (sx) sx.addEventListener("click", closeSettings);
    sm.addEventListener("click", (e) => { if (e.target === sm) closeSettings(); });   // 点遮罩关闭
    $$("#settingsNav .sm-nav-item").forEach(b =>
      b.addEventListener("click", () => setSettingsSection(b.dataset.section)));
    // 外观主题三卡片：即时预览（不保存也看得到效果），保存时随设置一起落库
    $$("#uiModeCards .ui-mode-card").forEach(c => c.addEventListener("click", () => {
      $$("#uiModeCards .ui-mode-card").forEach(x => x.classList.toggle("active", x === c));
      setUiMode(c.dataset.mode);
    }));
    // 语言：改一次就生效并落库，不需要按保存
    const langSel = $("#setLang");
    if (langSel) langSel.addEventListener("change", () => setLangPref(langSel.value));
  }
}

/* ---------- 静态界面图标（单色线性，统一风格；颜色跟随 currentColor） ---------- */
function setStaticIcons() {
  document.querySelectorAll(".nav-ico[data-ico]").forEach(el => {
    el.innerHTML = PERSONA_MARKS.glyph(el.dataset.ico, 21);
  });
  const si = $("#sendIco");
  if (si) si.innerHTML = PERSONA_MARKS.glyph("send", 19);
}

/* ---------- 启动 ---------- */
(async function init() {
  PERSONA_MARKS.installDefs();   // 先把角色标志物的共用渐变注入文档
  await I18N.load();             // 语言包要在首次渲染之前就位，避免中英闪一下
  setStaticIcons();
  bindGate();
  if (TOKEN) {
    try { if (await enterApp()) return; } catch (e) { /* token 失效，回退访客 */ }
  }
  await becomeGuest(true);   // 默认以访客身份直接进入主界面（silent=true 不弹提示）
})();
