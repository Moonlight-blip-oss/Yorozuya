/* ============================================================
   Yorozuya · 工作台（Workbench）
   ------------------------------------------------------------
   M2 的前端：右侧抽屉 = 运行记录 / 工作区 / 成本，运行中的**五种工作卡片**
   （计划 · 工具 · 命令输出 · 审批 · diff）都在这里实时长出来。

   与陪伴层的关系（刻意保持的边界）：
   · 本文件是**只读展示 + 触发**，一行都不碰 `app.js` 的聊天链路；
     复用的全是它的全局设施（`api()` / `toast()` / `esc()` / `T()` / `authHeaders()`）。
   · 事件流是**可续传**的：内核把每条事件落库并带单调 `seq`，
     所以页面刷新 / 流断掉之后，用 `?after=<seq>` 就能把缺口补齐 —— 这是 M2 的技术要点。

   为什么要「回放」与「续传」分开：
   · 续传 = 这次运行还没结束，断线后接上，仍然可以审批 / 停止；
   · 回放 = 运行已经结束，把历史事件按顺序再演一遍（只读，按钮不可点）。
   ============================================================ */
"use strict";

const Workbench = (() => {
  /* ---------------- 小工具 ---------------- */
  const q = (sel, root) => (root || document).querySelector(sel);
  const qa = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const auth = () => (typeof authHeaders === "function" ? authHeaders() : {});
  const RUN_KEY = "yorouya-agent-run";        // {runId, lastSeq, at}：刷新后续传用
  const GOAL_KEY = "yorouya-agent-goals";     // 最近用过的委托目标（最多 8 条）
  const TOCHAT_KEY = "yorouya-agent-tochat";  // 「跑完把结论发到对话里」这个开关
  const WS_KEY = "yorouya-agent-ws";          // 上次用的工作区（下次默认选中它）
  const TMO_KEY = "yorouya-agent-timeout";    // 上次选的审批超时时间
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* 隐私模式下写不了，忽略 */ } };

  async function apiJson(path, opts) {
    const r = await fetch(path, { ...(opts || {}),
      headers: { "Content-Type": "application/json", ...auth(), ...((opts || {}).headers || {}) } });
    if (r.status === 401) throw new Error(T("未登录或登录已过期"));
    const txt = await r.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = { raw: txt }; }
    if (!r.ok) throw new Error((data && (data.error || data.detail)) || ("HTTP " + r.status));
    return data;
  }

  const fmtTok = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));
  const fmtSec = (s) => (s >= 60 ? Math.floor(s / 60) + "m" + Math.round(s % 60) + "s" : (s || 0) + "s");
  const fmtWhen = (ms) => {
    if (!ms) return "—";
    const d = new Date(ms);
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const STATUS_TXT = {
    done: "完成", verify_failed: "验证未通过", unverified: "未验证", budget: "触顶收尾",
    stopped: "已停止", error: "出错", running: "运行中", blocked: "被拦下",
  };
  const statusText = (s) => T(STATUS_TXT[s] || s || "未知");

  /* ---------------- 状态 ---------------- */
  const ST = {
    open: false,
    tab: "run",
    status: null,        // /api/agent/status
    prices: { in: 0, out: 0, currency: "CNY" },
    stats: null,
    runs: [],
    workspaces: [],
    run: null,           // 当前正在看/正在跑的那次
    timer: null,         // 续传轮询
    replay: null,        // 回放定时器
    stick: true,         // 卡片流是否跟随最新（用户自己往上翻时会变 false，与聊天页同一口径）
    todos: null,         // 待办清单（自己从 /api/todos 拉，见 refreshTodoState）
    todoFilter: "open",  // 未完成 / 已完成 / 全部
    pendingTodo: null,   // 这次委托是「从哪条待办」派出去的：{id, text}
    unseen: 0,           // 跑完但还没看的次数（侧栏角标）
    runSearch: "",       // 运行记录：搜索词
    runStatus: "all",    // 运行记录：状态筛选
    pendingChat: false,  // 有结论刚发进对话、但当时没刷新（切回对话页时补刷）
  };

  /* 当前运行对象 */
  function newRun(id) {
    return { id, lastSeq: 0, seen: new Set(), live: false, replaying: false, done: false,
             approval: null, approvalTimeout: 120, demo: false, steps: {}, plan: [],
             files: {},                    // path → {created, adds, dels}：结果摘要卡要用
             wsName: "",                   // 这次委托跑在哪个工作区（摘要卡上显示）
             startedAt: Date.now(), streamEl: null, status: "running" };
  }

  /* ---------------- 外壳：只建一次，页面 / 抽屉按需挂载 ----------------
     为什么不做两份 DOM：
       ① id 会重复（非法），querySelector 会拿到错的那个；
       ② 运行状态、卡片流、审批倒计时都要维护两份，迟早不一致。
     做法：外壳只建一次，`mountWhere()` 决定它挂在**左侧「工作台」页面**里，
     还是挂在**右侧抽屉**里 —— 同一个节点搬来搬去，不是复制。
       · 页面：全宽，适合长内容（卡片流 / diff / 成本表）；
       · 抽屉：从对话页随手拉出来看一眼，不切走当前页。
     两者状态天然一致：跑到一半从页面收进抽屉，卡片一张不少。 */
  const SHELL = `
  <header class="wb-head">
    <div class="wb-title-wrap">
      <span class="wb-ico" data-ico="navWorkbench"></span>
      <h2 id="wbTitle" data-i18n="工作台">工作台</h2>
      <span class="wb-demo" id="wbDemo" hidden data-i18n="演示模式">演示模式</span>
    </div>
    <div class="wb-head-ops">
      <span class="wb-ver" id="wbVer"></span>
      <span class="wb-live" id="wbLive" hidden><i></i><span data-i18n="运行中">运行中</span></span>
      <button class="ghost-btn slim" id="wbRefresh" type="button" data-i18n="刷新">刷新</button>
      <button class="icon-btn" id="wbClose" type="button" aria-label="关闭" data-i18n-aria="关闭">×</button>
    </div>
  </header>
  <nav class="wb-tabs" aria-label="工作台分区" data-i18n-aria="工作台分区">
    <button class="wb-tab active" type="button" data-wbtab="run">
      <span data-i18n="委托">委托</span><i class="wb-tab-dot" id="wbTabRunDot" hidden></i>
    </button>
    <button class="wb-tab" type="button" data-wbtab="todo">
      <span data-i18n="待办">待办</span><span class="wb-tab-badge" id="wbTabTodoBadge" hidden>0</span>
    </button>
    <button class="wb-tab" type="button" data-wbtab="runs" data-i18n="运行记录">运行记录</button>
    <button class="wb-tab" type="button" data-wbtab="ws" data-i18n="工作区">工作区</button>
    <button class="wb-tab" type="button" data-wbtab="cost" data-i18n="成本">成本</button>
  </nav>
  <div class="wb-body">
    <!-- ① 发起委托 + 运行时的事件流（五种工作卡片都长在这里） -->
    <section class="wb-pane active" id="wbPaneRun">
      <div class="wb-form">
        <label class="wb-field">
          <span data-i18n="要它做什么">要它做什么</span>
          <textarea id="wbGoal" rows="3" maxlength="1000" placeholder="例如：check_calc.py 跑不过，帮我定位并修好，最后再跑一次确认输出 ALL-OK" data-i18n-ph="例如：check_calc.py 跑不过，帮我定位并修好，最后再跑一次确认输出 ALL-OK"></textarea>
        </label>
        <!-- 「这条委托是从哪条待办派出去的」：跑完验证通过会自动把它勾掉 -->
        <div class="wb-pending" id="wbPending" hidden>
          <span class="wb-pending-ico">🛠</span>
          <span class="wb-pending-txt" id="wbPendingTxt"></span>
          <button class="icon-btn" id="wbPendingClear" type="button" title="不再关联这条待办" data-i18n-title="不再关联这条待办" aria-label="不再关联这条待办" data-i18n-aria="不再关联这条待办">×</button>
        </div>
        <!-- 常用委托模板：一句话就能开工。第一条是只读的 —— 不需要任何审批，最省事 -->
        <div class="wb-tpl" id="wbTpl"></div>
        <!-- 最近用过的目标：同一件事要反复跑的时候不用重打 -->
        <div class="wb-goals" id="wbGoals" hidden></div>
        <div class="wb-form-row">
          <label class="wb-field">
            <span data-i18n="工作区">工作区</span>
            <select id="wbWs"></select>
          </label>
          <label class="wb-field wb-field-sm">
            <span data-i18n="审批超时">审批超时</span>
            <select id="wbTimeout">
              <option value="60">60s</option>
              <option value="120" selected>120s</option>
              <option value="300">300s</option>
            </select>
          </label>
        </div>
        <label class="switch-row wb-switch"><span data-i18n="自动批准（跳过审批卡片，风险自担）">自动批准（跳过审批卡片，风险自担）</span><input type="checkbox" id="wbAuto"></label>
        <p class="wb-auto-warn" id="wbAutoWarn" hidden data-i18n="⚠️ 自动批准已开启：写文件、跑命令都不会再停下来问你，直接执行。"></p>
        <label class="switch-row wb-switch"><span data-i18n="跑完把结论发到对话里">跑完把结论发到对话里</span><input type="checkbox" id="wbToChat" checked></label>
        <p class="wb-tip" id="wbDemoTip" hidden></p>
        <p class="wb-tip" id="wbNoWs" hidden data-i18n="还没有登记工作区：切到「工作区」页填一个项目目录。"></p>
        <div class="wb-form-ops">
          <button class="primary-btn" id="wbStart" type="button" data-i18n="预览并开始">预览并开始</button>
          <button class="danger-btn" id="wbStop" type="button" hidden data-i18n="停止">停止</button>
          <span class="wb-hint" data-i18n="Ctrl/⌘ + Enter 直接开始">Ctrl/⌘ + Enter 直接开始</span>
        </div>
      </div>
      <div class="wb-stream" id="wbStream"></div>
    </section>
    <!-- ①b 待办：在工作台里就能看待办、把某条直接派给小玉 -->
    <section class="wb-pane" id="wbPaneTodo">
      <div class="wb-form">
        <form class="wb-todo-add" id="wbTodoAdd">
          <input type="text" id="wbTodoText" maxlength="200" placeholder="要做的事…（比如：把 calc.py 的边界判断补上）" data-i18n-ph="要做的事…（比如：把 calc.py 的边界判断补上）">
          <button class="primary-btn slim" type="submit" data-i18n="添加">添加</button>
        </form>
        <p class="wb-tip" data-i18n-html="点「交给小玉做」会把这条待办的目标填进委托框；跑完<b>验证通过</b>就自动勾掉它 —— 没验证的不替你下结论。"></p>
        <p class="wb-tip" id="wbTodoGuest" hidden data-i18n="访客模式可以加待办，但不会保存（工程委托本身也需要登录）。"></p>
      </div>
      <div class="wb-todo-filters" id="wbTodoFilters">
        <button class="filter-btn active" type="button" data-todof="open" data-i18n="未完成">未完成</button>
        <button class="filter-btn" type="button" data-todof="done" data-i18n="已完成">已完成</button>
        <button class="filter-btn" type="button" data-todof="all" data-i18n="全部">全部</button>
      </div>
      <div class="wb-list" id="wbTodoList"></div>
    </section>
    <!-- ② 运行记录：点开即回放；可搜索 / 筛选 / 再跑一次 -->
    <section class="wb-pane" id="wbPaneRuns">
      <div class="wb-runs-bar">
        <input type="search" id="wbRunSearch" maxlength="60" placeholder="搜目标或工作区" data-i18n-ph="搜目标或工作区">
        <div class="wb-run-filters" id="wbRunFilters">
          <button class="filter-btn active" type="button" data-runst="all" data-i18n="全部">全部</button>
          <button class="filter-btn" type="button" data-runst="ok" data-i18n="已完成">已完成</button>
          <button class="filter-btn" type="button" data-runst="bad" data-i18n="没成功">没成功</button>
          <button class="filter-btn" type="button" data-runst="todo" data-i18n="从待办来的">从待办来的</button>
        </div>
        <span class="wb-runs-count" id="wbRunsCount"></span>
        <!-- 删除**当前看到的这批**（所见即所删）：前端把 id 数组发过去，服务端不重新算筛选 -->
        <button class="danger-btn slim" id="wbRunsPurge" type="button" hidden
                title="删掉下面列出的这些记录（含它们在项目里的留档文件夹）"
                data-i18n="清空这些">清空这些</button>
      </div>
      <div class="wb-list" id="wbRunList"></div>
    </section>
    <!-- ③ 工作区：登记 / 切换 / 看 git 状态 -->
    <section class="wb-pane" id="wbPaneWs">
      <div class="wb-form">
        <label class="wb-field">
          <span data-i18n="登记一个项目目录">登记一个项目目录</span>
          <div class="wb-path-row">
            <input type="text" id="wbWsPath" placeholder="F:\\code\\my-project" maxlength="300">
            <!-- 「浏览…」只在桌面版（有原生窗口）里出现：走 pywebview 的原生目录选择器。
                 浏览器里没有这个能力（网页拿不到绝对路径），所以那时按钮隐藏、只留手输。 -->
            <button class="ghost-btn slim" id="wbWsPick" type="button" hidden
                    data-i18n-title="在资源管理器里选一个目录" title="在资源管理器里选一个目录"
                    data-i18n="浏览…">浏览…</button>
          </div>
        </label>
        <div class="wb-form-ops">
          <button class="primary-btn slim" id="wbWsAdd" type="button" data-i18n="登记工作区">登记工作区</button>
          <span class="wb-hint" data-i18n="只登记 + 写一份 agent.config.json / AGENT.md，不跑任何命令">只登记 + 写一份 agent.config.json / AGENT.md，不跑任何命令</span>
        </div>
      </div>
      <div class="wb-list" id="wbWsList"></div>
    </section>
    <!-- ④ 成本与步数仪表 -->
    <section class="wb-pane" id="wbPaneCost"><div class="wb-list" id="wbCostBody"></div></section>
  </div>`;

  let shell = null;
  let mountMode = "";                  // "page" | "drawer"

  const hostOf = (mode) => q(`[data-wb-host="${mode}"]`);

  /* 兜底：如果我们被一份**旧的 index.html** 加载（浏览器/WebView2 缓存、或换包换一半），
     页面挂载点和侧栏入口都不存在 —— 那就自己把它们建出来，别让用户点进一片空白。
     （exe 是整包快照，这种情况帮不了；但只要前端文件是新的，这条路就一定能走通。） */
  function ensureNavItem() {
    const nav = q("#nav");
    if (!nav || q('#nav .nav-btn[data-tab="workbench"]')) return;
    const b = document.createElement("button");
    b.className = "nav-btn";
    b.type = "button";
    b.dataset.tab = "workbench";
    b.title = T("工作台：把活交给小玉（读文件 / 改文件 / 跑测试）");
    b.innerHTML = '<span class="nav-ico"></span><span class="nav-label"></span>';
    nav.appendChild(b);
    const ico = q(".nav-ico", b);
    if (ico && typeof PERSONA_MARKS !== "undefined") {
      ico.innerHTML = PERSONA_MARKS.glyph("navWorkbench", 18);
    }
    const lb = q(".nav-label", b);
    if (lb) lb.textContent = T("工作台");
    // 点击由 app.js 在 #nav 上的委托处理（它按 data-tab 调 switchTab），这里不重复绑
  }

  function ensureHost(mode) {
    let h = hostOf(mode);
    if (h) return h;
    if (mode === "drawer") {
      const box = q("#workbench");
      if (!box) return null;
      h = document.createElement("div");
      h.className = "wb-host";
      h.dataset.wbHost = "drawer";
      box.appendChild(h);
      return h;
    }
    const main = q("#main");
    if (!main) return null;
    const sec = document.createElement("section");
    sec.className = "tab";
    sec.id = "tab-workbench";
    h = document.createElement("div");
    h.className = "wb-host";
    h.dataset.wbHost = "page";
    sec.appendChild(h);
    main.insertBefore(sec, q("#settingsModal") || null);
    ensureNavItem();
    return h;
  }

  function ensureShell() {
    if (shell) return shell;
    shell = document.createElement("div");
    shell.className = "wb-shell";
    shell.innerHTML = SHELL;
    const page = ensureHost("page");
    (page || document.body).appendChild(shell);
    // 外壳是 JS 造的，所以静态图标与静态文案要在这里补一次
    const ico = q(".wb-ico", shell);
    if (ico && typeof PERSONA_MARKS !== "undefined") {
      ico.innerHTML = PERSONA_MARKS.glyph("navWorkbench", 16);
    }
    if (typeof I18N !== "undefined" && I18N.applyStatic) I18N.applyStatic(shell);
    mountMode = page ? "page" : "drawer";
    shell.dataset.wbMode = mountMode;
    renderVersion();
    attachFollow();          // 卡片流的「跟随最新」标志（只挂一次）
    return shell;
  }

  /** 构建号：一眼看出「你打开的是哪一版」——排查「界面还是旧的」这类问题时最有用 */
  function renderVersion() {
    const el = shell && q("#wbVer", shell);
    if (!el) return;
    const fe = (typeof S !== "undefined" && S && S.build) ? S.build : "—";
    const be = (ST.status && ST.status.build) ? ST.status.build : "—";
    el.textContent = T("前端 {v} · 内核 {k}", { v: fe, k: be });
    el.title = T("构建号：换包或刷新后应该变化；如果这里一直不变，说明界面还是旧的（缓存）");
  }

  /** 把外壳挂到页面或抽屉（同一个节点，搬而不是复制）；顺带保住滚动位置 */
  function mountWhere(mode) {
    const s = ensureShell();
    const dst = ensureHost(mode);
    if (!dst) return;
    const pane = q(".wb-pane.active", s);
    const stream = q("#wbStream", s);
    const keepPane = pane ? pane.scrollTop : 0;
    const keepStream = stream ? stream.scrollTop : 0;
    if (s.parentElement !== dst) dst.appendChild(s);
    s.dataset.wbMode = mode;
    mountMode = mode;
    if (pane) pane.scrollTop = keepPane;
    if (stream) stream.scrollTop = keepStream;
    const closeBtn = q("#wbClose", s);
    if (closeBtn) {
      const label = mode === "page" ? T("收起（回对话页）") : T("关闭");
      closeBtn.title = label;
      closeBtn.setAttribute("aria-label", label);
    }
  }

  /* ---------------- 打开 / 关闭 ---------------- */
  function isGuest() { return !!(ST.status && ST.status.needLogin); }

  /** 打开后统一刷一遍数据（页面与抽屉共用） */
  function onShown(prefillGoal) {
    if (prefillGoal) {
      const g = q("#wbGoal");
      if (g && !g.value.trim()) g.value = String(prefillGoal).slice(0, 1000);
      setTab("run");
    }
    clearUnseen();                       // 人已经看到工作台了，角标清零
    resetFollow();                       // 并把「跟随最新」复位：上一次翻到哪儿不该管住这一次
    refreshStatus().then(() => { renderWs(); renderCost(); });
    renderRuns();
    renderPendingTodo();
    renderGoalHistory();
    refreshTodoState();                  // 待办清单也跟着刷（可能刚被内核自动勾掉）
  }

  /** 快捷入口：拉到右侧抽屉（不切走当前页） */
  function open(prefillGoal) {
    const box = q("#workbench");
    if (!box) return;
    mountWhere("drawer");
    box.hidden = false;
    const sc = q("#wbScrim");
    if (sc) sc.hidden = false;
    document.body.classList.add("wb-open");
    ST.open = true;
    onShown(prefillGoal);
  }

  /** 页面入口：左侧「工作台」页（全宽；由 switchTab 切过去） */
  function showPage(prefillGoal) {
    const box = q("#workbench");
    if (box) box.hidden = true;
    const sc = q("#wbScrim");
    if (sc) sc.hidden = true;
    document.body.classList.remove("wb-open");
    ST.open = true;
    mountWhere("page");
    /* 整页模式从头看：抽屉里滚到哪是「窄栏里的位置」，换成 1240px 全宽后那个位置没有意义，
       带过来会让头部的「开始委托」落在视口上方（点了侧栏进来却看不到按钮）。
       顺带把跟随标志复位，否则 stick 会被这次 scroll 事件算成 false、后面卡片不再跟到底。 */
    const pane = streamScroller();
    if (pane && pane !== streamEl()) pane.scrollTop = 0;
    ST.stick = true;      // 见 onShown 里的 resetFollow：这里只负责把位置归零
    onShown(prefillGoal);
  }

  function close() {
    const box = q("#workbench");
    if (mountMode === "page") {
      // 页面模式下的「×」= 收起工作台回对话页；外壳归位到抽屉（隐藏），下次快捷入口即时可用
      if (typeof switchTab === "function") switchTab("chat");
      if (box) { mountWhere("drawer"); box.hidden = true; }
      ST.open = false;
      return;
    }
    if (box) box.hidden = true;
    const sc = q("#wbScrim");
    if (sc) sc.hidden = true;
    document.body.classList.remove("wb-open");
    ST.open = false;
  }

  const toggle = (goal) => (ST.open && mountMode === "drawer" ? close() : open(goal));

  /** app.js 的 switchTab 会通知这里：切到「工作台」页就把外壳挂过去 */
  function onTab(name) {
    if (name === "workbench") { showPage(); return; }
    if (mountMode === "page") ST.open = false;     // 离开页面：外壳留在页里（隐藏），状态不丢
    // 跑完的结论当时没能刷进对话（正在打字 / 没看对话页）→ 切回对话页时补一次
    if (name === "chat" && ST.pendingChat && !chatBusy()) {
      ST.pendingChat = false;
      try { if (typeof refreshState === "function") refreshState(); } catch (e) { /* 忽略 */ }
    }
  }

  function setTab(name) {
    ST.tab = name;
    const s = ensureShell();
    qa(".wb-tab", s).forEach((b) => b.classList.toggle("active", b.dataset.wbtab === name));
    qa(".wb-pane", s).forEach((p) => p.classList.toggle("active", p.id === "wbPane" + cap(name)));
    if (name === "runs") renderRuns();
    if (name === "ws") { refreshStatus().then(renderWs); }
    if (name === "cost") renderCost();
    if (name === "todo") renderTodoPane();
  }
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  /* ---------------- 状态刷新 ---------------- */
  async function refreshStatus() {
    try {
      ST.status = await apiJson("/api/agent/status");
      ST.workspaces = ST.status.workspaces || [];
      ST.runs = ST.status.runs || [];
      const dm = q("#wbDemo");
      if (dm) dm.hidden = !(ST.status.hasKey === false && !ST.status.needLogin);
      renderRunForm();
      renderVersion();
    } catch (e) {
      /* 状态拿不到（比如没登录）：不弹窗，面板自己会显示提示 */
      ST.status = ST.status || { needLogin: true };
    }
    return ST.status;
  }

  /* ---------------- 运行面板：表单 ---------------- */
  function renderRunForm() {
    const sel = q("#wbWs");
    if (!sel) return;
    const cur = sel.value;
    const list = ST.workspaces || [];
    const demoOn = ST.status && ST.status.hasKey === false && !ST.status.needLogin;
    sel.innerHTML = list.length
      ? list.map((w) => `<option value="${esc(w.id)}">${esc(w.name)}${w.exists ? "" : T("（目录已不存在）")}</option>`).join("")
      : `<option value="">${T("还没有工作区")}</option>`;
    if (cur && list.some((w) => String(w.id) === cur)) sel.value = cur;
    else {
      // 没显式选过就用「上次用过的那个工作区」——同一段时间通常都在同一个项目上干活
      const last = Number(lsGet(WS_KEY, 0)) || 0;
      if (last && list.some((w) => Number(w.id) === last)) sel.value = String(last);
    }
    sel.disabled = !!demoOn || !list.length;
    const tip = q("#wbDemoTip");
    if (tip) {
      tip.hidden = !demoOn;
      tip.textContent = demoOn
        ? T("未配置 API Key：本次由内核按固定脚本演示，只在专用演示工作区里跑，不会碰你登记的真实项目。配上 Key 后同样的卡片流由真实模型驱动。")
        : "";
    }
    const hint = q("#wbNoWs");
    if (hint) hint.hidden = !!list.length || !!demoOn;
  }

  /* ---------------- 待办直通（工作台里把待办派给小玉）----------------
     数据从哪来：`/api/todos` 单独一条窄接口。为什么不复用陪伴层的整份 `S`：
       · 待办页的数据里混着 500 条聊天消息，为了刷新一个待办清单把它们全拉一遍不值；
       · `S = await api("/api/state")` 是**整个替换**，聊天正在流式打字时那一下会闪。
     所以这里只改 `S.todos` 这一个字段（对象身份不变），再让陪伴层自己重画待办页与侧栏角标。 */

  async function refreshTodoState() {
    try {
      const r = await apiJson("/api/todos");
      ST.todos = (r && r.todos) || [];
    } catch (e) {
      ST.todos = ST.todos || [];
    }
    // 把结果同步给陪伴层的全局状态（供侧栏角标 / 待办页用），别整份替换
    try {
      if (typeof S !== "undefined" && S) S.todos = ST.todos;
      if (typeof renderTodos === "function") renderTodos();
    } catch (e) { /* 陪伴层还没初始化完就算了 */ }
    renderTodoPane();
    setNavBadge();
  }

  /** 待办清单的一份**本地副本**（`S.todos` 是权威，但工作台可能先于它加载） */
  function todosNow() {
    if (typeof S !== "undefined" && S && Array.isArray(S.todos)) return S.todos;
    return ST.todos || [];
  }

  async function renderTodoPane() {
    const box = q("#wbTodoList");
    if (!box) return;
    const guest = q("#wbTodoGuest");
    if (guest) guest.hidden = !isGuest();
    if (ST.todos === null) {
      // 陪伴层已经把待办拉回来了就直接用，别为了同一份数据再发一次请求
      if (typeof S !== "undefined" && S && Array.isArray(S.todos)) ST.todos = S.todos;
      else await refreshTodoState();
    }
    if (!q("#wbTodoList")) return;              // 等待期间外壳被搬走/销毁了
    const all = todosNow();
    const f = ST.todoFilter;
    const list = all.filter((t) => (f === "open" ? !t.done : f === "done" ? t.done : true));
    qa("#wbTodoFilters .filter-btn", shell).forEach((b) =>
      b.classList.toggle("active", b.dataset.todof === f));
    if (!list.length) {
      box.innerHTML = `<div class="wb-empty">${f === "open"
        ? T("没有未完成的待办。上面写一条，比如「把 calc.py 的边界判断补上」。")
        : T("这里空空如也。")}</div>`;
      return;
    }
    const now = Date.now();
    box.innerHTML = list.map((t) => {
      let due = "";
      if (t.due) {
        const diff = t.due - now;
        if (t.done) due = T("原定 {time}", { time: fmtWhen(t.due) });
        else if (diff < 0) due = T("已到期");
        else if (diff < 3600000) due = T("{n} 分钟后", { n: Math.max(1, Math.round(diff / 60000)) });
        else due = fmtWhen(t.due);
      }
      // 「这条待办派出去的最近一次运行」——跑没跑过、上次结果如何，一眼能看到
      const last = (ST.runs || []).find((r) => r.todoId && r.todoId === t.id);
      return `
      <div class="wb-todo ${t.done ? "done" : ""}" data-todo="${esc(t.id)}">
        <button class="todo-check ${t.done ? "on" : ""}" type="button" data-todo-act="toggle"
                title="${T("完成 / 取消完成")}" aria-label="${T("完成 / 取消完成")}">${t.done ? "✓" : ""}</button>
        <div class="wbt-body">
          <div class="wbt-text">${esc(t.text)}</div>
          <div class="wbt-meta">
            ${due ? `<span class="wbt-due">${esc(due)}</span>` : ""}
            ${last ? `<span class="wbr-status wbr-${esc(last.status)}">${T("上次")}：${esc(statusText(last.status))}</span>` : ""}
          </div>
        </div>
        <div class="wbr-ops">
          ${t.done ? "" : `<button class="primary-btn slim" type="button" data-todo-act="run">${T("交给小玉做")}</button>`}
          <button class="ghost-btn slim" type="button" data-todo-act="del">${T("删除")}</button>
        </div>
      </div>`;
    }).join("");
    qa("[data-todo]", box).forEach((el) => {
      const id = el.dataset.todo;
      qa("[data-todo-act]", el).forEach((b) => {
        b.onclick = () => onTodoAct(b.dataset.todoAct, id, b);
      });
    });
  }

  async function onTodoAct(act, id, btn) {
    const t = todosNow().find((x) => x.id === id) || {};
    if (act === "toggle") {
      btn.disabled = true;
      try {
        const r = await apiJson(`/api/todos/${encodeURIComponent(id)}`,
                                { method: "PATCH", body: JSON.stringify({ done: !t.done }) });
        if (r && Array.isArray(r.todos)) ST.todos = r.todos;
        syncTodoToApp();
        if (!t.done) toast(T("🎉 完成一件"), t.text || "");
      } catch (e) { toast(T("操作失败"), e.message, true); }
      btn.disabled = false;
      await refreshTodoState();
    } else if (act === "del") {
      if (!confirm(T("删掉这条待办？"))) return;
      try {
        await apiJson(`/api/todos/${encodeURIComponent(id)}`, { method: "DELETE" });
        await refreshTodoState();
      } catch (e) { toast(T("操作失败"), e.message, true); }
    } else if (act === "run") {
      delegateTodo(t);
    }
  }

  function syncTodoToApp() {
    try {
      if (typeof S !== "undefined" && S && ST.todos) S.todos = ST.todos;
      if (typeof renderTodos === "function") renderTodos();
    } catch (e) { /* 陪伴层还没就绪 */ }
  }

  /** ★ 「交给小玉做」：把待办目标填进委托框，并记住「这次是替哪条待办干活」 */
  function delegateTodo(t) {
    if (!t || !t.id) return;
    const g = q("#wbGoal");
    if (!g) return;
    const busy = g.value.trim();
    if (busy && !ST.pendingTodo && busy !== (t.text || "").trim()) {
      // 别无声覆盖她已经写了一半的目标
      if (!confirm(T("委托框里已经有内容了，要换成这条待办的目标吗？"))) return;
    }
    ST.pendingTodo = { id: t.id, text: t.text || "" };
    g.value = t.text || "";
    setTab("run");
    renderPendingTodo();
    const sel = q("#wbWs");
    if (sel && !sel.disabled) {
      const ws = ST.workspaces || [];
      if (ws.length === 1) sel.value = String(ws[0].id);
    }
    if (sel && sel.disabled) {
      toast(T("这条待办已经填进委托框"), T("但还没有可用工作区：先去「工作区」页登记一个项目目录"));
    } else {
      toast(T("已填进委托框"), T("点「开始委托」就开工；验证通过会自动勾掉这条待办"));
      g.focus();
    }
  }

  /** 委托框下面那条「来自待办：xxx」提示 */
  function renderPendingTodo() {
    const box = q("#wbPending");
    const txt = q("#wbPendingTxt");
    if (!box || !txt) return;
    const p = ST.pendingTodo;
    box.hidden = !p;
    if (!p) return;
    txt.textContent = T("来自待办：{text}（跑完验证通过会自动勾掉）", { text: p.text });
  }

  function clearPendingTodo() {
    ST.pendingTodo = null;
    renderPendingTodo();
  }

  /* ---------------- 常用委托模板（一句话开工） ----------------
     为什么要有：委托框空着的时候，用户不知道该写什么才算"一句好委托"。
     第一条刻意是**只读任务**（不需要任何审批）—— 想先看看 agent 靠不靠谱时最省事。 */
  const TPL = [
    { zh: "看一遍这个项目：它是做什么的、怎么跑起来、结构大概什么样。只读，不要改任何文件",
      en: "Read through this project: what it does, how to run it, roughly how it is structured. Read-only — do not change any file." },
    { zh: "跑一次项目里现有的测试，把失败的原因找出来并修好，最后再跑一遍确认全绿",
      en: "Run the project's existing tests, fix what fails, then run them again to confirm everything is green." },
    { zh: "找出这个项目里所有已经坏掉或不一致的地方，列一份清单给我（先不要改）",
      en: "Find everything broken or inconsistent in this project and give me a list first (do not change anything yet)." },
    { zh: "把项目里所有 TODO / FIXME 收集起来，按文件列出来",
      en: "Collect every TODO / FIXME in the project and list them by file." },
  ];

  function renderTemplates() {
    const box = q("#wbTpl");
    if (!box) return;
    // ⚠️ I18N.lang 是 **getter**（不是函数）—— 写成 `I18N.lang()` 会 "not a function" 直接抛。
    //   现成的判断属性是 isEnglish。
    const en = !!(typeof I18N !== "undefined" && I18N.isEnglish);
    box.innerHTML = `<span class="wb-tpl-label">${T("常用委托")}</span>` + TPL.map((x, i) => {
      const txt = en ? x.en : x.zh;
      return `<button class="wb-tpl-chip" type="button" data-tpl-i="${i}" title="${esc(txt)}">${esc(txt.slice(0, 26))}…</button>`;
    }).join("");
    qa("[data-tpl-i]", box).forEach((b) => {
      b.onclick = () => {
        const g = q("#wbGoal");
        if (g) { g.value = TPL[Number(b.dataset.tplI)][en ? "en" : "zh"]; g.focus(); }
        clearPendingTodo();          // 手填模板 = 不再算"替某条待办干活"
        toast(T("模板已填进委托框"), T("可以直接改，也可以就这么开始"));
      };
    });
  }

  /* ---------------- 委托目标历史（最近用过的，一键回填） ---------------- */
  function readGoals() {
    try { const a = JSON.parse(lsGet(GOAL_KEY, "[]")); return Array.isArray(a) ? a : []; }
    catch (e) { return []; }
  }

  function pushGoal(goal) {
    const g = String(goal || "").trim();
    if (!g) return;
    const list = readGoals().filter((x) => x !== g);
    list.unshift(g);
    lsSet(GOAL_KEY, JSON.stringify(list.slice(0, 8)));
    renderGoalHistory();
  }

  function renderGoalHistory() {
    const box = q("#wbGoals");
    if (!box) return;
    const list = readGoals();
    box.hidden = !list.length;
    if (!list.length) return;
    box.innerHTML = `<span class="wbg-label">${T("最近用过")}</span>` + list.map((g, i) =>
      `<button class="wbg-chip" type="button" data-goal-i="${i}" title="${esc(g)}">${esc(g.slice(0, 34))}</button>`
    ).join("");
    qa("[data-goal-i]", box).forEach((b) => {
      b.onclick = () => {
        const g = q("#wbGoal");
        if (g) { g.value = list[Number(b.dataset.goalI)] || ""; g.focus(); }
        clearPendingTodo();     // 手动换了目标，就不再算「替那条待办干活」
      };
    });
  }

  /* ---------------- 侧栏 / 页签的进度角标 ---------------- */
  function clearUnseen() {
    if (ST.unseen) { ST.unseen = 0; }
    setNavBadge();
  }

  function setNavBadge() {
    const live = !!(ST.run && ST.run.live && !ST.run.done);
    const el = q("#wbNavBadge") || q("#nav .nav-btn[data-tab='workbench'] .nav-badge");
    if (el) {
      el.className = "nav-badge" + (live ? " live" : (ST.unseen ? " unseen" : ""));
      if (live) {
        el.hidden = false; el.textContent = "●";
        el.title = T("正在跑一次委托");
      } else if (ST.unseen) {
        el.hidden = false;
        el.textContent = ST.unseen > 9 ? "9+" : String(ST.unseen);
        el.title = T("{n} 次委托已经跑完，点进工作台看看结果", { n: ST.unseen });
      } else {
        el.hidden = true; el.title = "";
      }
    }
    const dot = q("#wbTabRunDot");
    if (dot) dot.hidden = !live;
    // 待办页签上的数字 = 未完成待办数（和工作台「待办」分区一致）
    const tb = q("#wbTabTodoBadge");
    if (tb) {
      const open = todosNow().filter((t) => !t.done).length;
      tb.hidden = !open;
      tb.textContent = open > 99 ? "99+" : String(open);
    }
  }

  /* ---------------- 跑完之后的收尾确认（勾待办 / 结论回对话）----------------
     时序：run.end 是**收尾之前**推出去的，所以不能拿它当结论。
     真正的信号是 SSE 流关闭 —— 流关了就说明服务端生成器走完了，收尾一定做完。
     再来问 `/effects` 拿最终结果；万一还没落库（重连路径下可能早了一步）就重试几次。 */
  async function confirmEffects(runId) {
    if (!runId) return null;
    for (let i = 0; i < 3; i++) {
      try {
        const r = await apiJson(`/api/agent/runs/${encodeURIComponent(runId)}/effects`);
        if (r && r.ready) return r;
      } catch (e) { /* 没就绪就再等一会儿 */ }
      await sleep(1200);
    }
    return null;
  }

  async function afterRun(runId) {
    const r = await confirmEffects(runId);
    if (r) applyEffects(r);
  }

  function applyEffects(r) {
    const st = String(r.status || "");
    // ★ 任务留档是**跑完之后**才写的，所以结果卡刚画出来时还不知道它在哪；
    //   收尾结果里带回来了 → 补到卡片上（并且 toast 一句，否则档案在项目里她永远不知道）。
    const task = r.task || null;
    if (task && task.ok && task.rel) {
      if (ST.run) ST.run.taskDir = task.rel;
      const card = document.querySelector("#wbStream .wb-card-end");
      if (card && !card.querySelector(".wbr-archive")) {
        const box = document.createElement("div");
        box.className = "wbr-archive";
        box.innerHTML = `${T("留档")}：<code>${esc(task.rel)}</code>
          <span class="wbr-archive-hint">${T("这次的目标 / 改了什么 / 验证结论都包在里面")}</span>`;
        const ops = card.querySelector(".wbc-ops");
        if (ops) ops.parentNode.insertBefore(box, ops);
        else card.appendChild(box);
      }
      toast(T("这次任务已留档到项目里"), task.rel);
    }
    if (r.todo) {
      // 用**服务端算好的** auto 标志，不在前端重写一遍「什么时候算自动勾」——
      // 规则只该有一处（`server._agent_complete_todo`），前端复制一份迟早分叉。
      if (r.todo.auto) {
        toast(T("待办已完成并自动勾掉"), r.todo.text || "");
      } else if (!r.todo.done) {
        addTodoDoneNotice(T("这条待办没有自动勾掉（{why}）。确认没问题的话，在下面这个按钮上点一下就行。",
                            { why: statusText(st) }), r.todo.id);
      }
      // done 但不是 auto（她早就自己勾过了）→ 什么都不用说
    }
    if (r.chat && r.chat.posted) {
      if (chatVisible() && !chatBusy()) {
        // 正看着对话、也没在打字：直接把新消息渲出来
        try { if (typeof refreshState === "function") refreshState(); } catch (e) { /* 忽略 */ }
      } else {
        ST.pendingChat = true;      // 等切回对话页再补刷
      }
    }
    ST.pendingTodo = null;
    renderPendingTodo();
    refreshTodoState();
    if (ST.tab === "runs") renderRuns();
  }

  function chatVisible() {
    const sec = q("#tab-chat");
    return !!(sec && sec.classList.contains("active"));
  }

  function chatBusy() {
    try { return typeof busy !== "undefined" && !!busy; } catch (e) { return false; }
  }


  /* ---------------- 起跑 ---------------- */
  function startRun() {
    const goal = (q("#wbGoal").value || "").trim();
    if (!goal) { toast(T("还差一句委托"), T("至少写一句要它做什么"), true); return; }
    if (isGuest()) { toast(T("工程委托需要登录"), T("访客模式不落库，运行记录与快照无法保存"), true); return; }
    const ws = q("#wbWs");
    const wsName = ws && ws.selectedOptions[0] ? ws.selectedOptions[0].textContent : T("演示工作区");
    const auto = q("#wbAuto").checked;
    const timeout = Number(q("#wbTimeout").value || 120);
    const likelyWrite = /修|改|新增|删除|重构|实现|fix|bug|edit|write|test/i.test(goal);
    const steps = likelyWrite
      ? [T("先读取相关文件并定位问题"), T("提出并执行最小改动（写入与命令仍按权限审批）"), T("运行工作区配置的验证命令并交付报告")]
      : [T("读取相关文件和项目状态"), T("分析结果，必要时请求高风险操作审批"), T("交付结论与可导出的运行报告")];
    clearStream();
    const card = pushCard("preview", `
      <div class="wbc-hd"><b>${T("执行预览")}</b><span class="wbc-chip-quiet">${esc(wsName || "")}</span></div>
      <div class="wbc-text">${esc(goal)}</div>
      <ul class="wbc-plan">${steps.map((x, i) => `<li><span class="wbp-box">${i + 1}</span>${esc(x)}</li>`).join("")}</ul>
      <div class="wbc-why">${auto ? T("自动批准已开启：写入和命令不会暂停确认。") : T("写文件、执行命令等高风险动作仍会单独请求你的批准。")}</div>
      <div class="wbc-ops"><button class="primary-btn slim" data-preview="go">${T("确认并开始")}</button><button class="ghost-btn slim" data-preview="back">${T("返回修改委托")}</button></div>`);
    q('[data-preview="go"]', card).onclick = () => confirmStartRun();
    q('[data-preview="back"]', card).onclick = () => clearStream();
  }

  async function confirmStartRun() {
    const goal = (q("#wbGoal").value || "").trim();
    if (!goal) { toast(T("还差一句委托"), T("至少写一句要它做什么"), true); return; }
    if (isGuest()) { toast(T("工程委托需要登录"), T("访客模式不落库，运行记录与快照无法保存"), true); return; }
    const wsId = q("#wbWs").value;
    const auto = q("#wbAuto").checked;
    const timeout = Number(q("#wbTimeout").value || 120);
    const toChat = q("#wbToChat") ? !!q("#wbToChat").checked : true;
    const body = { goal, autoApprove: auto, approvalTimeout: timeout, toChat };
    if (wsId && !q("#wbWs").disabled) {
      body.workspaceId = Number(wsId);
      lsSet(WS_KEY, String(wsId));                 // 记住这次用的工作区，下次默认选它
    }
    // ★ 这条委托是替哪条待办干的活：跑完服务端会据此自动勾掉它（只在验证通过时）
    if (ST.pendingTodo && ST.pendingTodo.id) body.todoId = ST.pendingTodo.id;
    pushGoal(goal);

    clearStream();
    resetFollow();      // ★ 新一次委托从头跟随：不复位的话上一轮的"往上翻"会一直生效，
                        //   新卡片（含审批卡）全长在视口外——那正是她那次 520 秒的成因
    ST.run = newRun("");
    ST.run.streamEl = q("#wbStream");
    ST.run.approvalTimeout = timeout;
    setLive(true);
    q("#wbStart").disabled = true;
    q("#wbStop").hidden = false;
    saveRunState();
    try {
      await streamRun(body);
    } catch (e) {
      if (ST.run && !ST.run.done) {
        addNotice("error", T("连接断开：{msg}（已自动转入续传，运行仍在服务端继续）", { msg: e.message }));
        startCatchUp(ST.run.id);
        return;
      }
    }
    // 流关闭 = 服务端生成器走完 = 收尾（勾待办 / 结论回对话）已经做完，去确认一下
    await afterRun(ST.run && ST.run.id);
  }

  /* POST /api/agent/run 的 SSE：带 seq，事件先落库再推流（所以断了也能补齐） */
  async function streamRun(body) {
    const res = await fetch("/api/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth() },
      body: JSON.stringify(body),
    });
    if (res.status === 401) { toast(T("未登录或登录已过期"), "", true); return; }
    if (!res.ok || !res.body) {
      const txt = await res.text().catch(() => "");
      throw new Error(txt ? txt.slice(0, 200) : ("HTTP " + res.status));
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        const t = line.trim();
        if (!t.startsWith("data:")) continue;
        let ev = null;
        try { ev = JSON.parse(t.slice(5).trim()); } catch (e) { continue; }
        handleEvent(ev);
      }
    }
    if (ST.run && !ST.run.done) {
      addNotice("warn", T("数据流结束但运行还没收尾，正在用 seq 补齐后续事件…"));
      startCatchUp(ST.run.id);
    }
  }

  /* ---------------- 事件 → 卡片 ---------------- */
  function handleEvent(ev) {
    const type = ev.type, p = ev.payload || {};
    const seq = Number(ev.seq || p.seq || 0);
    if (type === "run.ready") {
      if (ST.run && ST.run.id && ST.run.id !== p.run_id) clearStream();
      if (!ST.run || ST.run.id !== p.run_id) ST.run = newRun(p.run_id);
      ST.run.id = p.run_id;
      ST.run.demo = !!p.demo;
      ST.run.wsName = p.workspace || "";
      ST.run.approvalTimeout = Number(p.approvalTimeout || ST.run.approvalTimeout || 120);
      ST.run.live = true;
      const dm = q("#wbDemo");
      if (dm && p.demo) dm.hidden = false;
      addMeta(p);
      saveRunState();
      return;
    }
    if (!ST.run) ST.run = newRun("");
    if (seq > 0) {
      if (ST.run.seen.has(seq)) return;          // 续传重叠：同一条事件不画两遍
      ST.run.seen.add(seq);
      ST.run.lastSeq = Math.max(ST.run.lastSeq, seq);
      if (!ST.run.replaying) saveRunState();
    }
    switch (type) {
      case "run.start": addNotice("info", T("开始：{goal}", { goal: p.goal || "" })); break;
      case "plan.update": cardPlan(p.plan || []); break;
      case "text.delta": cardText(p.text || "", p.step); break;
      case "thought.delta": cardThink(p.text || ""); break;
      case "tool.call": cardTool(p); break;
      case "tool.approval": cardApproval(p); break;
      case "tool.result": cardToolResult(p); break;
      case "tool.output": cardOutput(p); break;
      case "fs.diff": cardDiff(p); break;
      case "verify": cardVerify(p); break;
      case "notice": addNotice(p.level || "info", p.text || ""); break;
      case "run.summary": cardSummary(p); break;
      case "run.end": cardEnd(p); break;
      case "run.undo": addNotice("info", T("已回滚：还原 {r} 个文件，移走 {m} 个新建文件",
        { r: (p.restored || []).length, m: (p.removed || []).length })); break;
      default: break;
    }
  }

  /* ---------------- 卡片渲染 ---------------- */
  function streamEl() { return ST.run && ST.run.streamEl ? ST.run.streamEl : q("#wbStream"); }

  /* 卡片流自己**不滚**：`.wb-stream` 没有 overflow，真正能滚的是它所在的分区 `.wb-pane`。
     之前把这句写成 `box.scrollTop = box.scrollHeight`（box = #wbStream），一直是个**空操作** ——
     新卡片（尤其审批卡）会停在可视区下方，用户得自己往下滚才看得到，
     120 秒不看就按「超时=拒绝」把这次委托拒掉了。 */
  function streamScroller() {
    const box = streamEl();
    if (!box) return null;
    let n = box.parentElement;
    while (n && n !== document.body) {
      const oy = getComputedStyle(n).overflowY;
      if (oy === "auto" || oy === "scroll") return n;
      n = n.parentElement;
    }
    return box;
  }

  /* 「跟随最新」的标志：靠**监听滚动**维护，而不是每次去算"离底多远"。
     为什么不能用"离底 > 120 就不跟随"来推断用户意图：新一轮开跑时 scrollTop 是 0、
     内容还在往下长，只要长过一屏就会满足"离底 > 120"，于是刚开跑就再也不跟随了
     （实测就是这条把审批卡推出屏幕的）。用户真的往上翻时，scroll 事件会把 stick 置 false。 */
  function attachFollow() {
    const sc = streamScroller();
    if (!sc || sc.__wbFollowBound) return;
    sc.__wbFollowBound = true;
    sc.addEventListener("scroll", () => {
      ST.stick = sc.scrollHeight - sc.scrollTop - sc.clientHeight <= 120;
    }, { passive: true });
  }

  function stickStream(force) {
    const el = streamScroller();
    if (!el) return;
    if (!force && ST.stick === false) return;     // 用户明确往上翻了，别抢他的滚动位置
    el.scrollTop = el.scrollHeight;
  }

  /** 复位「跟随最新」标志 —— **只改标志，绝不动当前滚动位置**。
      为什么要有它：原先 ST.stick 只在进整页时被置 true，一旦她往上翻过一次就永远是 false
      → 之后每张卡片（**连审批卡都算**）都长在视口外 → 干等 120 秒超时被当「拒绝」。
      ⚠️ 别在这儿顺手滚到底：表单和卡片流在**同一个滚动容器**里，滚到底会把「开始委托」
      和「要它做什么」一起顶到视口上方（实测 btnBottom 变成 -2459）。
      打开工作台第一眼该看到表单，不是卡片流的尾巴 —— 位置归零由 showPage 单独负责。 */
  function resetFollow() {
    ST.stick = true;
  }

  function pushCard(kind, html, key, force) {
    const box = streamEl();
    if (!box) return null;
    const el = document.createElement("div");
    el.className = "wb-card wb-card-" + kind;
    el.innerHTML = html;
    if (key) el.dataset.key = key;
    box.appendChild(el);
    // force：**必须让用户看见**的卡（审批 / 报错），不管他之前在翻哪里都要滚过去
    stickStream(!!force);
    return el;
  }

  function addMeta(p) {
    const box = streamEl();
    if (!box) return;
    const el = pushCard("meta", `
      <div class="wbc-row">
        <span class="wbc-chip">${esc(p.workspace || "")}</span>
        <span class="wbc-chip wbc-chip-quiet">${T("任务分级")}：${esc(p.taskLevel || "—")}</span>
        <span class="wbc-chip wbc-chip-quiet">Lv.${esc(p.level || 1)}</span>
        ${p.demo ? `<span class="wbc-chip wbc-chip-warn">${T("演示模式")}</span>` : ""}
        ${p.autoApprove ? `<span class="wbc-chip wbc-chip-warn">${T("自动批准")}</span>` : ""}
      </div>
      <div class="wbc-path" title="${esc(p.root || "")}">${esc(p.root || "")}</div>`);
    return el;
  }

  function cardText(text, step) {
    const box = streamEl();
    if (!box || !text) return;
    const last = box.lastElementChild;
    if (last && last.classList.contains("wb-card-text") && String(last.dataset.step) === String(step)) {
      const body = q(".wbc-text", last);
      body.textContent += text;
    } else {
      const el = pushCard("text", `<div class="wbc-text"></div>`);
      if (el) { el.dataset.step = String(step); q(".wbc-text", el).textContent = text; }
    }
    stickStream();
  }

  function cardThink(text) {
    let el = q(".wb-card-think .wbc-think-body");
    if (!el) {
      const c = pushCard("think", `<div class="wbc-hd">${T("思考")}</div><div class="wbc-think-body"></div>`);
      el = c && q(".wbc-think-body", c);
    }
    if (el) el.textContent += text;
  }

  function cardPlan(plan) {
    ST.run.plan = plan;
    let el = q(".wb-card-plan");
    const body = plan.map((t) => `<li class="${t.done ? "done" : ""}"><span class="wbp-box">${t.done ? "✓" : ""}</span>${esc(t.text)}</li>`).join("");
    if (!el) {
      el = pushCard("plan", `<div class="wbc-hd">${T("计划")} <span class="wbc-count">${plan.filter((x) => x.done).length}/${plan.length}</span></div><ul class="wbc-plan">${body}</ul>`);
    } else {
      el.innerHTML = `<div class="wbc-hd">${T("计划")} <span class="wbc-count">${plan.filter((x) => x.done).length}/${plan.length}</span></div><ul class="wbc-plan">${body}</ul>`;
    }
  }

  const RISK_TXT = { read: "只读", write: "写文件", exec: "执行命令" };

  function cardTool(p) {
    const key = p.step + ":" + p.name;
    const decided = p.decision === "allow" ? "ok" : (p.decision === "deny" ? "no" : "ask");
    /* ★ 降噪：工具卡**默认折叠**成一行（工具名 + 风险 + 裁决 + 参数摘要），点一下展开细节。
       为什么默认收起：排查环境那种任务会连出十几张工具卡，把「计划 / 审批 / 自检 / 结果」全刷下去 ——
       真正需要你看见的卡反而找不到了。
       ⚠️ 但**被拒 / 失败的默认展开**：那是你要处理的东西，藏起来就等于没提示。 */
    const brief = String(p.preview || "").split("\n").join(" ").slice(0, 60);
    const el = pushCard("tool", `
      <div class="wbc-hd">
        <button class="wbc-fold" type="button" data-fold="tool"
                title="${T("展开 / 收起细节")}" aria-label="${T("展开 / 收起细节")}">▸</button>
        <span class="wbc-tool">${esc(p.name)}</span>
        <span class="wbc-risk wbc-risk-${esc(p.risk || "read")}">${T(RISK_TXT[p.risk] || p.risk || "")}</span>
        <span class="wbc-dec wbc-dec-${decided}">${esc(decisionText(p.decision))}</span>
        <span class="wbc-brief">${esc(brief)}</span>
        <span class="wbc-step">#${esc(p.step || "")}</span>
      </div>
      <div class="wbc-body">
        ${p.preview ? `<pre class="wbc-args">${esc(p.preview)}</pre>` : ""}
        ${p.reason ? `<div class="wbc-why">${esc(p.reason)}</div>` : ""}
        <div class="wbc-result" hidden></div>
      </div>`, key);
    el.classList.add("folded");
    const fb = q(".wbc-fold", el);
    if (fb) fb.onclick = () => {
      el.classList.toggle("folded");
      fb.textContent = el.classList.contains("folded") ? "▸" : "▾";
    };
    ST.run.steps[key] = ST.run.steps[key] || {};
    ST.run.steps[key].toolEl = el;
  }

  function cardToolResult(p) {
    const key = p.step + ":" + p.name;
    const rec = ST.run.steps[key] || (ST.run.steps[key] = {});
    const el = rec.toolEl || pushCard("tool", `<div class="wbc-hd"><span class="wbc-tool">${esc(p.name)}</span></div><div class="wbc-result"></div>`, key);
    const outEl = q(".wbc-result", el);
    if (!outEl) return;
    outEl.hidden = false;
    outEl.className = "wbc-result " + (p.ok ? "ok" : "bad");
    outEl.innerHTML = `<span class="wbc-ok">${p.ok ? "✓" : "✕"}</span> ${esc(p.summary || "")}
      ${p.ms != null ? `<span class="wbc-ms">${p.ms}ms</span>` : ""}`;
    // ★ 失败的 / 被拒的工具卡**自动展开**：那是需要她处理的东西，藏起来等于没提示。
    //   成功的保持折叠（降噪），但她随时可以点 ▸ 展开看参数与结果。
    if (!p.ok) {
      el.classList.remove("folded");
      const fb2 = q(".wbc-fold", el);
      if (fb2) fb2.textContent = "▾";
    }
  }

  function cardOutput(p) {
    const key = p.step + ":out";
    const rec = ST.run.steps[key] || (ST.run.steps[key] = {});
    if (!rec.el) {
      const c = pushCard("out", `
        <div class="wbc-hd">${T("命令输出")} <span class="wbc-step">#${esc(p.step || "")}</span>
          <button class="wbc-more" type="button" hidden>${T("展开")}</button>
        </div>
        <pre class="wbc-out"></pre>`);
      rec.el = c;
    }
    const pre = q(".wbc-out", rec.el);
    pre.textContent += (pre.textContent ? "\n" : "") + (p.text || "");
    if (pre.textContent.length > 1800) {
      rec.el.classList.add("folded");
      const btn = q(".wbc-more", rec.el);
      if (btn) {
        btn.hidden = false;
        btn.onclick = () => {
          rec.el.classList.toggle("folded");
          btn.textContent = rec.el.classList.contains("folded") ? T("展开") : T("收起");
        };
      }
    }
    const box = streamEl();
    if (box && !rec.el.classList.contains("folded")) stickStream();
  }

  function cardDiff(p) {
    const lines = String(p.diff || "").split("\n");
    const body = lines.map((l) => {
      const cls = l.startsWith("+++") || l.startsWith("---") ? "meta"
        : l.startsWith("+") ? "add" : l.startsWith("-") ? "del"
          : l.startsWith("@@") ? "hunk" : "";
      return `<span class="wbd-line ${cls}">${esc(l)}</span>`;
    }).join("");
    const adds = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
    const dels = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;
    // 累计到这次运行上：跑完的「本次结果」卡要一眼说清改了哪些文件、增删多少行
    if (ST.run && ST.run.files && p.path) {
      const cur = ST.run.files[p.path] || (ST.run.files[p.path] = { created: !!p.created,
                                                                   adds: 0, dels: 0 });
      cur.adds += adds;
      cur.dels += dels;
    }
    const el = pushCard("diff", `
      <div class="wbc-hd">
        <span class="wbc-file">${esc(p.path || "")}</span>
        <span class="wbc-chip-quiet">${p.created ? T("新建") : T("修改")}</span>
        <span class="wbd-add">+${adds}</span> <span class="wbd-del">-${dels}</span>
        <button class="wbc-more" type="button">${T("收起")}</button>
      </div>
      <div class="wbc-diff">${body || `<span class="wbd-line">${T("（无差异内容）")}</span>`}</div>`);
    const btn = q(".wbc-more", el);
    const box2 = q(".wbc-diff", el);
    if (btn) btn.onclick = () => {
      const folded = box2.style.maxHeight === "88px";
      box2.style.maxHeight = folded ? "" : "88px";
      btn.textContent = folded ? T("收起") : T("展开");
    };
    box2.style.maxHeight = "88px";
  }

  function cardReview(data) {
    const paths = [...new Set((data.diffs || []).map((d) => d.path).filter(Boolean))];
    if (!paths.length || !ST.run || ST.run.status === "running") return;
    const rows = paths.map((path) => `<div class="wbr-review-row" data-review-path="${esc(path)}">
      <code>${esc(path)}</code><span class="wbc-chip-quiet">${T("待审阅")}</span>
      <button class="ghost-btn slim" data-review="accept" data-path="${esc(path)}">${T("接受")}</button>
      <button class="danger-btn slim" data-review="reject" data-path="${esc(path)}">${T("撤回此文件")}</button></div>`).join("");
    const el = pushCard("review", `<div class="wbc-hd"><b>${T("逐文件审阅")}</b><span class="wbc-chip-quiet">${T("撤回会恢复该文件运行前的快照；新建文件会移入回收站")}</span></div><div class="wbr-review">${rows}</div>`);
    qa("[data-review]", el).forEach((b) => b.onclick = async () => {
      const path = b.dataset.path, decision = b.dataset.review;
      const row = b.closest(".wbr-review-row");
      qa("button", row).forEach((x) => { x.disabled = true; });
      try {
        const r = await apiJson(`/api/agent/runs/${encodeURIComponent(ST.run.id)}/review`, {
          method: "POST", body: JSON.stringify({ path, decision }) });
        if (!r.ok) throw new Error((r.errors || []).join("; ") || T("没有完成"));
        row.querySelector(".wbc-chip-quiet").textContent = decision === "accept" ? T("已接受") : T("已撤回");
        row.classList.toggle("rejected", decision === "reject");
        toast(decision === "accept" ? T("已接受此文件的改动") : T("已撤回此文件的改动"), path);
      } catch (e) { qa("button", row).forEach((x) => { x.disabled = false; }); toast(T("审阅操作失败"), e.message, true); }
    });
  }

  function cardVerify(p) {
    pushCard("verify", `
      <div class="wbc-hd">${T("内核自检")}
        <span class="wbc-dec ${p.ok ? "wbc-dec-ok" : "wbc-dec-no"}">${p.ok ? T("通过") : T("未通过")}</span>
        <span class="wbc-chip-quiet">${T("改动前")}：${p.baseline_ok ? T("通过") : T("未通过")}</span>
      </div>
      <div class="wbc-cmd">$ ${esc(p.cmd || "")}</div>
      ${p.output ? `<pre class="wbc-out folded">${esc(p.output)}</pre>` : ""}`);
  }

  function cardApproval(p) {
    // 回放历史事件时不会有 tool.approval（审批是一次 HTTP 往返，不进事件流的历史语义），
    // 但续传时可能会补到 —— 所以这里只在「运行仍活着」时才给可点的按钮。
    const live = ST.run.live && !ST.run.replaying;
    // ★ force=true：审批是**阻塞式**提示 —— 用户看不见就等于没有，
    //   120 秒后会被自动当成「拒绝」。所以绝不能受「他是不是在跟随最新」影响。
    const el = pushCard("approval", `
      <div class="wbc-hd">
        <span class="wbc-badge-ask">${T("要你批一下")}</span>
        <span class="wbc-tool">${esc(p.name)}</span>
        <span class="wbc-risk wbc-risk-${esc(p.risk || "")}">${T(RISK_TXT[p.risk] || "")}</span>
        <span class="wbc-cd" hidden></span>
      </div>
      <pre class="wbc-args">${esc(p.preview || "")}</pre>
      <div class="wbc-why">${esc(p.reason || "")}</div>
      <div class="wbc-ops">
        <button class="primary-btn slim" data-wb="allow-once">${T("允许这次")}</button>
        <button class="ghost-btn slim" data-wb="allow-tool">${T("本会话允许该工具")}</button>
        ${p.risk === "exec" && ST.status && ST.status.tier && ST.status.tier.key === "partner"
          ? `<button class="ghost-btn slim" data-wb="allow-prefix">${T("本会话允许该命令前缀")}</button>` : ""}
        <button class="danger-btn slim" data-wb="deny">${T("拒绝")}</button>
      </div>`, null, true);
    if (!live) {
      el.classList.add("wbc-hist");
      qa(".wbc-ops button", el).forEach((b) => { b.disabled = true; });
      const ops = q(".wbc-ops", el);
      if (ops) ops.insertAdjacentHTML("beforebegin", `<div class="wbc-why">${T("（历史运行：审批按钮已失效）")}</div>`);
    } else {
      ST.run.approval = { el: el, step: p.step, name: p.name };
      bindApproval(el, p);
      startCountdown(el);
    }
  }

  function decisionText(d) {
    return d === "allow" ? T("已批准") : d === "deny" ? T("已拒绝") : (d === "ask" ? T("待审批") : (d || ""));
  }

  function bindApproval(el, p) {
    qa("[data-wb]", el).forEach((b) => {
      b.onclick = async () => {
        const act = b.dataset.wb;
        const verdict = act === "deny" ? "deny" : "allow";
        const scope = act === "allow-tool" ? "tool" : (act === "allow-prefix" ? "prefix" : "once");
        qa("[data-wb]", el).forEach((x) => { x.disabled = true; });
        stopCountdown(el);
        markDecided(el, verdict === "allow");
        try {
          await apiJson(`/api/agent/runs/${ST.run.id}/approval`, {
            method: "POST", body: JSON.stringify({ verdict, scope }) });
        } catch (e) {
          toast(T("裁决没送到"), e.message, true);
        }
        ST.run.approval = null;
      };
    });
  }

  function markDecided(el, ok) {
    const cd = q(".wbc-cd", el);
    if (cd) { cd.hidden = false; cd.textContent = ok ? T("已批准") : T("已拒绝"); cd.className = "wbc-cd " + (ok ? "wbc-dec-ok" : "wbc-dec-no"); }
  }

  function startCountdown(el) {
    const cd = q(".wbc-cd", el);
    if (!cd) return;
    cd.hidden = false;
    let left = Math.max(5, ST.run.approvalTimeout || 120);
    const tick = () => {
      cd.textContent = T("超时自动拒绝 {s}s", { s: left });
      if (left <= 0) {
        stopCountdown(el);
        cd.textContent = T("已超时（视为拒绝）");
        qa("[data-wb]", el).forEach((x) => { x.disabled = true; });
        return;
      }
      left -= 1;
    };
    tick();
    el._cdTimer = setInterval(tick, 1000);
  }
  function stopCountdown(el) { if (el && el._cdTimer) { clearInterval(el._cdTimer); el._cdTimer = null; } }

  function cardSummary(p) {
    const v = p.verification || {};
    pushCard("summary", `
      <div class="wbc-hd">${T("内核结论")}
        <span class="wbc-dec ${p.status === "done" ? "wbc-dec-ok" : "wbc-dec-no"}">${esc(statusText(p.status))}</span>
        ${v.ran ? `<span class="wbc-chip-quiet">${T("验证")}：${v.ok ? T("通过") : (v.baseline_failed ? T("改动前就是红的") : T("未通过"))}</span>`
                : `<span class="wbc-chip-quiet">${T("未自动验证")}</span>`}
      </div>
      <pre class="wbc-sum">${esc(p.summary || "")}</pre>
      <div class="wbc-touched">${T("改动文件")}：${(p.touched || []).length ? (p.touched || []).map((f) => `<code>${esc(f)}</code>`).join(" ") : T("无")}</div>`);
  }

  function cardEnd(p) {
    if (!ST.run) return;
    ST.run.done = true;
    ST.run.status = p.status;
    setLive(false);
    q("#wbStart").disabled = false;
    q("#wbStop").hidden = true;
    saveRunState(true);
    const v = p.verification || {};
    // ★ 结果摘要：跑完最该先看到的三件事 —— **动了什么 / 有没有被证明 / 花了多少**。
    //   以前只有一行状态数字，"改了哪些文件"要去翻 diff 卡、"为什么没验证"要靠自己猜。
    const files = Object.entries((ST.run && ST.run.files) || {});
    const adds = files.reduce((n, [, f]) => n + (f.adds || 0), 0);
    const dels = files.reduce((n, [, f]) => n + (f.dels || 0), 0);
    const filesHTML = files.length
      ? `<div class="wbr-files"><span class="wbr-files-label">${T("改动了 {n} 个文件", { n: files.length })}</span>${files.slice(0, 6).map(([path, f]) =>
          `<span class="wbr-file">${esc(path)}${f.created ? `<i>${T("新建")}</i>` : ""}</span>`
        ).join("")}${files.length > 6 ? `<span class="wbr-file-more">${T("还有 {n} 个", { n: files.length - 6 })}</span>` : ""}
        <span class="wbr-diffsum"><b class="wbd-add">+${adds}</b> <b class="wbd-del">-${dels}</b></span></div>`
      : `<div class="wbr-files none">${T("没有改动任何文件")}</div>`;
    // 「没得验」和「验了没通过」是两件事，界面上必须分开说
    const noCmd = !v.ran && v.source !== "off";
    const verifyHTML = v.ran
      ? `<div class="wbr-verdict ${v.ok ? "ok" : "bad"}">
           <b>${v.ok ? T("验证通过") : T("验证未通过")}</b>
           <code>${esc(v.cmd || "")}</code>
           ${v.baseline_failed ? `<span class="wbr-baseline">${T("改动前它本来就是红的")}</span>` : ""}
         </div>`
      : `<div class="wbr-verdict none">
           <b>${T("没有验证")}</b>
           <span>${esc(v.note || (v.source === "off"
             ? T("这个工作区关闭了验证闸门")
             : T("这个工作区没配验证命令 —— 内核没有命令可跑，就没法自己证明改动是好的")))}</span>
         </div>`;
    const cost = money(p.tokens_in, p.tokens_out);
    const el0 = pushCard("end", `
      <div class="wbc-hd">
        <span class="wbc-badge-end">${T("本次结果")}</span>
        <span class="wbr-status wbr-${esc(p.status)}">${esc(statusText(p.status))}</span>
        <span class="wbc-chip-quiet">${esc(ST.run.wsName || "")}</span>
      </div>
      ${filesHTML}
      ${verifyHTML}
      ${v.ran && !v.ok ? `<div class="wbc-why">${T("验证未通过：这不是「大概好了」，是真的没证明没坏。")}</div>` : ""}
      ${(ST.run && ST.run.taskDir) ? `<div class="wbr-archive">${T("留档")}：<code>${esc(ST.run.taskDir)}</code>
        <span class="wbr-archive-hint">${T("这次的目标 / 改了什么 / 验证结论都包在里面")}</span></div>` : ""}
      <div class="wbc-stats">
        <div class="wbs"><b>${esc(p.steps || 0)}</b><span>${T("步数")}</span></div>
        <div class="wbs"><b>${esc(p.tool_calls || 0)}</b><span>${T("工具调用")}</span></div>
        <div class="wbs"><b>${fmtTok((p.tokens_in || 0) + (p.tokens_out || 0))}</b><span>token</span></div>
        <div class="wbs"><b>${fmtSec(p.seconds || 0)}</b><span>${T("耗时")}</span></div>
        <div class="wbs"><b>${esc(cost || "—")}</b><span>${T("花费")}</span></div>
      </div>
      <div class="wbc-ops">
        ${noCmd ? `<button class="primary-btn slim" data-end="verify">${T("给它配一条验证命令")}</button>` : ""}
        <button class="ghost-btn slim" data-end="diff">${T("看 diff")}</button>
        <button class="ghost-btn slim" data-end="report">${T("导出报告")}</button>
        <button class="danger-btn slim" data-end="undo">${T("回滚这次改动")}</button>
      </div>`);
    const el = el0 || q("#wbStream").lastElementChild;
    qa("[data-end]", el).forEach((b) => {
      b.onclick = async () => {
        const act = b.dataset.end;
        if (act === "diff") { setTab("runs"); await openRun(ST.run.id); }
        else if (act === "report") downloadReport(ST.run.id);
        else if (act === "undo") doUndo(ST.run.id, b);
        else if (act === "verify") gotoVerifyEdit(b);
      };
    });
    // 跑完了：没在看工作台的话，侧栏角标留个记号（否则切走就完全不知道跑完没）
    if (!ST.open || document.hidden) {
      ST.unseen += 1;
      setNavBadge();
    }
    renderRuns();
  }

  function addNotice(level, text) {
    if (!text) return null;
    // 报错要**强制**滚到眼前：她翻历史的时候出错，错过了就等于没提示。
    return pushCard("notice", `<div class="wbn wbn-${esc(level || "info")}">${esc(text)}</div>`,
                    null, level === "error");
  }

  /** 「没自动勾掉」时的收尾提示 + 一个就地「标记完成」按钮。
      为什么不只写一句话让她自己去勾：这时候她就在工作台里，让她跨页去找那条待办
      是最容易变成「算了不管了」的一步。 */
  function addTodoDoneNotice(text, todoId) {
    const el = pushCard("notice", `<div class="wbn wbn-warn wbn-actions">
        <span class="wbn-txt">${esc(text)}</span>
        <button class="ghost-btn slim" type="button" data-notice-act="done">${T("我自己确认过了，标记完成")}</button>
      </div>`);
    const b = el && el.querySelector("[data-notice-act='done']");
    if (b) {
      b.onclick = async () => {
        b.disabled = true;
        try {
          await apiJson(`/api/todos/${encodeURIComponent(todoId)}`,
                        { method: "PATCH", body: JSON.stringify({ done: true }) });
          b.textContent = T("已勾掉");
          const wrap = el.querySelector(".wbn-actions") || el;
          wrap.classList.add("wbn-settled");
          await refreshTodoState();
          toast(T("🎉 完成一件"), T("这条待办已标记完成"));
        } catch (e) {
          toast(T("操作失败"), e.message, true);
          b.disabled = false;
        }
      };
    }
    return el;
  }

  /** 从结果摘要卡跳到「工作区」页，并**直接打开那条工作区的验证命令编辑框**。
      少了这一步，"给它配一条验证命令"就只是一句话，她还得自己去找是哪一条。 */
  function gotoVerifyEdit(btn) {
    const name = (ST.run && ST.run.wsName) || "";
    setTab("ws");
    refreshStatus().then(() => {
      renderWs();
      const cards = qa("#wbWsList [data-ws]");
      const hit = cards.find((c) => (q(".wbw-top b", c) || {}).textContent === name) || cards[0];
      if (!hit) { toast(T("还没有可用工作区"), T("先在下面登记一个项目目录")); return; }
      hit.scrollIntoView({ block: "nearest" });
      const w = (ST.workspaces || []).find((x) => Number(x.id) === Number(hit.dataset.ws)) || {};
      openVerifyEdit(hit, w);
      toast(T("把验证命令填在这里"), T("一行一条；填完点「保存」，下次跑完内核就会用它证明改动"));
    });
    void btn;
  }

  function clearStream() {
    const box = q("#wbStream");
    if (box) box.innerHTML = "";
    /* 清空之后必须把分区滚回顶部。留着上一轮那次运行滚到底的 scrollTop，
       接下来的 stickStream() 会因为「离底 > 120px」判定成"用户正在往上翻历史"而不再跟随，
       于是新运行的卡片（尤其审批卡）停在视口外 —— 用户看不见，120s 后按「超时=拒绝」拒掉委托。 */
    const sc = streamScroller();
    if (sc && sc !== box) sc.scrollTop = 0;
    ST.stick = true;                       // 新一轮：恢复跟随（用户上一轮翻到哪儿都翻篇了）
    if (ST.run) { ST.run.steps = {}; ST.run.seen = new Set(); }
  }

  function setLive(on) {
    const el = q("#wbLive");
    if (el) { el.hidden = !on; }
    const btn = q("#wbStop");
    if (btn) btn.hidden = !on;
    setNavBadge();
  }

  /* ---------------- 停止 / 回滚 / 报告 ---------------- */
  async function stopRun() {
    if (!ST.run || !ST.run.id) return;
    try {
      const r = await apiJson(`/api/agent/runs/${ST.run.id}/stop`, { method: "POST", body: "{}" });
      toast(r.ok ? T("已请求停止") : T("这个运行已经不在跑了"), "");
      if (!r.ok) setLive(false);
    } catch (e) { toast(T("停止失败"), e.message, true); }
  }

  async function doUndo(runId, btn) {
    if (!confirm(T("把工作区恢复到这次运行之前？（新建的文件会移入回收站，不做真删除）"))) return;
    if (btn) btn.disabled = true;
    try {
      const r = await apiJson(`/api/agent/runs/${runId}/undo`, { method: "POST", body: "{}" });
      toast(T("已回滚"), T("还原 {r} 个文件，移走 {m} 个", { r: (r.restored || []).length, m: (r.removed || []).length }));
      addNotice("info", T("回滚完成：还原 {r}，移走 {m}", { r: (r.restored || []).length, m: (r.removed || []).length }));
    } catch (e) { toast(T("回滚失败"), e.message, true); }
    if (btn) btn.disabled = false;
  }

  async function downloadReport(runId) {
    try {
      const r = await apiJson(`/api/agent/runs/${runId}/report`);
      const blob = new Blob([r.markdown || ""], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `yorozuya-run-${runId}.md`;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      toast(T("报告已导出"), `yorozuya-run-${runId}.md`);
    } catch (e) { toast(T("导出失败"), e.message, true); }
  }

  /* ---------------- 续传（断线补齐） ---------------- */
  function saveRunState(finished) {
    try {
      if (!ST.run || !ST.run.id) return;
      localStorage.setItem(RUN_KEY, JSON.stringify({
        runId: ST.run.id, lastSeq: ST.run.lastSeq, at: Date.now(), done: !!finished }));
    } catch (e) { /* 隐私模式：忽略 */ }
  }
  function readRunState() {
    try { return JSON.parse(localStorage.getItem(RUN_KEY) || "null"); } catch (e) { return null; }
  }
  function clearRunState() { try { localStorage.removeItem(RUN_KEY); } catch (e) {} }

  function startCatchUp(runId) {
    stopCatchUp();
    if (!runId) return;
    const tick = async () => {
      let status = "running";
      try {
        const r = await apiJson(`/api/agent/runs/${runId}/events?after=${ST.run ? ST.run.lastSeq : 0}`);
        (r.events || []).forEach(handleEvent);
        const pend = await apiJson(`/api/agent/runs/${runId}/pending`);
        if (pend.pending && ST.run && !ST.run.approval && ST.run.live) {
          cardApproval(pend.pending);
        }
        const d = await apiJson(`/api/agent/runs/${runId}/diff`);
        status = d.status || "running";
      } catch (e) {
        addNotice("warn", T("续传失败：{msg}", { msg: e.message }));
        stopCatchUp();
        return;
      }
      if (status !== "running") {
        stopCatchUp();
        if (ST.run && !ST.run.done) {
          ST.run.done = true;
          ST.run.live = false;
          setLive(false);
          q("#wbStart").disabled = false;
          addNotice("info", T("运行已结束（状态：{s}）", { s: statusText(status) }));
          renderRuns();
          afterRun(runId);
        }
        clearRunState();
      }
    };
    ST.timer = setInterval(tick, 1200);
    tick();
  }
  function stopCatchUp() { if (ST.timer) { clearInterval(ST.timer); ST.timer = null; } }

  /* 页面打开（或刷新）时：如果上次的运行还没结束，用 seq 补齐 */
  async function resumeOnBoot() {
    const saved = readRunState();
    if (!saved || !saved.runId || saved.done) return;
    /* ★ 两道守卫，防的是「整条卡片流被画两遍」：
       本函数在页面加载 1.5s 后还会被无条件再调一次（见文件末尾的 setTimeout）。
       如果那一刻用户刚点了「开始委托」、卡片正在流，按 after=0 重放一遍就会
       把工具卡 / diff / 命令输出**全部重复渲染**（而且重复的审批卡会让「允许」按钮
       跑到视口外）。所以：正在跑的、或已经在画同一个 run 的，一律不插手。 */
    if (ST.run && ST.run.live && ST.run.id === saved.runId) return;
    if (ST.run && ST.run.id === saved.runId && ST.run.seen && ST.run.seen.size > 0) return;
    if (Date.now() - (saved.at || 0) > 30 * 60 * 1000) { clearRunState(); return; }
    ST.run = newRun(saved.runId);
    ST.run.lastSeq = Number(saved.lastSeq || 0);
    ST.run.live = true;
    try {
      const r = await apiJson(`/api/agent/runs/${saved.runId}/events?after=0`);
      ST.run.seen = new Set();
      (r.events || []).forEach(handleEvent);
      const d = await apiJson(`/api/agent/runs/${saved.runId}/diff`);
      if (d.status === "running") {
        ST.run.live = true;
        open();
        addNotice("info", T("检测到上次的委托还在跑，已按 seq 续上（断线前的卡片都补齐了）。"));
        startCatchUp(saved.runId);
      } else {
        ST.run.done = true;
        ST.run.live = false;
        clearRunState();
      }
    } catch (e) { /* 忽略：可能已被清理 */ }
  }

  /* ---------------- 回放 ---------------- */
  async function openRun(runId) {
    try {
      const r = await apiJson(`/api/agent/runs/${runId}/events?after=0`);
      const events = r.events || [];
      clearStream();
      ST.run = newRun(runId);
      ST.run.live = false;
      ST.run.replaying = true;
      setLive(false);
      const d = await apiJson(`/api/agent/runs/${runId}/diff`);
      ST.run.status = d.status;
      const head = pushCard("meta", `
        <div class="wbc-row">
          <span class="wbc-chip">${T("回放")}</span>
          <span class="wbc-chip-quiet">${esc(statusText(d.status))}</span>
          <span class="wbc-chip-quiet">${events.length} ${T("条事件")}</span>
        </div>
        <div class="wbc-path">${esc((d.touched || []).join(" ") || T("未改动文件"))}</div>`);
      head.dataset.replay = "head";
      cardReview(d);
      replayEvents(events);
    } catch (e) { toast(T("打不开这次运行"), e.message, true); }
  }

  function replayEvents(events) {
    if (ST.replay) { clearInterval(ST.replay); ST.replay = null; }
    const step = events.length > 120 ? 0 : Math.max(20, Math.min(140, Math.round(3000 / Math.max(1, events.length))));
    let i = 0;
    const feed = () => {
      const ev = events[i++];
      if (!ev) { clearInterval(ST.replay); ST.replay = null; ST.run.replaying = false; return; }
      handleEvent(ev);
      if (i >= events.length) { clearInterval(ST.replay); ST.replay = null; ST.run.replaying = false; }
    };
    if (step === 0) { events.forEach(handleEvent); ST.run.replaying = false; return; }
    ST.replay = setInterval(feed, step);
    feed();
  }

  /* ---------------- 运行记录面板 ---------------- */
  const OK_STATUS = ["done"];
  const BAD_STATUS = ["verify_failed", "error", "blocked", "stopped", "budget", "unverified"];

  /** 筛出要显示的运行（搜索 + 状态 + 来源），返回 {list, total} */
  function filterRuns(runs) {
    const kw = (ST.runSearch || "").trim().toLowerCase();
    const st = ST.runStatus || "all";
    const list = (runs || []).filter((r) => {
      if (st === "ok" && OK_STATUS.indexOf(r.status) < 0) return false;
      if (st === "bad" && BAD_STATUS.indexOf(r.status) < 0) return false;
      if (st === "todo" && !r.todoId) return false;
      if (kw) {
        const hay = ((r.goal || "") + " " + (r.ws || "")).toLowerCase();
        if (hay.indexOf(kw) < 0) return false;
      }
      return true;
    });
    return { list, total: (runs || []).length };
  }

  async function renderRuns() {
    const box = q("#wbRunList");
    if (!box) return;
    try {
      const rows = await apiJson("/api/agent/runs?limit=50");
      ST.runs = Array.isArray(rows) ? rows : [];
    } catch (e) { /* 下面统一渲染空态 */ }
    if (!q("#wbRunList")) return;
    qa("#wbRunFilters .filter-btn", shell).forEach((b) => {
      b.classList.toggle("active", b.dataset.runst === (ST.runStatus || "all"));
    });
    const runs = ST.runs || [];
    if (!runs.length) {
      box.innerHTML = `<div class="wb-empty">${isGuest()
        ? T("工程委托需要登录：访客模式不落库，运行记录与快照无法保存。")
        : T("还没有任何委托记录。在「委托」页写一句要它做的事就能开跑。")}</div>`;
      const cnt0 = q("#wbRunsCount"); if (cnt0) cnt0.textContent = "";
      return;
    }
    const { list, total } = filterRuns(runs);
    const cnt = q("#wbRunsCount");
    if (cnt) cnt.textContent = list.length === total ? T("{n} 条", { n: total })
                                                     : T("{a} / {b} 条", { a: list.length, b: total });
    if (!list.length) {
      box.innerHTML = `<div class="wb-empty">${T("没有符合条件的记录：换个关键词，或把筛选切回「全部」。")}</div>`;
      return;
    }
    box.innerHTML = list.map((r) => `
      <div class="wb-run" data-run="${esc(r.id)}">
        <div class="wbr-top">
          <span class="wbr-status wbr-${esc(r.status)}">${esc(statusText(r.status))}</span>
          ${r.demo ? `<span class="wbc-chip wbc-chip-warn">${T("演示")}</span>` : ""}
          ${r.todoId ? `<span class="wbc-chip-quiet" title="${T("这条委托是从待办派出去的")}">${T("待办")}</span>` : ""}
          <span class="wbr-when">${esc(fmtWhen(r.createdAt))}</span>
        </div>
        <div class="wbr-goal">${esc((r.goal || "").slice(0, 120))}</div>
        <div class="wbr-nums">
          <span>${esc(r.workspaceId ? (r.ws || "") : "")}</span>
          <span>${T("步数")} ${esc(r.steps || 0)}</span>
          <span>${T("工具")} ${esc(r.toolCalls || 0)}</span>
          <span>${fmtTok((r.tokensIn || 0) + (r.tokensOut || 0))} token</span>
          <span>${fmtSec(r.seconds || 0)}</span>
          ${r.verification && r.verification.ran ? `<span class="${r.verification.ok ? "wbr-ok" : "wbr-bad"}">${T("验证")}${r.verification.ok ? "✓" : "✕"}</span>` : ""}
        </div>
        <div class="wbr-ops">
          <button class="ghost-btn slim" data-run-act="replay">${T("回放")}</button>
          <button class="ghost-btn slim" data-run-act="again" title="${T("同一句话、同一个工作区，重新跑一次")}">${T("再跑一次")}</button>
          <button class="ghost-btn slim" data-run-act="report">${T("报告")}</button>
          ${r.taskDir ? `<span class="wbr-task" title="${T("这次任务在项目里的留档文件夹：{p}", { p: r.taskDir })}">${T("留档")}</span>` : ""}
          <button class="danger-btn slim" data-run-act="undo">${T("回滚")}</button>
          <button class="danger-btn slim" data-run-act="del" title="${T("删掉这条记录（含它在项目里的留档文件夹）")}">${T("删除")}</button>
        </div>
      </div>`).join("");
    qa("[data-run]", box).forEach((el) => {
      const id = el.dataset.run;
      qa("[data-run-act]", el).forEach((b) => {
        b.onclick = () => {
          const act = b.dataset.runAct;
          // ★ 只有「会把你带到别处」的动作才切到委托页（回放要看着卡片流、再跑一次要用委托表单、
          //   回滚的结果卡也落在卡片流上）。「报告」（下载一个文件）和「删除」都是**就地完成**的事。
          //   原先这里是一句**无条件** setTab("run")，于是点「删除」会把你拽回委托页 ——
          //   她报的正是这个：删完应该留在原地，不然刚筛出来的那一批就白筛了。
          const GO_TO_RUN = ["replay", "again", "undo"];
          if (GO_TO_RUN.indexOf(act) >= 0) setTab("run");
          if (act === "replay") openRun(id);
          else if (act === "report") downloadReport(id);
          else if (act === "undo") doUndo(id, b);
          else if (act === "again") rerun(id);
          else if (act === "del") deleteRun(id, el);
        };
      });
    });
    const purge = q("#wbRunsPurge");
    if (purge) {
      purge.hidden = !list.length;          // 一条都没列出来就不给按（避免"按了没反应"）
      purge.onclick = () => deleteRuns(list.map((x) => x.id), purge);
    }
  }

  /* ---------------- 删除运行记录 ----------------
     「删」是一个**不可逆动作**（虽然留档会进回收站），所以：
     · 二次确认要说清删掉什么（库里记录 + 项目里的留档文件夹 + 这次的大输出产物）
     · 单条删 → 只刷新这一条所在的列表；批量删 → 全部重画
     · 删完必须把**成本仪表**与**角标**一起刷新（否则数字还停在旧值上，看着像没删掉） */
  async function deleteRun(id, btn) {
    const r = (ST.runs || []).find((x) => x.id === id) || {};
    const goal = String(r.goal || "").slice(0, 40);
    if (!confirm(T("删掉这条记录？「{goal}」—— 会一并删掉库里的记录和它在项目里的留档文件夹（进回收站，可捞回）。快照保留，所以不影响回滚。",
                    { goal }))) return;
    if (btn) btn.disabled = true;
    try {
      const got = await apiJson(`/api/agent/runs/${id}`, { method: "DELETE" });
      const warn = (got && got.errors || []).length;
      toast(T("已删除这条记录"), warn ? T("有 {n} 处没能清理，详见控制台", { n: warn }) : "");
      if (warn) console.warn("[Workbench] 删除遗留：", got.errors);
      await refreshAfterDelete();
    } catch (e) {
      toast(T("删除失败"), e.message, true);
      if (btn) btn.disabled = false;
    }
  }

  async function deleteRuns(ids, btn) {
    if (!ids.length) { toast(T("这里没有可删的记录"), ""); return; }
    if (!confirm(T("删掉当前列出的 {n} 条记录？会一并删掉它们在项目里的留档文件夹（进回收站，可捞回）。",
                    { n: ids.length }))) return;
    if (btn) btn.disabled = true;
    try {
      const got = await apiJson("/api/agent/runs/purge", { method: "POST", body: JSON.stringify({ ids }) });
      const warn = (got && got.errors || []).length;
      toast(T("已删除 {n} 条记录", { n: (got && got.deleted) || 0 }),
            warn ? T("有 {n} 处没能清理", { n: warn }) : "");
      if (warn) console.warn("[Workbench] 删除遗留：", got.errors);
      await refreshAfterDelete();
    } catch (e) {
      toast(T("删除失败"), e.message, true);
      if (btn) btn.disabled = false;
    }
  }

  async function refreshAfterDelete() {
    await renderRuns();
    await refreshStatus();          // 成本仪表 / 工作区数字都从 status 来
    renderCost();
    setNavBadge();
    renderTodoPane();               // 待办角标也可能受影响（理论上不会，但一起刷新最省事）
  }

  /** 「再跑一次」：把目标与工作区填回委托框，并**直接开跑**（审批照旧，不自动批准）。 */
  async function rerun(runId) {
    const r = (ST.runs || []).find((x) => x.id === runId);
    if (!r) return;
    const g = q("#wbGoal");
    if (g) g.value = r.goal || "";
    const sel = q("#wbWs");
    if (sel && !sel.disabled && r.workspaceId
        && (ST.workspaces || []).some((w) => Number(w.id) === Number(r.workspaceId))) {
      sel.value = String(r.workspaceId);
    }
    clearPendingTodo();                 // 再跑一次 ≠ 又替那条待办干活（待办已经勾掉了）
    if (r.todoId) {
      // 重新挂上原来那条待办：这次重跑还是替它干，跑完该按同样的规则收尾
      const t = todosNow().find((x) => x.id === r.todoId);
      ST.pendingTodo = { id: r.todoId, text: (t && t.text) || r.goal || "" };
    }
    renderPendingTodo();
    await startRun();
  }

  /* ---------------- 工作区面板 ---------------- */
  async function renderWs() {
    const box = q("#wbWsList");
    if (!box) return;
    if (isGuest()) { box.innerHTML = `<div class="wb-empty">${T("访客模式看不到工作区：工程委托要登录（运行记录与快照需要落库）。")}</div>`; return; }
    const list = ST.workspaces || [];
    if (!list.length) { box.innerHTML = `<div class="wb-empty">${T("还没有登记任何工作区。填一个项目目录，登记后就能把活交给小玉。")}</div>`; return; }
    box.innerHTML = list.map((w) => `
      <div class="wb-ws" data-ws="${esc(w.id)}">
        <div class="wbw-top">
          <b>${esc(w.name)}</b>
          ${w.exists ? "" : `<span class="wbw-missing">${T("目录已不存在")}</span>`}
        </div>
        <div class="wbw-root" title="${esc(w.root)}">${esc(w.root)}</div>
        <div class="wbw-nums">
          <span>${T("最近使用")}：${esc(fmtWhen(w.lastUsed))}</span>
        </div>
        <!-- ★ 验证命令：这是「工作台干的活能不能被证明」的唯一依据，所以放在最显眼处、并且能直接改。
             她踩过：工作区没配验证命令 → 内核没命令可跑 → 每次运行都只能标「未验证」，
             看上去像"工作台干的活没法验证"。 -->
        <div class="wbw-vf" data-vf-view>
          ${(w.verifyCmds && w.verifyCmds.length)
            ? `<span class="wbw-vf-tag ok">${T("验证命令")}</span>`
              + w.verifyCmds.map((c) => `<code>${esc(c)}</code>`).join("")
            : `<span class="wbw-vf-tag bad">${T("没有验证命令")}</span>
               <span class="wbw-vf-why">${T("内核没法自己证明改动是好的，跑完只能标「未验证」")}</span>`}
        </div>
        <div class="wbw-vf-edit" data-vf-edit hidden>
          <textarea rows="2" maxlength="600" data-vf-input
                    placeholder="一行一条命令；全部退出码 0 才算通过"
                    data-i18n-ph="一行一条命令；全部退出码 0 才算通过"></textarea>
          <div class="wbw-vf-ops">
            <button class="primary-btn slim" type="button" data-vf-act="save">${T("保存")}</button>
            <button class="ghost-btn slim" type="button" data-vf-act="detect-edit">${T("自动探测")}</button>
            <button class="ghost-btn slim" type="button" data-vf-act="cancel">${T("取消")}</button>
            <span class="wbw-vf-hint">${T("留空 = 明确不要自动验证")}</span>
          </div>
        </div>
        ${(w.mcpServers && w.mcpServers.length) ? `<div class="wbw-mcp" title="${esc(w.mcpServers.map((s) => s.name + " ← " + s.command + " " + (s.args || []).join(" ")).join("\n"))}">${T("MCP 服务器")}：${esc(w.mcpServers.map((s) => s.name).join(" · "))}</div>` : ""}
        <div class="wbw-git" hidden></div>
        <div class="wbr-ops">
          <button class="ghost-btn slim" data-ws-act="use">${T("用它跑委托")}</button>
          <button class="ghost-btn slim" data-ws-act="verify">${T("改验证命令")}</button>
          <button class="ghost-btn slim" data-ws-act="detect">${T("自动探测")}</button>
          <button class="ghost-btn slim" data-ws-act="git">${T("看 git 状态")}</button>
          <button class="danger-btn slim" data-ws-act="del">${T("移出列表")}</button>
        </div>
      </div>`).join("");
    qa("[data-ws]", box).forEach((el) => {
      const id = Number(el.dataset.ws);
      const w = (ST.workspaces || []).find((x) => Number(x.id) === id) || { id, verifyCmds: [] };
      qa("[data-ws-act]", el).forEach((b) => {
        b.onclick = async () => {
          const act = b.dataset.wsAct;
          if (act === "use") {
            setTab("run");
            const sel = q("#wbWs");
            if (sel && !sel.disabled) sel.value = String(id);
            const g = q("#wbGoal");
            if (g) g.focus();          // 切过来就是为了写目标，顺手把光标放进去
          } else if (act === "verify") {
            openVerifyEdit(el, w);
          } else if (act === "detect") {
            await detectVerify(el, id, false);
          } else if (act === "git") {
            await showGit(el, id);
          } else if (act === "del") {
            if (!confirm(T("只从列表里移除，不会动磁盘上的目录。继续？"))) return;
            try {
              await apiJson(`/api/agent/workspaces/${id}`, { method: "DELETE" });
              toast(T("已移除"), "");
              await refreshStatus();
              renderWs();
            } catch (e) { toast(T("移除失败"), e.message, true); }
          }
        };
      });
      qa("[data-vf-act]", el).forEach((b) => {
        b.onclick = async () => {
          const act = b.dataset.vfAct;
          if (act === "save") await saveVerify(el, w);
          else if (act === "cancel") closeVerifyEdit(el);
          else if (act === "detect-edit") await detectVerify(el, id, true);
        };
      });
    });
  }

  async function showGit(el, id) {
    const box = q(".wbw-git", el);
    if (!box) return;
    box.hidden = false;
    box.textContent = T("读取中…");
    try {
      const r = await apiJson(`/api/agent/workspaces/${id}/git`);
      const g = r.git || {};
      box.innerHTML = g.isRepo
        ? `<span class="wbc-chip">${esc(g.branch)}</span>
           <span>${T("改动")} ${esc(g.dirty)}</span>
           <span>${T("暂存")} ${esc(g.staged)}</span>
           <span>${T("未跟踪")} ${esc(g.untracked)}</span>
           <div class="wbw-last">${esc(g.lastCommit || "")}</div>`
        : `<span class="wbc-chip-quiet">${esc(g.error || T("不是 git 仓库"))}</span>`;
    } catch (e) { box.textContent = T("读取失败：{msg}", { msg: e.message }); }
  }

  /* ---------------- 工作区：验证命令的查看 / 编辑 / 自动探测 ----------------
     为什么这块要显眼：**"工作台干的活能不能被证明"全靠它**。
     她踩过的坑就是工作区没配验证命令 → 内核没命令可跑 → 每次运行都只能标「未验证」，
     看上去像"工作台干的活没法验证"，其实是**没得验**。 */
  function openVerifyEdit(el, w) {
    const view = q("[data-vf-view]", el);
    const edit = q("[data-vf-edit]", el);
    const input = q("[data-vf-input]", el);
    if (!edit || !input) return;
    input.value = (w.verifyCmds || []).join("\n");
    if (view) view.hidden = true;
    edit.hidden = false;
    input.focus();
  }

  function closeVerifyEdit(el) {
    const view = q("[data-vf-view]", el);
    const edit = q("[data-vf-edit]", el);
    if (view) view.hidden = false;
    if (edit) edit.hidden = true;
  }

  async function saveVerify(el, w) {
    const input = q("[data-vf-input]", el);
    const cmds = String((input && input.value) || "").split("\n")
      .map((s) => s.trim()).filter(Boolean);
    try {
      await apiJson(`/api/agent/workspaces/${w.id}`,
                    { method: "PATCH", body: JSON.stringify({ verifyCmds: cmds }) });
      await refreshStatus();
      renderWs();
      toast(T("验证命令已保存"), cmds.length
        ? T("下次跑完就会自己跑这几条命令来证明")
        : T("留空 = 不自动验证，跑完会标「未验证」"));
    } catch (e) { toast(T("保存失败"), e.message, true); }
  }

  /** 自动探测：**只认得出时才改**，认不出来如实说清楚（不瞎猜一条能跑挂的命令） */
  async function detectVerify(el, id, intoEditor) {
    const btn = q("[data-ws-act='detect'], [data-vf-act='detect-edit']", el);
    if (btn) btn.disabled = true;
    try {
      const r = await apiJson(`/api/agent/workspaces/${id}/detect`, { method: "POST", body: "{}" });
      const found = r.detected || [];
      if (found.length) {
        if (intoEditor) {
          const input = q("[data-vf-input]", el);
          if (input) input.value = found.join("\n");
          toast(T("探测到了一条验证命令"), found.join("；") + " —— " + T("点「保存」生效"));
        } else {
          await refreshStatus();
          renderWs();
          toast(T("探测到并已配上验证命令"), found.join("；"));
        }
      } else {
        toast(T("没认出这是什么项目"), T("请手动填一条 —— 猜一条跑不通的命令比留空更糟"));
      }
    } catch (e) { toast(T("探测失败"), e.message, true); }
    if (btn) btn.disabled = false;
  }

  /* ★ 「浏览…」：桌面版弹**原生目录选择器**（pywebview 的 create_file_dialog），
     省得手抄一长串路径 —— 这是她实测反馈的点（"只能输入，不能用资源管理器选"）。

     两个前提，所以这里写得比看上去啰嗦：
     ① pywebview 的 api 是**页面加载完之后**才注入的（会发一个 pywebviewready 事件），
        所以既要在绑定时试一次，也要监听那个事件 —— 否则第一次进页面按钮永远不出现；
     ② 浏览器里根本没有这个能力（网页拿不到绝对路径），所以那时**隐藏按钮**、只留手输，
        而不是留一个点了没反应的按钮。 */
  function wireNativePicker() {
    const btn = q("#wbWsPick");
    const input = q("#wbWsPath");
    if (!btn || !input) return;
    const api = (window.pywebview && window.pywebview.api) || null;
    const can = !!(api && typeof api.pick_folder === "function");
    btn.hidden = !can;
    if (!can) return;
    btn.onclick = async () => {
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = T("正在打开…");
      try {
        const picked = await api.pick_folder();
        if (picked) { input.value = picked; input.focus(); }
      } catch (e) {
        toast(T("打不开目录选择器"), (e && e.message) || String(e), true);
      } finally {
        btn.disabled = false;
        btn.textContent = label;
      }
    };
  }
  window.addEventListener("pywebviewready", wireNativePicker);

  async function addWorkspace() {
    const input = q("#wbWsPath");
    const path = (input.value || "").trim();
    if (!path) { toast(T("请填写项目目录"), ""); input.focus(); return; }
    const btn = q("#wbWsAdd");
    btn.disabled = true;
    try {
      const r = await apiJson("/api/agent/workspaces", { method: "POST", body: JSON.stringify({ path }) });
      toast(T("工作区已登记"), r.workspace ? r.workspace.root : "");
      input.value = "";
      await refreshStatus();
      renderWs();
    } catch (e) { toast(T("登记失败"), e.message, true); }
    btn.disabled = false;
  }

  /* ---------------- 成本面板 ---------------- */
  function money(tokensIn, tokensOut) {
    const p = ST.prices || {};
    if (!p.in && !p.out) return "";
    const v = (tokensIn || 0) / 1e6 * (p.in || 0) + (tokensOut || 0) / 1e6 * (p.out || 0);
    return v.toFixed(4) + " " + (p.currency || "CNY");
  }

  async function renderCost() {
    const box = q("#wbCostBody");
    if (!box) return;
    if (isGuest()) {
      box.innerHTML = `<div class="wb-empty">${T("访客模式看不到成本：工程委托要登录（运行记录与快照需要落库）。")}</div>`;
      return;
    }
    let data = null;
    try { data = await apiJson("/api/agent/stats"); } catch (e) { box.innerHTML = `<div class="wb-empty">${esc(e.message)}</div>`; return; }
    ST.stats = data.stats || {};
    ST.prices = data.prices || ST.prices;
    ST.runs = data.runs || ST.runs;
    const s = ST.stats;
    const total = (s.tokensIn || 0) + (s.tokensOut || 0);
    const rate = s.runs ? Math.round((s.done / s.runs) * 100) : 0;
    const rows = (ST.runs || []).slice(0, 20);
    box.innerHTML = `
      <div class="wbc-stats wbc-stats-grid">
        <div class="wbs"><b>${esc(s.runs || 0)}</b><span>${T("委托次数")}</span></div>
        <div class="wbs"><b>${rate}%</b><span>${T("完成率")}</span></div>
        <div class="wbs"><b>${esc(s.steps || 0)}</b><span>${T("总步数")}</span></div>
        <div class="wbs"><b>${esc(s.toolCalls || 0)}</b><span>${T("工具调用")}</span></div>
        <div class="wbs"><b>${fmtTok(total)}</b><span>token</span></div>
        <div class="wbs"><b>${esc(s.approvals || 0)}</b><span>${T("审批次数")}</span></div>
        <div class="wbs"><b>${esc(s.denied || 0)}</b><span>${T("被你拒绝")}</span></div>
        <div class="wbs"><b>${esc(s.undos || 0)}</b><span>${T("回滚次数")}</span></div>
        <div class="wbs"><b>${esc(s.verifyFailed || 0)}</b><span>${T("验证未通过")}</span></div>
        <div class="wbs"><b>${esc(s.workspaces || 0)}</b><span>${T("工作区")}</span></div>
      </div>
      <div class="wb-price">
        <div class="wbc-hd">${T("单价（每百万 token）")}
          <span class="wbc-chip-quiet">${T("留空就不显示金额 —— 不内置一份一定会过期的价格表")}</span></div>
        <div class="wb-price-row">
          <label><span>${T("输入")}</span><input type="number" min="0" step="0.01" id="wbPriceIn" value="${esc(ST.prices.in || 0)}"></label>
          <label><span>${T("输出")}</span><input type="number" min="0" step="0.01" id="wbPriceOut" value="${esc(ST.prices.out || 0)}"></label>
          <label><span>${T("币种")}</span><input type="text" maxlength="8" id="wbPriceCur" value="${esc(ST.prices.currency || "CNY")}"></label>
          <button class="primary-btn slim" id="wbPriceSave">${T("保存单价")}</button>
        </div>
        <div class="wbc-why">${T("总花费（按当前单价估算）：")}${money(s.tokensIn, s.tokensOut) || T("未设置单价")}</div>
      </div>
      <div class="wb-table">
        <div class="wbc-hd">${T("最近运行")}</div>
        ${rows.length ? rows.map((r) => `
          <div class="wb-row" data-run="${esc(r.id)}">
            <span class="wbr-status wbr-${esc(r.status)}">${esc(statusText(r.status))}</span>
            <span class="wbrow-goal">${esc((r.goal || "").slice(0, 46))}</span>
            <span>${esc(r.steps || 0)}${T("步")}</span>
            <span>${fmtTok((r.tokensIn || 0) + (r.tokensOut || 0))}</span>
            <span>${money(r.tokensIn, r.tokensOut) || "—"}</span>
          </div>`).join("") : `<div class="wb-empty">${T("暂无数据")}</div>`}
      </div>`;
    const btn = q("#wbPriceSave");
    if (btn) btn.onclick = async () => {
      try {
        const r = await apiJson("/api/agent/prices", { method: "POST", body: JSON.stringify({
          in: Number(q("#wbPriceIn").value || 0), out: Number(q("#wbPriceOut").value || 0),
          currency: q("#wbPriceCur").value || "CNY" }) });
        ST.prices = r.prices;
        toast(T("单价已保存"), "");
        renderCost();
      } catch (e) { toast(T("保存失败"), e.message, true); }
    };
  }

  /* ---------------- 初始化 ---------------- */
  function bind() {
    ensureShell();                       // 外壳要先有，里面的控件才绑得上
    const s = shell;
    const openBtn = q("#btnWorkbench");  // 对话页顶栏：快捷入口（抽屉）
    if (openBtn) openBtn.onclick = () => toggle();
    const closeBtn = q("#wbClose", s);
    if (closeBtn) closeBtn.onclick = close;
    const scrim = q("#wbScrim");
    if (scrim) scrim.onclick = close;
    const ref = q("#wbRefresh", s);
    if (ref) ref.onclick = async () => {
      await refreshStatus();
      renderRuns(); renderWs(); renderCost(); renderGoalHistory(); renderPendingTodo();
      await refreshTodoState();
      toast(T("已刷新"), "");
    };
    qa(".wb-tab", s).forEach((b) => { b.onclick = () => setTab(b.dataset.wbtab); });
    const start = q("#wbStart", s);
    if (start) start.onclick = startRun;
    const stop = q("#wbStop", s);
    if (stop) stop.onclick = stopRun;
    const addWs = q("#wbWsAdd", s);
    if (addWs) addWs.onclick = addWorkspace;
    wireNativePicker();

    /* ---- 待办分区 ---- */
    const todoAdd = q("#wbTodoAdd", s);
    if (todoAdd) todoAdd.onsubmit = async (e) => {
      e.preventDefault();
      const input = q("#wbTodoText");
      const text = (input.value || "").trim();
      if (!text) return;
      const btn = q("#wbTodoAdd button[type=submit]");
      if (btn) btn.disabled = true;
      try {
        const r = await apiJson("/api/todos", { method: "POST", body: JSON.stringify({ text }) });
        if (r && Array.isArray(r.todos)) ST.todos = r.todos;
        syncTodoToApp();
        input.value = "";
        await refreshTodoState();
        toast(T("✅ 已添加"), T("点「交给小玉做」就能把它派给小玉"));
      } catch (err) { toast(T("添加失败"), err.message, true); }
      if (btn) btn.disabled = false;
      input.focus();
    };
    const todoFilters = q("#wbTodoFilters", s);
    if (todoFilters) todoFilters.onclick = (e) => {
      const b = e.target.closest(".filter-btn");
      if (!b) return;
      ST.todoFilter = b.dataset.todof;
      renderTodoPane();
    };
    const pendingClear = q("#wbPendingClear", s);
    if (pendingClear) pendingClear.onclick = () => {
      clearPendingTodo();
      toast(T("已取消关联"), T("这次委托不再自动勾任何待办"));
    };

    /* ---- 运行记录：搜索与筛选 ---- */
    const search = q("#wbRunSearch", s);
    if (search) {
      search.value = ST.runSearch;
      search.addEventListener("input", () => { ST.runSearch = search.value; renderRuns(); });
    }
    const runFilters = q("#wbRunFilters", s);
    if (runFilters) runFilters.onclick = (e) => {
      const b = e.target.closest(".filter-btn");
      if (!b) return;
      ST.runStatus = b.dataset.runst;
      renderRuns();
    };

    /* ---- 「跑完把结论发到对话里」这个小开关，记在本地 ---- */
    const toChat = q("#wbToChat", s);
    if (toChat) {
      toChat.checked = lsGet(TOCHAT_KEY, "1") !== "0";
      toChat.onchange = () => lsSet(TOCHAT_KEY, toChat.checked ? "1" : "0");
    }
    const auto = q("#wbAuto", s);
    const autoWarn = q("#wbAutoWarn", s);
    if (auto) {
      // ⚠️ 自动批准**刻意不沿用上次**：它是"关掉审批闸门"，静默继承等于下次无声地跳过所有确认。
      //   而超时时间、结论回对话、上次的工作区这些沿用无所谓。
      const syncAutoWarn = () => { if (autoWarn) autoWarn.hidden = !auto.checked; };
      auto.onchange = syncAutoWarn;
      syncAutoWarn();
    }
    const tmo = q("#wbTimeout", s);
    if (tmo) {
      const saved = lsGet(TMO_KEY, "");
      if (saved && [...tmo.options].some((o) => o.value === saved)) tmo.value = saved;
      tmo.onchange = () => lsSet(TMO_KEY, tmo.value);
    }
    renderTemplates();
    renderGoalHistory();
    renderPendingTodo();
    setNavBadge();

    const fast = q("#wbGoal", s);
    if (fast) fast.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") startRun();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && ST.open) close();
    });
    // 语言切换：动态区域自己重绘（I18N.setLang 会广播这个事件；onLangChange 由 app.js 兜一层）
    document.addEventListener("i18n:changed", onLangChange);
    syncMountWithActiveTab();
  }

  /* 启动时如果上次停在「工作台」页（restoreLastTab 恢复的），外壳要挂在页面这边 */
  function syncMountWithActiveTab() {
    const sec = q("#tab-workbench");
    if (sec && sec.classList.contains("active")) showPage();
  }

  /* 供 app.js 在语言切换时调用（refreshI18nText 里挂一行） */
  function onLangChange() {
    if (ST.open) { renderRuns(); renderWs(); renderCost(); renderGoalHistory();
                   renderPendingTodo(); renderTodoPane(); renderTemplates(); }
    setNavBadge();
  }

  /** 从对话页的「工作台结论」小标点进来：直接回放那一次委托 */
  function showRun(runId) {
    if (!runId) return;
    open();                 // 走右侧抽屉（不切走当前页）
    setTab("runs");
    openRun(runId);
  }

  /** 从对话页的「让小玉来处理」卡片一键开工（快捷抽屉） */
  function openWithGoal(goal) { open(goal); setTab("run"); }

  /** 初始化失败时，把原因**写在界面上** —— 绝不让它表现为「点进来一片空白」 */
  function reportFailure(err) {
    const msg = (err && (err.stack || err.message)) || String(err);
    console.error("[Workbench] 初始化失败：", err);
    const box = (ensureHost("page") || ensureHost("drawer"));
    if (box) {
      box.innerHTML = `<div class="wb-fail"><b>${esc(T("工作台初始化失败"))}</b>
        <p>${esc(T("请先刷新页面（Ctrl+F5）。如果仍然失败，把下面这条消息发给开发者："))}</p>
        <pre>${esc(msg).slice(0, 800)}</pre>
        <button class="primary-btn slim" onclick="location.reload()">${esc(T("刷新页面"))}</button></div>`;
    }
    try {
      if (typeof toast === "function") toast(T("工作台初始化失败"), T("请刷新页面（Ctrl+F5）"), true);
    } catch (e) { /* 连 toast 都没有就算了，页面上已经写了 */ }
  }

  function boot() {
    try {
      bind();
    } catch (e) {
      reportFailure(e);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  // app.js 的 init 是异步的（要先拉 /api/state），恢复「上次停在哪个页」发生在它之后，
  // 所以这里再补一次挂载检查。
  setTimeout(() => {
    try {
      syncMountWithActiveTab();
      resumeOnBoot();
      // 待办角标与「来自待办」提示要趁陪伴层已经把状态拉回来之后再画
      renderGoalHistory();
      renderPendingTodo();
      renderTodoPane();
    } catch (e) { reportFailure(e); }
  }, 1500);

  return { open, close, toggle, setTab, openWithGoal, showRun, onLangChange, onTab, resumeOnBoot,
           syncMountWithActiveTab, delegateTodo, refreshTodos: refreshTodoState,
           get state() { return ST; } };
})();
