/* ============================================================
   Yorozuya · 角色标志性元素（内联 SVG）
   ------------------------------------------------------------
   为什么不用图片：矢量在 20px 头像和 120px 吉祥物下都清晰，
   能直接吃 CSS 变量做主题联动，也不用往包里塞 PNG。
   每个角色不只靠颜色区分，而是靠「标志物」一眼认人：
     gintoki   银发卷毛（金属反光）+ 死鱼眼 + 草莓牛奶
     shinpachi 蓝镜片反光 + 黑框眼镜 + 吐槽汗滴
     kagura    双丸子头 + 红旗袍盘扣 + 紫伞
   渐变统一放在隐藏 <defs> 里（id 固定、只注入一次），
   避免同一段 SVG 被插入多次时 id 冲突。
   ============================================================ */
"use strict";

const PERSONA_MARKS = (() => {
  /* ---------- 共用材质（银 / 镜片 / 旗袍红 / 伞 / 肤 / 墨） ---------- */
  const SVG_NS = 'xmlns="http://www.w3.org/2000/svg"';
  const DEFS = `
<svg id="pvDefs" ${SVG_NS} width="0" height="0" aria-hidden="true" style="position:absolute">
  <defs>
    <!-- 银发：金属反光（暗→亮→更亮→中灰，模拟高光扫过） -->
    <linearGradient id="pvSilver" x1="0" y1="0" x2="0.7" y2="1">
      <stop offset="0%" stop-color="#8d959d"/>
      <stop offset="26%" stop-color="#dfe4e9"/>
      <stop offset="44%" stop-color="#ffffff"/>
      <stop offset="62%" stop-color="#c4cbd2"/>
      <stop offset="100%" stop-color="#9aa2aa"/>
    </linearGradient>
    <linearGradient id="pvSilverSoft" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="100%" stop-color="#b9c0c7"/>
    </linearGradient>
    <!-- 镜片：玻璃蓝 + 上亮下深的通透感 -->
    <linearGradient id="pvLens" x1="0" y1="0" x2="0.4" y2="1">
      <stop offset="0%" stop-color="#eaf4ff"/>
      <stop offset="45%" stop-color="#9ec9f2"/>
      <stop offset="100%" stop-color="#4A90E2"/>
    </linearGradient>
    <!-- 旗袍红：橙红暖调 -->
    <linearGradient id="pvRed" x1="0" y1="0" x2="0.6" y2="1">
      <stop offset="0%" stop-color="#ff8a6b"/>
      <stop offset="55%" stop-color="#FF6B6B"/>
      <stop offset="100%" stop-color="#d93f36"/>
    </linearGradient>
    <linearGradient id="pvUmbrella" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#b98cd8"/>
      <stop offset="100%" stop-color="#7e57a8"/>
    </linearGradient>
    <radialGradient id="pvSkin" cx="42%" cy="34%" r="72%">
      <stop offset="0%" stop-color="#fff3e6"/>
      <stop offset="100%" stop-color="#f3d9c2"/>
    </radialGradient>
    <linearGradient id="pvHairDark" x1="0" y1="0" x2="0.5" y2="1">
      <stop offset="0%" stop-color="#4b5560"/>
      <stop offset="100%" stop-color="#262c33"/>
    </linearGradient>
    <!-- 镜片反光 / 金属高光扫条 -->
    <linearGradient id="pvGlint" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity=".92"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity=".08"/>
    </linearGradient>
  </defs>
</svg>`;

  /* ---------- 银时：银发卷毛 + 死鱼眼 ---------- */
  const gintoki = (o = {}) => `
<g>
  <!-- 银色卷毛：一圈交叠的卷，金属反光靠渐变 + 高光扫条 -->
  <g fill="url(#pvSilver)">
    <circle cx="32" cy="15" r="9.2"/>
    <circle cx="19" cy="20" r="8.4"/>
    <circle cx="45" cy="20" r="8.4"/>
    <circle cx="13" cy="31" r="7.4"/>
    <circle cx="51" cy="31" r="7.4"/>
    <circle cx="23" cy="11" r="7.2"/>
    <circle cx="41" cy="11" r="7.2"/>
    <circle cx="15" cy="24" r="6.2" opacity=".92"/>
    <circle cx="49" cy="24" r="6.2" opacity=".92"/>
  </g>
  <!-- 高光：头发上缘的一道金属反光 -->
  <path d="M20 12c4-4 9-6 12-6s8 2 12 6" fill="none" stroke="url(#pvGlint)" stroke-width="2.4" stroke-linecap="round"/>
  <path d="M15 23c1-3 3-5 5-6" fill="none" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round" opacity=".75"/>
  <!-- 脸 -->
  <circle cx="32" cy="35" r="16.5" fill="url(#pvSkin)"/>
  <!-- 额前碎发压在脸上，强化「卷毛」轮廓 -->
  <g fill="url(#pvSilverSoft)">
    <path d="M17 30c3-3 7-4 10-2-4 1-7 3-9 6z"/>
    <path d="M47 30c-3-3-7-4-10-2 4 1 7 3 9 6z"/>
  </g>
  <!-- 死鱼眼：两条压平的眼线（他的招牌） -->
  <g stroke="#2b323a" stroke-width="2.1" stroke-linecap="round">
    <path d="M23 36h8.5"/>
    <path d="M32.5 36H41"/>
  </g>
  <path d="M29 43.5c1.6 1.4 4.4 1.4 6 0" fill="none" stroke="#8a6a55" stroke-width="1.6" stroke-linecap="round"/>
  ${o.milk ? `
  <!-- 草莓牛奶：他的精神支柱 -->
  <g transform="translate(45,39)">
    <rect x="0" y="0" width="9" height="13" rx="2.2" fill="#fff" stroke="#c8ccd2" stroke-width=".9"/>
    <rect x="0" y="5" width="9" height="8" rx="2.2" fill="#ff9db4"/>
    <rect x="1.6" y="-4" width="1.5" height="6" rx=".7" fill="#8fd6f0"/>
    <circle cx="4.5" cy="2.6" r="1.5" fill="#ff6f8e"/>
  </g>` : ""}
</g>`;

  /* ---------- 新八：黑框眼镜 + 蓝镜片反光 ---------- */
  const shinpachi = (o = {}) => `
<g>
  <!-- 短发 -->
  <path d="M15 34c0-11 7-19 17-19s17 8 17 19c-1.6-6-4-9-7-10.5 0-4.5-4.5-7-10-7s-10 2.5-10 7C19 25 16.6 28 15 34z" fill="url(#pvHairDark)"/>
  <!-- 脸 -->
  <circle cx="32" cy="36" r="16.5" fill="url(#pvSkin)"/>
  <!-- 脸颊一点吐槽红晕 -->
  <ellipse cx="21" cy="42" rx="3" ry="1.8" fill="#f2a5a0" opacity=".55"/>
  <ellipse cx="43" cy="42" rx="3" ry="1.8" fill="#f2a5a0" opacity=".55"/>
  <!-- 镜框 -->
  <g fill="none" stroke="#2f3a46" stroke-width="2.2">
    <rect x="15.5" y="29.5" width="14.5" height="11" rx="4"/>
    <rect x="34" y="29.5" width="14.5" height="11" rx="4"/>
    <path d="M30 34.5h4"/>
    <path d="M15.5 33l-5-1.5"/>
    <path d="M48.5 33l5-1.5"/>
  </g>
  <!-- 蓝镜片 + 对角反光条（玻璃感） -->
  <g>
    <rect x="16.8" y="30.8" width="12" height="8.4" rx="3.2" fill="url(#pvLens)" opacity=".92"/>
    <rect x="34.9" y="30.8" width="12.4" height="8.4" rx="3.2" fill="url(#pvLens)" opacity=".92"/>
    <path d="M18 39.2l8.5-8.4h2.6l-8.5 8.4z" fill="url(#pvGlint)"/>
    <path d="M21.6 39.2l7-6.9h1.5l-7 6.9z" fill="#fff" opacity=".85"/>
    <path d="M36.1 39.2l8.5-8.4h2.6l-8.5 8.4z" fill="url(#pvGlint)"/>
  </g>
  <!-- 镜片下的眼睛（被镜片压住，加深「本体的眼镜」梗） -->
  <g fill="#333c47">
    <ellipse cx="22.8" cy="35" rx="1.5" ry="1.9"/>
    <ellipse cx="40.9" cy="35" rx="1.5" ry="1.9"/>
  </g>
  <!-- 吐槽的汗滴 -->
  <path d="M51 20c1.6 2.6 2.6 4 2.6 5.2a2.6 2.6 0 1 1-5.2 0c0-1.2 1-2.6 2.6-5.2z" fill="#8fd6f0" opacity=".9"/>
  <path d="M29 46c1.8 1.5 4.4 1.5 6 0" fill="none" stroke="#8a6a55" stroke-width="1.6" stroke-linecap="round"/>
</g>`;

  /* ---------- 神乐：双丸子头 + 红旗袍盘扣 + 紫伞 ---------- */
  const kagura = (o = {}) => `
<g>
  <!-- 紫伞（她的随身武器） -->
  <g transform="translate(32,7)">
    <path d="M-15 4a15 11 0 0 1 30 0z" fill="url(#pvUmbrella)"/>
    <path d="M-15 4a15 11 0 0 1 30 0" fill="none" stroke="#5d3f80" stroke-width="1.1"/>
    <path d="M0 -7v11M-7.5 -5.2l-2 9.2M7.5 -5.2l2 9.2M-3.7 -6.7l-1 10.7M3.7 -6.7l1 10.7"
          stroke="#5d3f80" stroke-width=".9" opacity=".75" fill="none"/>
    <path d="M-1.2 4h2.4v5h-2.4z" fill="#7a5330"/>
  </g>
  ${o.buns !== false ? `
  <!-- 双丸子头 -->
  <circle cx="16" cy="22" r="7.4" fill="url(#pvRed)"/>
  <circle cx="48" cy="22" r="7.4" fill="url(#pvRed)"/>
  <circle cx="14" cy="20" r="2.2" fill="#fff" opacity=".45"/>
  <circle cx="46" cy="20" r="2.2" fill="#fff" opacity=".45"/>` : ""}
  <!-- 头发 -->
  <path d="M15 36c0-11 7-18 17-18s17 7 17 18c-1.4-5.6-4-8.6-7-10 0-4.2-4.5-6.6-10-6.6S22 21.8 22 26c-3 1.4-5.6 4.4-7 10z" fill="url(#pvRed)"/>
  <!-- 脸 -->
  <circle cx="32" cy="36" r="16.5" fill="url(#pvSkin)"/>
  <!-- 红旗袍：立领 + 盘扣 + 金边 -->
  <path d="M20 49c3.4-3.4 7.6-5 12-5s8.6 1.6 12 5c-3 2.6-7.4 4-12 4s-9-1.4-12-4z" fill="url(#pvRed)"/>
  <path d="M20.6 48.6c3.3-3.2 7.4-4.7 11.4-4.7s8.1 1.5 11.4 4.7" fill="none" stroke="#f4d27a" stroke-width="1.2"/>
  <circle cx="32" cy="49.2" r="1.9" fill="#f4d27a"/>
  <circle cx="32" cy="53" r="1.5" fill="#f4d27a" opacity=".85"/>
  <!-- 眼睛 + 笑口 -->
  <g fill="#3a241f">
    <ellipse cx="25.4" cy="35.6" rx="2.3" ry="2.7"/>
    <ellipse cx="38.6" cy="35.6" rx="2.3" ry="2.7"/>
  </g>
  <circle cx="26.2" cy="34.6" r=".8" fill="#fff"/>
  <circle cx="39.4" cy="34.6" r=".8" fill="#fff"/>
  <path d="M28.6 42.6c2.2 2.6 4.6 2.6 6.8 0" fill="none" stroke="#a8543f" stroke-width="1.7" stroke-linecap="round"/>
  <ellipse cx="21.5" cy="41" rx="2.6" ry="1.5" fill="#f2a5a0" opacity=".5"/>
  <ellipse cx="42.5" cy="41" rx="2.6" ry="1.5" fill="#f2a5a0" opacity=".5"/>
</g>`;

  /* ---------- 水印专用：只留标志物，聊天时当底纹不抢字 ---------- */
  const watermark = {
    gintoki: `
<g fill="url(#pvSilver)">
  <circle cx="32" cy="18" r="10"/><circle cx="16" cy="24" r="9"/><circle cx="48" cy="24" r="9"/>
  <circle cx="12" cy="38" r="8"/><circle cx="52" cy="38" r="8"/>
  <circle cx="24" cy="12" r="8"/><circle cx="40" cy="12" r="8"/>
</g>`,
    shinpachi: `
<g fill="none" stroke="#2f3a46" stroke-width="3">
  <rect x="6" y="22" width="23" height="18" rx="6"/>
  <rect x="35" y="22" width="23" height="18" rx="6"/>
  <path d="M29 30h6"/><path d="M6 29l-5-2"/><path d="M58 29l5-2"/>
</g>
<g fill="url(#pvLens)" opacity=".95">
  <rect x="8.6" y="24.6" width="17.8" height="12.8" rx="4.6"/>
  <rect x="37.6" y="24.6" width="17.8" height="12.8" rx="4.6"/>
</g>
<path d="M12 37l13-12.4h4L16 37z" fill="url(#pvGlint)"/>`,
    kagura: `
<g transform="translate(32,30)">
  <path d="M-24 2a24 17 0 0 1 48 0z" fill="url(#pvUmbrella)"/>
  <path d="M-24 2a24 17 0 0 1 48 0" fill="none" stroke="#5d3f80" stroke-width="1.6"/>
  <path d="M0 -13v17M-12 -9.5l-3 13M12 -9.5l3 13M-6 -12l-1.6 15M6 -12l1.6 15"
        stroke="#5d3f80" stroke-width="1.3" opacity=".7" fill="none"/>
  <rect x="-1.8" y="2" width="3.6" height="9" rx="1.4" fill="#7a5330"/>
</g>
<circle cx="14" cy="16" r="9" fill="url(#pvRed)"/><circle cx="50" cy="16" r="9" fill="url(#pvRed)"/>`,
  };

  /* ---------- 背景纹理（data URI，48px 平铺）
     日式/复古方向：木纹（wood）/ 和纸纤维（washi）/ 暖调和纸（washi-warm）。
     颜色随底色明暗取反（深底用淡白、浅底用淡黑），整层再由 CSS 压到 opacity .05 ---------- */
  const tile = (inner) =>
    'url("data:image/svg+xml,' + encodeURIComponent(
      `<svg ${SVG_NS} width="48" height="48" viewBox="0 0 48 48">${inner}</svg>`
    ) + '")';

  // 木纹：横向长纹 + 两处结节，极简不抢眼
  const wood = (c) => tile(`
    <g stroke="${c}" fill="none" stroke-width="1">
      <path d="M0 7h48M0 21h48M0 35h48"/>
      <path d="M0 14h48M0 28h48M0 42h48" stroke-width=".6" opacity=".7"/>
    </g>
    <g fill="${c}" opacity=".8">
      <ellipse cx="13" cy="21" rx="3.4" ry="1.5"/>
      <ellipse cx="35" cy="35" rx="2.6" ry="1.2"/>
    </g>`);

  // 和纸：稀疏短纤维 + 细颗粒
  const washi = (c) => tile(`
    <g stroke="${c}" stroke-width=".9" fill="none" opacity=".85" stroke-linecap="round">
      <path d="M6 9l6 3M22 5l5 4M38 12l6 2M11 27l7 2M29 24l6 3M4 40l7 3M33 41l6 2"/>
      <path d="M17 17l5-3M41 30l5-3M14 45l6-2"/>
    </g>
    <g fill="${c}" opacity=".5">
      <circle cx="9" cy="19" r=".8"/><circle cx="26" cy="14" r=".7"/><circle cx="45" cy="22" r=".8"/>
      <circle cx="20" cy="36" r=".7"/><circle cx="42" cy="44" r=".8"/>
    </g>`);

  const TEXTURES = {
    wood: (scheme) => wood(scheme === "dark" ? "rgba(255,255,255,.9)" : "rgba(60,50,40,.85)"),
    washi: (scheme) => washi(scheme === "dark" ? "rgba(255,255,255,.9)" : "rgba(40,45,50,.7)"),
    "washi-warm": (scheme) => washi(scheme === "dark" ? "rgba(255,255,255,.9)" : "rgba(120,80,50,.7)"),
  };

  /* ---------- 专属图标（单色线性，吃 currentColor，可跟着点缀色走）----------
     银时：卷发剪影 / 洞爷湖木刀    神乐：伞 / 包子    新八：眼镜 / 吐槽汗 ---------- */
  const GLYPHS = {
    // 银时：银色卷毛剪影（一圈交叠的卷）
    hair: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
        <circle cx="8.5" cy="9" r="4"/>
        <circle cx="15.5" cy="7" r="4.2"/>
        <circle cx="22.5" cy="9.5" r="4"/>
        <circle cx="6" cy="16" r="3.4"/>
        <circle cx="25" cy="16" r="3.4"/>
        <path d="M11 22c2 1.6 5.6 1.6 7.6 0"/>
      </g>`,
    // 银时：洞爷湖木刀（带护手的木刀剪影）
    bokutou: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M25.5 6.5L11 21"/>
        <path d="M9.4 19.4l3.2 3.2"/>
        <path d="M7.2 24.2l4.6-2.3 2.3 4.6-2.5 1.2a2.4 2.4 0 0 1-3.2-1.2z" fill="currentColor" fill-opacity=".22"/>
        <path d="M12.4 21.6l-3 3"/>
      </g>`,
    // 神乐：紫伞
    umbrella: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3.6 13.4a12.6 9 0 0 1 24.8 0z" fill="currentColor" fill-opacity=".16"/>
        <path d="M3.6 13.4a12.6 9 0 0 1 24.8 0"/>
        <path d="M16 13.4v9.2a2.6 2.6 0 0 0 5.2 0"/>
        <path d="M10 8.2l-1.6 5.2M22 8.2l1.6 5.2M16 6.6v6.8" stroke-width="1.2"/>
      </g>`,
    // 神乐：包子
    baozi: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round">
        <path d="M16 8c5 0 11 4.6 11 11 0 4.4-4.4 7-11 7s-11-2.6-11-7c0-6.4 6-11 11-11z" fill="currentColor" fill-opacity=".16"/>
        <path d="M16 8c5 0 11 4.6 11 11 0 4.4-4.4 7-11 7s-11-2.6-11-7c0-6.4 6-11 11-11z"/>
        <path d="M11.5 11.6c1.6 2 3 3 4.5 3.4M20.5 11.6c-1.6 2-3 3-4.5 3.4"/>
        <path d="M16 15v4.6"/>
      </g>`,
    // 新八：黑框眼镜（蓝镜片用点缀色淡填充暗示）
    glasses: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round">
        <rect x="3.4" y="11" width="10.6" height="8.4" rx="3" fill="currentColor" fill-opacity=".16"/>
        <rect x="18" y="11" width="10.6" height="8.4" rx="3" fill="currentColor" fill-opacity=".16"/>
        <path d="M14 14.4h4M3.4 13.6L.8 12.6M28.6 13.6l2.6-1"/>
      </g>`,
    // 新八：吐槽汗滴
    sweat: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round">
        <path d="M16 5c3 5 5 7.6 5 9.8A5 5 0 0 1 11 14.8C11 12.6 13 10 16 5z" fill="currentColor" fill-opacity=".18"/>
        <path d="M16 5c3 5 5 7.6 5 9.8A5 5 0 0 1 11 14.8C11 12.6 13 10 16 5z"/>
      </g>`,

    /* ---- 界面线性图标（单色线性风格，全部吃 currentColor）---- */
    navChat: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <path d="M5 8.5A3.5 3.5 0 0 1 8.5 5h15A3.5 3.5 0 0 1 27 8.5v9a3.5 3.5 0 0 1-3.5 3.5H13l-6 5v-5H8.5A3.5 3.5 0 0 1 5 17.5z"/>
        <path d="M11 12.5h10" stroke-opacity=".55"/>
      </g>`,
    navMemory: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 5.5a5 5 0 0 0-4.6 7A4.5 4.5 0 0 0 8 21a4.6 4.6 0 0 0 4 4.6V5.9A5 5 0 0 0 12 5.5z"/>
        <path d="M20 5.5a5 5 0 0 1 4.6 7A4.5 4.5 0 0 1 24 21a4.6 4.6 0 0 1-4 4.6V5.9a5 5 0 0 1 0-.4z"/>
        <path d="M16 5.2v21.2" stroke-opacity=".55"/>
      </g>`,
    navTodo: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <rect x="5" y="5.5" width="22" height="21" rx="4"/>
        <path d="M10.5 16.2l3.6 3.6 7.4-8"/>
      </g>`,
    navGrowth: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <path d="M16 27V14"/>
        <path d="M16 14c0-4.6 3.6-8 9-8 0 4.6-3.4 8-9 8z"/>
        <path d="M16 19c-4.2 0-7.4-3-7.4-7 4.2 0 7.4 3 7.4 7z" stroke-opacity=".75"/>
        <path d="M10 27h12" stroke-opacity=".55"/>
      </g>`,
    navHistory: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22.5 13.5H9.5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h1.2v3l3.6-3h8.2a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2z"/>
        <path d="M12.6 9.6h9.9a2 2 0 0 1 2 2v7.4" stroke-opacity=".5"/>
        <path d="M11.8 17.4h7.4M11.8 20.6h4.8" stroke-opacity=".75"/>
      </g>`,
    navSettings: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="16" cy="16" r="4.2"/>
        <path d="M16 4.6v3M16 24.4v3M27.4 16h-3M7.6 16h-3M24.1 7.9l-2.1 2.1M12 20l-2.1 2.1M24.1 24.1L22 22M12 12L9.9 9.9"/>
      </g>`,
    /* 工作台（M2）：一块带提示符的终端 —— 与其它图标同一笔触（1.7 线性、吃 currentColor） */
    navWorkbench: `
      <g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4.5" y="6" width="23" height="20" rx="4"/>
        <path d="M10 13.2l3.4 3.4L10 20"/>
        <path d="M16.6 20.4h6" stroke-opacity=".7"/>
      </g>`,
    send: `
      <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M5.5 16L26.5 6l-4.2 20-6.3-7.4z"/>
        <path d="M16 18.6L26.5 6" stroke-opacity=".5"/>
      </g>`,
  };

  /** 单个专属图标（底色透明、颜色 = currentColor） */
  function glyph(name, size) {
    const draw = GLYPHS[name];
    if (!draw) return "";
    const s = size || 18;
    return `<svg class="pv-glyph" viewBox="0 0 32 32" width="${s}" height="${s}" ${SVG_NS} aria-hidden="true">${draw}</svg>`;
  }

  /* ---------- 全身 Q 版小人（FloatingMascot 用）----------
     头 = 复用上面的半身像（0..64 放大 1.219 倍），下接和服身体 + 角色道具。
     配色刻意固定（银发就是银、旗袍就是红）——「一眼认人」不能随主题漂移。 */
  const CHIBI_BODY = (fabric, fabric2, sash, collar) => `
    <path d="M48 73c-10 0-18 2.8-20.5 9L19 112.5c-1.3 3.6.7 5.9 4.3 5.9h49.4c3.6 0 5.6-2.3 4.3-5.9L68.5 82C66 75.8 58 73 48 73z"
          fill="${fabric}"/>
    <path d="M48 74l-10 4.5L48 96l10-17.5z" fill="${fabric2}"/>
    <path d="M38 78.5L48 96 58 78.5" fill="none" stroke="${collar}" stroke-width="1.6"/>
    <rect x="30" y="97" width="36" height="7.5" rx="2" fill="${sash}"/>
    <rect x="30" y="97" width="36" height="2.4" rx="1.2" fill="#ffffff" opacity=".28"/>
    <ellipse cx="21" cy="97" rx="8.5" ry="13" fill="${fabric}"/>
    <ellipse cx="75" cy="97" rx="8.5" ry="13" fill="${fabric}"/>
    <circle cx="22" cy="110" r="4.6" fill="url(#pvSkin)"/>
    <circle cx="74" cy="110" r="4.6" fill="url(#pvSkin)"/>
    <ellipse cx="39" cy="122" rx="8" ry="4" fill="#3c4249"/>
    <ellipse cx="57" cy="122" rx="8" ry="4" fill="#3c4249"/>`;

  const CHIBI = {
    // 银时：深灰和服 + 银腰带 + 洞爷湖木刀斜背
    gintoki: () => CHIBI_BODY("#3B4149", "#2C3138", "url(#pvSilver)", "#C0C0C0") + `
      <g transform="translate(64,56) rotate(22) scale(1.8)" color="#8A6A45">
        <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M25.5 6.5L11 21"/><path d="M9.4 19.4l3.2 3.2"/>
          <path d="M7.2 24.2l4.6-2.3 2.3 4.6-2.5 1.2a2.4 2.4 0 0 1-3.2-1.2z"
                fill="currentColor" fill-opacity=".28"/>
        </g>
      </g>`,
    // 神乐：红旗袍 + 金腰带 + 紫伞扛肩（伞先画，压在身后）
    kagura: () => `
      <g transform="translate(62,62) rotate(16) scale(2.05)" color="#8A63B8">
        <g fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3.6 13.4a12.6 9 0 0 1 24.8 0z" fill="currentColor" fill-opacity=".32"/>
          <path d="M3.6 13.4a12.6 9 0 0 1 24.8 0"/>
          <path d="M16 13.4v9.2a2.6 2.6 0 0 0 5.2 0"/>
        </g>
      </g>` + CHIBI_BODY("url(#pvRed)", "#d93f36", "#F4D27A", "#f4d27a"),
    // 新八：浅蓝灰和服 + 浅蓝腰带 + 手里的备忘本
    shinpachi: () => CHIBI_BODY("#8FA9BD", "#7B95AA", "#5C9FD6", "#eef4f8") + `
      <g transform="translate(68,90) rotate(8)">
        <rect x="0" y="0" width="13" height="16" rx="1.6" fill="#FBFBFC" stroke="#8FA9BD" stroke-width="1.1"/>
        <path d="M3 4h7M3 7.5h7M3 11h4.5" stroke="#8FA9BD" stroke-width="1" stroke-linecap="round"/>
      </g>`,
  };

  /** 全身 Q 版小人（SVG 字符串）。size 为宽度像素，高度按 96:132 比例 */
  function chibi(pid, size) {
    const draw = CHIBI[pid];
    const w = size || 84;
    const h = Math.round(w * 132 / 96);
    if (!draw) {
      return `<span class="mark-fallback" style="font-size:${Math.round(w * 0.5)}px">${FALLBACK_EMOJI[pid] || "🥤"}</span>`;
    }
    return `<svg class="pv-chibi" viewBox="0 0 96 132" width="${w}" height="${h}" ${SVG_NS} aria-hidden="true">`
      + `<g transform="translate(9,0) scale(1.219)">${(BUST[pid] || BUST.gintoki)({ milk: false })}</g>`
      + draw() + `</svg>`;
  }

  /* ---------- 对外接口 ---------- */
  const BUST = { gintoki, kagura, shinpachi };
  const FALLBACK_EMOJI = { gintoki: "🥤", kagura: "🌂", shinpachi: "👓", tama: "🤖" };

  /** 半身像（头像 / 吉祥物 / 空对话大字），size 为像素边长 */
  function bust(pid, size, opts) {
    const draw = BUST[pid];
    const s = size || 40;
    if (!draw) {
      // 未注册 SVG 的新人格：退化成 emoji，保证「只加数据也能跑」
      return `<span class="mark-fallback" style="font-size:${Math.round(s * 0.62)}px">${FALLBACK_EMOJI[pid] || "🥤"}</span>`;
    }
    return `<svg class="pv-mark" viewBox="0 0 64 64" width="${s}" height="${s}" ${SVG_NS} aria-hidden="true">${draw(opts || {})}</svg>`;
  }

  /** 水印版（只有标志物）。未注册 SVG 的人格退化成 emoji，避免聊天水印空着 */
  function mark(pid, size) {
    const draw = watermark[pid];
    const s = size || 120;
    if (!draw) {
      const emo = FALLBACK_EMOJI[pid];
      return emo ? `<span class="mark-fallback" style="font-size:${Math.round(s * 0.62)}px">${emo}</span>` : "";
    }
    return `<svg class="pv-mark" viewBox="0 0 64 64" width="${s}" height="${s}" ${SVG_NS} aria-hidden="true">${draw}</svg>`;
  }

  /** 背景纹理（CSS background-image 值）。name 取自主题的 texture 字段，scheme 决定取反色 */
  function texture(name, scheme) {
    const f = TEXTURES[name];
    return f ? f(scheme || "light") : "none";
  }

  /** 标志物的 data URI 版（给 CSS 伪元素 / background-image 当水印用）
      op 直接烘进 SVG，这样 CSS 侧无需（也无法）单独控制图层透明度 */
  function markURI(pid, op) {
    const draw = watermark[pid];
    if (!draw) return "none";
    const o = (op == null ? 1 : op);
    return 'url("data:image/svg+xml,' + encodeURIComponent(
      `<svg ${SVG_NS} width="64" height="64" viewBox="0 0 64 64"><g opacity="${o}">${draw}</g></svg>`
    ) + '")';
  }

  /** 把渐变 defs 注入文档（只做一次） */
  function installDefs() {
    if (document.getElementById("pvDefs")) return;
    const box = document.createElement("div");
    box.innerHTML = DEFS;
    document.body.appendChild(box.firstElementChild);
  }

  return { bust, chibi, mark, markURI, texture, glyph, installDefs, has: (pid) => !!BUST[pid] };
})();
