# -*- coding: utf-8 -*-
"""Yorozuya 应用图标生成器（可复现）

两套设计，用 --style 切换（默认 badge）：

  badge  · 圆形徽章（现用）
    深墨蓝→夜黑渐变底，边缘一圈微发光的银白细描边（像月下刀刃反光）。
    正中一把斜放的木刀（浅木色带纹理、刀柄缠黑绳），刀下压着一张微翘起的
    委托单（泛黄纸、卷角、三行白色待办横线）。木刀右上悬一枚白色对话气泡，
    气泡里是齿轮与闪电交叠的符号（思考 + 执行）。底部一条弧形银缎带托住，
    缎带上沿弧写着 YOROZUYA。构图是"木刀压单、气泡悬上"的三角稳定结构。
    配色只有墨蓝 / 木色 / 银白三色。

  lantern · 提灯（上一版，保留可选）
    夜里亮着的纸灯笼 + 墨色「屋」+ 挂绳穗子。

三档细节，保证 16px 到 256px 都清晰：
  full（≥64px）全套；simple（24-48px）精简装饰、放大主体；
  micro（≤16px）只留剪影与最关键符号。

用法：python tools/make_icon.py [--style badge|lantern]
产物：icon/Yorozuya_1024.png、icon/Yorozuya_{512,128}.png、icon/Yorozuya.ico、
      renderer/favicon.ico、renderer/logo.png
"""
import argparse
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from render_sign import render_sign, vgrad
from render_badge_dog import render_sadaharu
from render_photo import render_photo

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "icon"
ICON_ICO = OUT_DIR / "Yorozuya.ico"
FAVICON = ROOT / "renderer" / "favicon.ico"
LOGO_PNG = ROOT / "renderer" / "logo.png"

# ============ 配色（徽章版：只有墨蓝 / 木色 / 银白三色）============
INKBLUE_HI = (26, 42, 74)       # #1a2a4a 墨蓝
INKBLUE_LO = (10, 14, 22)       # #0a0e16 夜黑
SILVER = (226, 235, 245)        # 银白
WOOD_HI = (226, 193, 138)       # 浅木色
WOOD_LO = (188, 148, 92)
WOOD_LINE = (150, 112, 64)      # 木纹
CORD_DARK = (22, 24, 32)        # 缠柄黑绳
PAPER_HI = (235, 219, 168)      # 泛黄委托单
PAPER_LO = (208, 187, 132)
PAPER_EDGE = (172, 150, 100)
BUBBLE_BG = (246, 250, 255)

# ============ 配色（提灯版）============
BG_HI = (70, 50, 42)
BG_LO = (32, 22, 18)
PAPERL_HI = (248, 228, 186)
PAPERL_LO = (226, 183, 124)
RIBLINE = (196, 122, 78)
CAP = (168, 74, 52)
INK = (42, 29, 20)
CORD = (198, 156, 104)
TASSEL = (176, 82, 58)
GLOW = (255, 176, 96)
LABEL = (232, 217, 190)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\STZHONGS.TTF",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
]
LATIN_CANDIDATES = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf"]

# —— 徽章版构图参数（集中放在这里，方便微调）——
BADGE_R = 0.478          # 徽章圆半径
SWORD_ANGLE = -27        # 木刀倾角
SLIP_ANGLE = -7          # 委托单倾角
RIBBON = dict(r=0.452, cx=0.5, cy=0.398, th=0.056, a0=62, a1=118)  # 弧缎带


def pick_font(cands, size):
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def ss_for(size):
    """超采样倍数：大图省内存，小图多采样。"""
    return 2 if size >= 512 else 4


def draw_tracked(draw, center, text, font, fill, tracking):
    ws = [draw.textlength(ch, font=font) for ch in text]
    total = sum(ws) + tracking * max(0, len(text) - 1)
    x = center[0] - total / 2
    for ch, w in zip(text, ws):
        draw.text((x, center[1]), ch, font=font, fill=fill, anchor="lm")
        x += w + tracking


def _circle_mask(S, cx, cy, r):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return m


# ---------------- 徽章版：零件 ----------------

