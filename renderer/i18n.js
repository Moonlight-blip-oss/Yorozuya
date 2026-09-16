/* ============================================================
   Yorozuya · 轻量 i18n
   ------------------------------------------------------------
   设计取舍：**用中文原文当 key**（gettext 风格），而不是 app.setting.title 这类符号名。
   好处有两个，都是实打实的：
     ① HTML 里本来就写着中文，语言包缺失 / 加载失败时界面原样保留中文，**永远不会变空白**；
     ② 新增文案时不用同时维护「键名表」和「文案表」，少一处出错的地方。
   语言包放 renderer/locales/{zh-CN,en-US}.json：
     - zh-CN.json 是源语言映射（key === value），保证两个包键集完全一致，方便比对；
     - en-US.json 是译文。
   用法：
     - 静态文案：给元素加 data-i18n="中文原文"（会自动替换 textContent）
                含内嵌标签的用 data-i18n-html="…"（替换 innerHTML）
                占位符/提示用 data-i18n-ph / data-i18n-title / data-i18n-aria
     - 动态文案：T("和{name}聊天", { name })
   ============================================================ */
const I18N = (() => {
  const SUPPORTED = ["zh-CN", "en-US"];
  const FALLBACK = "zh-CN";
  const packs = {};            // lang -> { 中文: 译文 }
  let pref = FALLBACK;         // 用户在设置里选的（可能是 auto）
  let lang = FALLBACK;         // 解析后的实际语言

  /** 用户选择 → 实际语言；auto 看浏览器语言 */
  function resolve(p) {
    if (!p || p === "auto") {
      const n = (navigator.languages && navigator.languages[0]) || navigator.language || "";
      return /^zh/i.test(n) ? "zh-CN" : "en-US";
    }
    if (p === "en") return "en-US";                        // 兼容旧值
    return SUPPORTED.indexOf(p) >= 0 ? p : FALLBACK;       // zh-TW / ja 等未支持 → 回退中文
  }

  /** 载入语言包（失败就保持中文兜底，不打断启动） */
  async function load() {
    await Promise.all(SUPPORTED.map(async (l) => {
      try {
        const r = await fetch("locales/" + l + ".json", { cache: "no-store" });
        if (r.ok) packs[l] = await r.json();
      } catch (e) { /* 静态预览 / 离线：忽略，界面用中文原文 */ }
    }));
    return packs;
  }

  /** 取译文；缺键返回 key 本身（= 中文原文），{name} 之类做插值 */
  function t(key, params) {
    if (key == null) return "";
    const pack = packs[lang] || {};
    let s = Object.prototype.hasOwnProperty.call(pack, key) ? pack[key] : key;
    if (params) {
      s = String(s).replace(/\{(\w+)\}/g, (m, k) =>
        (params[k] === undefined || params[k] === null) ? m : String(params[k]));
    }
    return s;
  }

  /** 把当前语言刷到静态 DOM 上（幂等，可反复调用） */
  function applyStatic(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(el => {
      const v = t(el.dataset.i18n);
      if (v !== el.textContent) el.textContent = v;
    });
    scope.querySelectorAll("[data-i18n-html]").forEach(el => {
      const v = t(el.dataset.i18nHtml);
      if (v !== el.innerHTML) el.innerHTML = v;
    });
    scope.querySelectorAll("[data-i18n-ph]").forEach(el => {
      const v = t(el.dataset.i18nPh);
      if (el.placeholder !== v) el.placeholder = v;
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(el => {
      const v = t(el.dataset.i18nTitle);
      if (el.title !== v) el.title = v;
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach(el => {
      const v = t(el.dataset.i18nAria);
      if (el.getAttribute("aria-label") !== v) el.setAttribute("aria-label", v);
    });
  }

  /** 切换语言：刷新静态文案 + 广播事件，让动态区域自行重绘 */
  function setLang(nextPref) {
    pref = nextPref || FALLBACK;
    lang = resolve(pref);
    document.documentElement.lang = lang;
    applyStatic();
    try {
      document.dispatchEvent(new CustomEvent("i18n:changed", { detail: { lang, pref } }));
    } catch (e) { /* 老内核：忽略 */ }
    return lang;
  }

  /** 日期也要跟着语言走（toLocaleDateString 的 locale） */
  const dateLocale = () => (lang === "en-US" ? "en-US" : "zh-CN");

  return {
    load, t, setLang, applyStatic, dateLocale,
    get lang() { return lang; },
    get isEnglish() { return lang === "en-US"; },
  };
})();

/** 全局短别名：T("和{name}聊天", { name }) */
const T = (key, params) => I18N.t(key, params);
