/* ============================================================
   Yorozuya · 主题提供者（personaThemes）
   ------------------------------------------------------------
   主题配置的唯一前端出口：把后端下发的 personaTheme 注入成 CSS 变量。
   组件只消费 var(--...)，因此**切换人格时只改变量、不重绘整页**。

   契约（后端 yorozuya/personas.py 的 THEMES 一一对应）：
     scheme   dark / light           这套底色的明暗（决定正文色与纹理取反）
     primary  主色（基底基调）        炭黑 / 米白 / 浅灰
     accent   点缀色                  银灰 / 旗袍红 / 眼镜蓝（只用于小面积）
     edge     边缘光                  描边 / 光晕
     texture  纹理类型                wood / washi / washi-warm
     icon     专属图标                {main, alt}
   说明：本项目前端是原生 JS（无 TS 构建链），故文件名为 .js；
   结构与命名按需求保持 personaThemes 语义，便于日后迁到 TS。
   ============================================================ */
"use strict";

const PersonaThemes = (() => {
  /* 后端字段 → CSS 变量 的映射表（组件侧只认变量名，不认后端字段） */
  const VAR_MAP = {
    "--primary": "primary",
    "--accent": "accent",
    "--accent-2": "accent2",
    "--accent-deep": "accentDeep",
    "--on-accent": "onAccent",
    "--accent-soft": "soft",
    "--edge": "edge",
    "--bg": "bg",
    "--panel": "panel",
    "--side": "side",
    "--side-alt": "sideAlt",
    "--side-glass": "sideGlass",
    "--panel-glass": "panelGlass",
    "--ink": "ink",
    "--ink-2": "ink2",
    "--ink-3": "ink3",
    "--line": "line",
    "--tint": "tint",
    "--brand": "brand",
    "--ring": "ring",
    "--bg-grad": "gradient",
    "--panel-grad": "panelGrad",
    "--accent-grad": "accentGrad",
    "--sheen": "sheen",
    "--glow": "glow",
    /* 气泡：角色气泡（米白/浅灰 + 深字）与用户气泡（深炭 + 白字） */
    "--ai-bubble": "aiBubble",
    "--ai-ink": "aiInk",
    "--user-bubble": "userBubble",
    "--user-ink": "userInk",
  };

  let current = null;          // 当前主题对象
  let mode = "auto";           // auto(跟随角色) / light / dark / system
  let allThemes = {};          // 全部主题（按 id），供 FloatingMascot 等按人格取配置

  /** 兜底：即使后端没给 mascot 字段，组件也能正常跑 */
  const MASCOT_DEFAULT = {
    image: "assets/mascot/gintoki_chibi.png",
    size: 84,
    aspect: "2 / 3",             // 立绘比例（与 mascotImage 的像素比一致）
    anim: { floatDuration: "3s", floatDistance: "-6px", fadeDuration: "0.3s" },
    lines: ["……"],
  };

  /** 头像兜底：各处头像（顶栏 / 气泡旁 / 左下悬浮按钮）统一走 avatar()，
      组件与业务代码都不再硬编码任何图片路径 */
  const AVATAR_DEFAULT = "assets/avatar/gintoki_avatar.png";

  /** 记录整套主题（renderAll 时调用），这样按任意 personaId 都能取到配置 */
  function setAll(map) { allThemes = map || {}; }

  /**
   * FloatingMascot 的配置：形象路径 / 尺寸 / 动画参数 / 点击台词。
   * 组件只消费这里返回的形状，不关心后端字段名，也不硬编码任何路径与台词。
   */
  function mascot(pid) {
    const t = allThemes[pid] || (current && current.key === pid ? current : null) || {};
    const anim = t.mascotAnim || {};
    return {
      personaId: pid || (t.key || ""),
      image: t.mascotImage || MASCOT_DEFAULT.image,
      size: t.mascotSize || MASCOT_DEFAULT.size,
      aspect: t.mascotAspect || MASCOT_DEFAULT.aspect,
      anim: {
        floatDuration: anim.floatDuration || MASCOT_DEFAULT.anim.floatDuration,
        floatDistance: anim.floatDistance || MASCOT_DEFAULT.anim.floatDistance,
        fadeDuration: anim.fadeDuration || MASCOT_DEFAULT.anim.fadeDuration,
      },
      lines: (t.mascotLines && t.mascotLines.length) ? t.mascotLines : MASCOT_DEFAULT.lines,
    };
  }

  /**
   * 各处头像的统一来源（顶栏 / 聊天气泡旁 / 左下悬浮按钮）。
   * 业务代码只调用它，不出现任何硬编码图片路径。
   */
  function avatar(pid) {
    const t = allThemes[pid] || (current && current.key === pid ? current : null) || {};
    return t.avatarImage || AVATAR_DEFAULT;
  }

  const prefersDark = () =>
    !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);

  /** 是否被「强制」成深/浅：auto 模式下两者都是 false，交给角色自己的底色 */
  const forcedDark = () => mode === "dark" || (mode === "system" && prefersDark());
  const forcedLight = () => mode === "light" || (mode === "system" && !prefersDark());

  const isDarkMode = () => {
    if (forcedDark()) return true;
    if (forcedLight()) return false;
    return current ? current.scheme === "dark" : false;   // auto：听角色的
  };

  /** 这套主题实际呈现的明暗（用于选择纹理反色等） */
  const effectiveScheme = () => (isDarkMode() ? "dark" : "light");

  /* 「强制浅色 / 深色」的表面色调色板。
     ⚠️ 必须放在这里、以【内联样式】下发，不能写进 CSS：
        角色主题变量本身就是 apply() 内联写在 <html> 上的，而**内联样式优先级高于任何样式表规则**，
        所以写在 CSS 里的 `html.force-dark { --bg: … }` 永远赢不了 —— 那是死代码。
        （这就是「点了深色却没反应」的根因，实测：类加上了、mode 变了，计算值仍停在角色底色。）
     只覆盖「表面色」：强调色 / 身份色 / 图标仍跟随角色，保证换明暗后人设不丢。 */
  /* 强制明暗：**只换语义令牌，不动角色色相** —— --accent / --accent-deep 这些
     「角色色」故意保留（强调色要跟随角色，界面才有辨识度）；
     但"角色色"里同时混了两种用途，深色下必须拆开：
       · 当**底色 / 渐变**用 → 保留（深色底上照样好看）
       · 当**文字**用 → 必须换成浅色档，否则是深底深字（实测 2.4:1）
     所以这里补上 --accent-text / --accent-soft / --ink-3：
     漏掉它们的代价是「深色模式下导航标签、人格面板、等级数字、软色底标签全都看不清」。 */
  const FORCE_PALETTE = {
    dark: {
      "--bg": "#141414", "--panel": "#1E1E1E", "--side": "#161616", "--side-alt": "#242424",
      "--side-glass": "rgba(18,18,18,.68)", "--panel-glass": "rgba(26,26,26,.64)",
      "--ink": "#EDEDED", "--ink-2": "#B4B4B4", "--ink-3": "#9DA2A7", "--line": "#2E2E2E",
      "--accent-text": "#DCE2E8",                       // 强调底/软底上的文字：浅
      "--accent-soft": "rgba(255,255,255,.10)",         // 软色底：浅色→深色，文字才压得住
      "--ai-bubble": "#262626", "--ai-ink": "#EDEDED",
      "--user-bubble": "#343434", "--user-ink": "#F7F7F7",
      /* ★ --panel-grad 也必须换：它是**不透明的浅色渐变**，而且 background-image 画在
         background-color 之上 —— 只换 --panel 是没用的，卡片/聊天输入框/头像/会话条目
         在深色下依旧是白底浅字（这正是"待办、记忆里的字看不清"的根因）。
         漏了它，任何"只读 background-color"的检查还会给你一个假通过。 */
      "--panel-grad": "linear-gradient(160deg, #232323 0%, #1E1E1E 55%, #191919 100%)",
      "--bg-grad": "linear-gradient(170deg, #1E1E1E 0%, #141414 55%, #0E0E0E 100%)",
    },
    light: {
      "--bg": "#F4F4F5", "--panel": "#FFFFFF", "--side": "#EFEFEF", "--side-alt": "#E7E7E9",
      "--side-glass": "rgba(240,240,241,.68)", "--panel-glass": "rgba(255,255,255,.64)",
      "--ink": "#23272B", "--ink-2": "#545A60", "--ink-3": "#5A6066", "--line": "#E2E3E6",
      "--accent-text": "#4B525A",                       // 浅色主题：强调色上的文字取深档
      "--accent-soft": "#E3E7EB",
      "--ai-bubble": "#FAFAFA", "--ai-ink": "#23272B",
      "--user-bubble": "#2B2B2B", "--user-ink": "#FAFAFA",
      /* 注意：这里**故意不覆盖 --panel-grad**。它带着角色色偏
         （银时冷灰 / 神乐米黄 / 新八浅蓝 / 小玉浅绿），浅色主题下正是靠它区分人格；
         深色主题那边才需要换成中性深色（浅色渐变在深色页面上是「白卡片」，见 dark 段注释）。 */
      "--bg-grad": "linear-gradient(170deg, #FBFBFC 0%, #F1F1F3 55%, #E9EAEC 100%)",
    },
  };

  /** 只把「角色主题」的变量写成内联样式（不含明暗覆盖，也永远不碰 DOM 结构） */
  function writeThemeVars(theme) {
    const r = document.documentElement.style;
    for (const varName in VAR_MAP) {
      const v = theme[VAR_MAP[varName]];
      if (v !== undefined && v !== null && v !== "") r.setProperty(varName, v);
    }
    r.setProperty("--radius", (theme.radius != null ? theme.radius : 12) + "px");
    r.setProperty("--texture-opacity", String(theme.textureOpacity != null ? theme.textureOpacity : 0.05));
    r.setProperty("--wm-img", (typeof PERSONA_MARKS !== "undefined" && theme.key)
      ? PERSONA_MARKS.markURI(theme.key, 0.12) : "none");
  }

  /** 落地「明暗」：auto 时铺角色本色；用户强制时用内联覆盖盖上去。
      顺序是关键 —— 同名内联属性后写的覆盖先写的，所以强制值一定生效。 */
  function syncScheme() {
    const root = document.documentElement;
    const pal = forcedDark() ? FORCE_PALETTE.dark : (forcedLight() ? FORCE_PALETTE.light : null);
    if (pal) {
      for (const k in pal) root.style.setProperty(k, pal[k]);
    } else if (current) {
      writeThemeVars(current);                    // auto：回到角色自己的底色
    }
    root.classList.toggle("force-dark", forcedDark());
    root.classList.toggle("force-light", forcedLight());
    root.classList.toggle("scheme-dark", effectiveScheme() === "dark");
    root.style.setProperty("--scheme", effectiveScheme());
    if (current && typeof PERSONA_MARKS !== "undefined") {
      root.style.setProperty("--texture-img",
        PERSONA_MARKS.texture(current.texture, effectiveScheme()));
    }
  }

  /** 注入主题：只写 CSS 变量，不触碰 DOM 结构 —— 切换零重绘 */
  function apply(theme) {
    if (!theme) return;
    current = theme;
    writeThemeVars(theme);
    syncScheme();                                 // 角色底色之上再叠用户的明暗选择
  }

  /** 外观模式：auto(跟随角色) / light / dark / system */
  function applyMode(next) {
    mode = next || "auto";
    syncScheme();
  }

  /* 「跟随系统」必须真的随系统变 —— 监听 prefers-color-scheme。
     （只监听一次；mode 不是 system 时忽略，避免影响用户明确选的浅/深色） */
  const systemMQ = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  if (systemMQ) {
    const onSystemSchemeChange = () => { if (mode === "system") syncScheme(); };
    if (systemMQ.addEventListener) systemMQ.addEventListener("change", onSystemSchemeChange);
    else if (systemMQ.addListener) systemMQ.addListener(onSystemSchemeChange);   // 老内核兜底
  }

  return {
    apply, applyMode, setAll, mascot, avatar,
  };
})();