def _sword_layer(L, grain=True):
    """水平绘制木刀（刀柄在左、刀尖在右）；调用方旋转后再合成。"""
    lay = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cy = L * 0.5
    x0 = L * 0.06
    x1 = L * 0.98
    blade_h = L * 0.080
    handle_len = L * 0.155
    hx = x0 + handle_len

    # 刀身：细长，刀尖平头（木刀无刃，刀尖是切平的）
    d.polygon([(hx, cy - blade_h * 0.52), (x1 - L * 0.055, cy - blade_h * 0.40),
               (x1, cy - blade_h * 0.22), (x1, cy + blade_h * 0.22),
               (x1 - L * 0.055, cy + blade_h * 0.40), (hx, cy + blade_h * 0.52)],
              fill=WOOD_HI + (255,))
    d.line([(hx + L * 0.01, cy - blade_h * 0.42), (x1 - L * 0.10, cy - blade_h * 0.26)],
           fill=(250, 240, 214, 150), width=max(1, int(L * 0.006)))
    # 下侧暗面 → 圆柱感
    d.polygon([(hx, cy + blade_h * 0.06), (x1 - L * 0.070, cy + blade_h * 0.12),
               (x1 - L * 0.070, cy + blade_h * 0.40), (hx, cy + blade_h * 0.50)],
              fill=WOOD_LO + (255,))
    if grain:
        for k in (-0.24, 0.04, 0.28):
            gy = cy + blade_h * k
            d.line([(hx + L * 0.014, gy), (x1 - L * 0.080, gy)],
                   fill=WOOD_LINE + (90,), width=max(1, int(L * 0.0042)))

    # 刀柄：缠四道黑绳
    hy = blade_h * 0.74
    d.rounded_rectangle([x0, cy - hy, hx, cy + hy], radius=hy * 0.5, fill=WOOD_LO + (255,))
    wrap_w = max(2, int(L * 0.0115))
    for i in range(4):
        wx = x0 + hy * 0.80 + (handle_len - hy * 1.60) * i / 3
        d.line([(wx, cy - hy * 0.96), (wx + hy * 0.70, cy + hy * 0.96)],
               fill=CORD_DARK + (240,), width=wrap_w)
    # 柄头
    d.rounded_rectangle([x0 - L * 0.014, cy - hy * 0.72, x0 + L * 0.006, cy + hy * 0.72],
                        radius=hy * 0.32, fill=WOOD_LINE + (255,))
    # 刀镡：柄与身之间的过渡环，一眼看出是"刀"而不是木棍
    d.rounded_rectangle([hx - L * 0.016, cy - hy * 1.14, hx + L * 0.014, cy + hy * 1.14],
                        radius=hy * 0.30, fill=(96, 68, 40, 255))
    return lay


