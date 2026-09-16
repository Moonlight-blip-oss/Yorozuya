/* ============================================================
   FloatingMascot · 右下角 Q 版悬浮小人
   ------------------------------------------------------------
   props = { personaId, image, size, anim, lines, onClick }
     · 固定 right:24px bottom:80px（不遮输入框与发送键）
     · 3s 上下浮动；切换人格时形象 0.3s 淡入淡出（双层交叉淡化，不硬跳）
     · 点击：缩放反馈 + 随机台词气泡；具体行为交给 onSwitch 之外传入的 onClick
   ⚠️ 组件只消费 personaThemes 给的配置，**不硬编码图片路径与台词**。
   ============================================================ */
"use strict";

const FloatingMascot = (() => {
  let root = null;
  let props = {};
  let built = false;
  let curLayer = 0;            // 0 / 1：当前可见的图层
  let bubbleTimer = null;
  let pressTimer = null;

  function mount(el, initial) {
    if (!el) return;
    root = el;
    build();
    update(initial);
  }

  /* 结构只建一次：两层 img 用于交叉淡化 + 光晕 + 台词气泡 */
  function build() {
    if (built) return;
    root.className = "floating-mascot";
    root.innerHTML = `
      <div class="fm-bubble" id="fmBubble" role="status" aria-live="polite"></div>
      <button class="fm-btn" id="fmBtn" type="button" aria-label="${T("和角色互动")}">
        <span class="fm-glow" aria-hidden="true"></span>
        <span class="fm-stage">
          <img class="fm-img" alt="">
          <img class="fm-img fm-img--back" alt="">
        </span>
      </button>`;
    root.querySelector("#fmBtn").addEventListener("click", onClick);
    built = true;
  }

  /** 更新 props：形象变化时走 0.3s 交叉淡化，尺寸/动画参数走 CSS 变量 */
  function update(next) {
    if (!root || !built) return;
    const prev = props;
    props = Object.assign({}, props, next || {});
    const r = root.style;
    const size = props.size || 84;
    r.setProperty("--fm-size", size + "px");
    r.setProperty("--fm-aspect", props.aspect || "2 / 3");
    r.setProperty("--fm-float-duration", (props.anim && props.anim.floatDuration) || "3s");
    r.setProperty("--fm-float-y", (props.anim && props.anim.floatDistance) || "-6px");
    r.setProperty("--fm-fade-duration", (props.anim && props.anim.fadeDuration) || "0.3s");

    if (props.image && props.image !== prev.image) {
      showImage(props.image);
    }
  }

  /* 双层交叉淡化：把新图放到背面层，等它淡入后再交换「当前层」 */
  function showImage(src) {
    const imgs = root.querySelectorAll(".fm-img");
    const nextLayer = 1 - curLayer;
    const incoming = imgs[nextLayer];
    const outgoing = imgs[curLayer];
    const first = !outgoing.src || !outgoing.classList.contains("on");

    incoming.onerror = () => {
      // 立绘缺失/路径写错时，降级成内联 SVG 小人 —— 绝不留在「破图」状态
      incoming.onerror = null;
      const svg = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
        PERSONA_MARKS.chibi(props.personaId, 200));
      incoming.src = svg;
    };
    incoming.src = src;
    incoming.classList.add("on");
    if (!first) outgoing.classList.remove("on");
    else imgs[0].classList.remove("on");
    curLayer = nextLayer;
  }

  /* 点击：先给瞬时缩放反馈，再交给外部行为 + 随机台词 */
  function onClick(e) {
    const btn = root.querySelector("#fmBtn");
    btn.classList.add("press", "hi");
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {
      btn.classList.remove("press");
      setTimeout(() => btn.classList.remove("hi"), 260);
    }, 130);
    if (typeof props.onClick === "function") props.onClick(e);
  }

  /** 弹一句随机台词（台词来自配置） */
  function say(text, ms) {
    const b = root && root.querySelector("#fmBubble");
    if (!b || !text) return;
    b.textContent = text;
    b.classList.add("on");
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => b.classList.remove("on"), ms || 3000);
  }

  function randomLine() {
    const lines = (props.lines && props.lines.length) ? props.lines : [];
    if (!lines.length) return "";
    return lines[Math.floor(Math.random() * lines.length)];
  }

  return { mount, update, say, randomLine };
})();