def _slip_layer(L, lines=True):
    """水平绘制委托单（泛黄纸 + 右下卷角 + 三行待办）。"""
    lay = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    w, h = L * 0.84, L * 0.62
    x0, y0 = (L - w) / 2, (L - h) / 2
    x1, y1 = x0 + w, y0 + h

    paper = vgrad(L, PAPER_HI, PAPER_LO).convert("RGBA")
    mask = Image.new("L", (L, L), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([x0, y0, x1, y1], radius=L * 0.030, fill=255)
    md.polygon([(x1 - L * 0.175, y1), (x1, y1 - L * 0.175), (x1, y1)], fill=0)   # 切掉右下角
    paper.putalpha(mask)
    lay = Image.alpha_composite(lay, paper)

    d = ImageDraw.Draw(lay, "RGBA")
    # 卷角本体 + 亮边
    d.polygon([(x1 - L * 0.175, y1), (x1, y1 - L * 0.175), (x1 - L * 0.012, y1 - L * 0.012)],
              fill=PAPER_EDGE + (240,))
    d.line([(x1 - L * 0.175, y1), (x1 - L * 0.012, y1 - L * 0.012)],
           fill=(252, 243, 210, 200), width=max(1, int(L * 0.007)))
    d.rounded_rectangle([x0, y0, x1, y1], radius=L * 0.030, outline=PAPER_EDGE + (140,),
                        width=max(1, int(L * 0.007)))
    if lines:
        for i, frac in enumerate((0.28, 0.54, 0.80)):
            ly = y0 + h * frac
            bw = L * 0.038
            bx = x0 + w * 0.09
            d.rounded_rectangle([bx, ly - bw * 0.5, bx + bw, ly + bw * 0.5],
                                radius=bw * 0.24, outline=(255, 255, 255, 195),
                                width=max(1, int(L * 0.0065)))
            ln = w * (0.60 if i == 0 else 0.70 if i == 1 else 0.42)
            d.line([(bx + bw * 1.7, ly), (bx + bw * 1.7 + ln, ly)],
                   fill=(255, 255, 255, 210), width=max(1, int(L * 0.0095)))
    return lay


def _gear(d, cx, cy, r, teeth, color, hole_color):
    for i in range(teeth):
        a = 2 * math.pi * i / teeth
        p = []
        for da, rr in ((-0.34, r * 1.02), (-0.12, r * 1.38), (0.12, r * 1.38), (0.34, r * 1.02)):
            ang = a + da
            p.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
        d.polygon(p, fill=color)
    d.ellipse([cx - r * 1.04, cy - r * 1.04, cx + r * 1.04, cy + r * 1.04], fill=color)
    # 中孔：必须用气泡底色填，RGBA 里填全透明等于"什么都不画"
    d.ellipse([cx - r * 0.40, cy - r * 0.40, cx + r * 0.40, cy + r * 0.40], fill=hole_color)


def _bubble_layer(L, symbol=True):
    """白色对话气泡，内含齿轮 + 闪电（思考 ⚡ 执行）。"""
    lay = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cx, cy = L * 0.5, L * 0.43
    rx, ry = L * 0.42, L * 0.32
    # 尾巴朝左下，指向木刀
    d.polygon([(cx - rx * 0.52, cy + ry * 0.74), (cx - rx * 0.02, cy + ry * 0.60),
               (cx - rx * 0.40, cy + ry * 1.50)], fill=BUBBLE_BG + (255,))
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=BUBBLE_BG + (255,))
    if symbol:
        gx, gy, gr = cx - rx * 0.24, cy + ry * 0.05, rx * 0.30
        _gear(d, gx, gy, gr, 8, INKBLUE_HI + (255,), BUBBLE_BG + (255,))
        # 闪电：白身 + 墨蓝厚描边，压在齿轮右下
        bx, by = cx + rx * 0.14, cy - ry * 0.46
        hgt = ry * 1.10
        bolt = [(bx + rx * 0.22, by - hgt * 0.02), (bx - rx * 0.17, by + hgt * 0.53),
                (bx + rx * 0.03, by + hgt * 0.51), (bx - rx * 0.21, by + hgt * 1.04),
                (bx + rx * 0.28, by + hgt * 0.43), (bx + rx * 0.06, by + hgt * 0.45)]
        off = max(2, int(L * 0.012))
        for dx, dy in ((-off, 0), (off, 0), (0, -off), (0, off),
                       (-off, -off), (off, off), (-off, off), (off, -off)):
            d.polygon([(x + dx, y + dy) for x, y in bolt], fill=INKBLUE_HI + (255,))
        d.polygon(bolt, fill=(252, 253, 255, 255))
    return lay


def _paste_rotated(base, layer, angle, center):
    rot = layer.rotate(angle, resample=Image.BICUBIC, center=(layer.width / 2, layer.height / 2)) \
        if angle else layer
    base.alpha_composite(rot, (int(center[0] - rot.width / 2), int(center[1] - rot.height / 2)))


def _shadow(base, layer, angle, center, blur, alpha, offset=(0, 0)):
    sh = layer.rotate(angle, resample=Image.BICUBIC, center=(layer.width / 2, layer.height / 2)) \
        if angle else layer
    a = sh.split()[3].point(lambda v: min(255, int(v * alpha)))
    dark = Image.new("RGBA", sh.size, (0, 0, 0, 255))
    dark.putalpha(a)
    dark = dark.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(dark, (int(center[0] - dark.width / 2 + offset[0]),
                                int(center[1] - dark.height / 2 + offset[1])))


def _draw_ribbon(img, S, detail):
    """弧形银缎带：兜在主体下方，两端微微上扬；缎带上沿弧写字。"""
    r = RIBBON["r"] * S
    ac = (RIBBON["cx"] * S, RIBBON["cy"] * S)
    th = RIBBON["th"] * S
    a0, a1 = math.radians(RIBBON["a0"] - 7), math.radians(RIBBON["a1"] + 7)
    steps = 80
    outer, inner = [], []
    for i in range(steps + 1):
        u = i / steps
        a = a0 + (a1 - a0) * u
        taper = 1.0 - 0.55 * (abs(u - 0.5) * 2) ** 2      # 中间厚、两端薄
        half = th / 2 * taper
        outer.append((ac[0] + math.cos(a) * (r + half), ac[1] + math.sin(a) * (r + half)))
        inner.append((ac[0] + math.cos(a) * (r - half), ac[1] + math.sin(a) * (r - half)))

    band = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band, "RGBA")
    bd.polygon(outer + inner[::-1], fill=SILVER + (255,))
    if detail == "full":
        f = pick_font(LATIN_CANDIDATES, int(S * 0.040))
        # 角度大的在左，保证从左到右读作 YOROZUYA
        ta_l, ta_r = math.radians(RIBBON["a1"] - 2), math.radians(RIBBON["a0"] + 2)
        for i, ch in enumerate("YOROZUYA"):
            t = (i + 0.5) / 8
            a = ta_l + (ta_r - ta_l) * t
            px = ac[0] + math.cos(a) * r
            py = ac[1] + math.sin(a) * r
            cw = max(6, int(f.getlength(ch) + S * 0.02))
            ch_img = Image.new("RGBA", (cw, int(S * 0.085)), (0, 0, 0, 0))
            ImageDraw.Draw(ch_img).text((cw / 2, ch_img.height / 2), ch, font=f,
                                        fill=INKBLUE_LO + (255,), anchor="mm")
            ch_img = ch_img.rotate(-math.degrees(a) + 90, resample=Image.BICUBIC, expand=True)
            band.alpha_composite(ch_img, (int(px - ch_img.width / 2), int(py - ch_img.height / 2)))
    img.alpha_composite(band)


def render_badge(size, detail="full"):
    S = size * ss_for(size)
    C = S * 0.5
    R = S * BADGE_R

    # 底：墨蓝 → 夜黑，圆形
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base = vgrad(S, INKBLUE_HI, INKBLUE_LO).convert("RGBA")
    img.alpha_composite(Image.composite(base, Image.new("RGBA", (S, S), (0, 0, 0, 0)),
                                        _circle_mask(S, C, C, R)))

    # 委托单（最下层，被木刀压住）
    slip = _slip_layer(int(S * 0.60), lines=(detail != "micro"))
    slip_c = (C, S * (0.630 if detail == "full" else 0.625))
    _shadow(img, slip, SLIP_ANGLE, (slip_c[0] + S * 0.004, slip_c[1] + S * 0.013), S * 0.012, 0.5)
    _paste_rotated(img, slip, SLIP_ANGLE, slip_c)

    # 木刀（压在委托单上）
    sword = _sword_layer(int(S * 0.80), grain=(detail == "full"))
    sw_c = (C, S * (0.500 if detail == "full" else 0.49))
    _shadow(img, sword, SWORD_ANGLE, (sw_c[0] + S * 0.006, sw_c[1] + S * 0.016), S * 0.016, 0.55)
    _paste_rotated(img, sword, SWORD_ANGLE, sw_c)

    # 对话气泡（悬于右上）
    bub = _bubble_layer(int(S * 0.315), symbol=(detail != "micro"))
    bb_c = (S * (0.745 if detail == "full" else 0.73), S * (0.285 if detail == "full" else 0.280))
    _shadow(img, bub, 0, (bb_c[0] + S * 0.004, bb_c[1] + S * 0.011), S * 0.013, 0.55)
    _paste_rotated(img, bub, 0, bb_c)

    # 缎带兜底
    if detail != "micro":
        _draw_ribbon(img, S, detail)

    # 银白描边 + 微光（像月下刀刃反光）
    ring_w = max(2, int(S * 0.013))
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([C - R + ring_w / 2, C - R + ring_w / 2,
                                  C + R - ring_w / 2, C + R - ring_w / 2],
                                 outline=SILVER + (255,), width=ring_w)
    glow = ring.filter(ImageFilter.GaussianBlur(S * 0.016))
    glow.putalpha(glow.split()[3].point(lambda v: int(v * 0.9)))
    halo = glow.filter(ImageFilter.GaussianBlur(S * 0.030))
    halo.putalpha(halo.split()[3].point(lambda v: int(v * 0.5)))
    img.alpha_composite(halo)
    img.alpha_composite(glow)
    img.alpha_composite(ring)

    return img.resize((size, size), Image.LANCZOS)


# ---------------- 提灯版（保留可选）----------------

def render_lantern(size, detail="full"):
    S = size * ss_for(size)
    img = vgrad(S, BG_HI, BG_LO).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")

    cx = S * 0.5
    body_w = S * {"full": 0.44, "simple": 0.50, "micro": 0.60}[detail]
    body_h = S * {"full": 0.50, "simple": 0.56, "micro": 0.66}[detail]
    cy = S * {"full": 0.555, "simple": 0.545, "micro": 0.52}[detail]
    x0, x1 = cx - body_w / 2, cx + body_w / 2
    y0, y1 = cy - body_h / 2, cy + body_h / 2
    cap_h = S * {"full": 0.052, "simple": 0.062, "micro": 0.075}[detail]
    cap_w = body_w * {"full": 0.42, "simple": 0.46, "micro": 0.54}[detail]

    n = 96
    gl = Image.new("L", (n, n), 0)
    px = gl.load()
    for yy in range(n):
        for xx in range(n):
            dx = (xx / n - 0.5) / 0.46
            dy = (yy / n - 0.5) / 0.46
            dd = (dx * dx + dy * dy) ** 0.5
            if dd < 1:
                px[xx, yy] = int(130 * (1 - dd) ** 1.8)
    glow = Image.new("RGBA", (S, S), GLOW + (255,))
    glow.putalpha(gl.resize((S, S), Image.LANCZOS))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img, "RGBA")

    if detail == "full":
        ring_y = y0 - S * 0.075
        d.ellipse([cx - S * 0.026, ring_y - S * 0.026, cx + S * 0.026, ring_y + S * 0.026],
                  outline=CORD + (255,), width=max(2, int(S * 0.011)))
        d.line([cx, ring_y + S * 0.024, cx, y0 - cap_h * 0.35],
               fill=CORD + (255,), width=max(2, int(S * 0.011)))

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([x0, y0, x1, y1], fill=255)
    paper = vgrad(S, PAPERL_HI, PAPERL_LO).convert("RGBA")
    paper.putalpha(mask)
    img.alpha_composite(paper)
    d = ImageDraw.Draw(img, "RGBA")

    for fr in {"full": (0.17, 0.83), "simple": (0.20, 0.80), "micro": ()}[detail]:
        fy = y0 + fr * body_h
        half = (body_w / 2) * (1 - ((fy - cy) / (body_h / 2)) ** 2) ** 0.5
        d.line([cx - half * 0.93, fy, cx + half * 0.93, fy], fill=RIBLINE + (200,),
               width=max(1, int(S * {"full": 0.011, "simple": 0.016}[detail])))

    for cy_cap in (y0 + cap_h * 0.34, y1 - cap_h * 0.34):
        d.rounded_rectangle([cx - cap_w / 2, cy_cap - cap_h / 2, cx + cap_w / 2, cy_cap + cap_h / 2],
                            radius=cap_h / 2, fill=CAP + (255,))

    gh = S * {"full": 0.215, "simple": 0.295, "micro": 0.360}[detail]
    d.text((cx, cy + body_h * {"full": 0.015, "simple": 0.01, "micro": 0.0}[detail]), "屋",
           font=pick_font(FONT_CANDIDATES, int(gh)), fill=INK + (255,), anchor="mm")

    if detail == "full":
        ty = y1 + S * 0.030
        d.line([cx, y1, cx, ty - S * 0.010], fill=CORD + (255,), width=max(2, int(S * 0.009)))
        d.polygon([(cx - S * 0.026, ty), (cx + S * 0.026, ty),
                   (cx + S * 0.014, ty + S * 0.038), (cx - S * 0.014, ty + S * 0.038)],
                  fill=TASSEL + (255,))
        draw_tracked(d, (cx, S * 0.932), "YOROZUYA", pick_font(LATIN_CANDIDATES, int(S * 0.048)),
                     LABEL + (225,), S * 0.012)

    mask2 = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask2).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.225), fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask2)
    return out.resize((size, size), Image.LANCZOS)


# ==================================================================

RENDERERS = {"photo": render_photo, "sadaharu": render_sadaharu,
                 "sign": render_sign, "badge": render_badge, "lantern": render_lantern}


def pick_detail(s):
    if s >= 64:
        return "full"
    if s >= 24:
        return "simple"
    return "micro"


def render(size, detail="full", style="badge"):
    return RENDERERS[style](size, detail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="photo", choices=list(RENDERERS))
    args = ap.parse_args()
    style = args.style
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    render(1024, "full", style).save(OUT_DIR / "Yorozuya_1024.png")
    render(512, "full", style).save(OUT_DIR / "Yorozuya_512.png")
    render(128, "full", style).save(OUT_DIR / "Yorozuya_128.png")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [render(s, pick_detail(s), style) for s in sizes]
    base = frames[-1]
    try:
        base.save(ICON_ICO, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    except TypeError:
        base.save(ICON_ICO, format="ICO", sizes=[(s, s) for s in sizes])

    render(64, "full", style).save(FAVICON, format="ICO", sizes=[(64, 64), (32, 32), (16, 16)])
    render(256, "full", style).save(LOGO_PNG)

    print("风格:", style)
    for p in (OUT_DIR / "Yorozuya_1024.png", OUT_DIR / "Yorozuya_512.png",
              OUT_DIR / "Yorozuya_128.png", ICON_ICO, FAVICON, LOGO_PNG):
        print("生成:", p.relative_to(ROOT), f"({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
