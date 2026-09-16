# -*- coding: utf-8 -*-
"""Yorozuya 图标 v4 · 万事屋木牌 + 定春（按参考图校正过形象）

参考图关键特征（已逐条落实到绘制里）：
  · 蓬松白毛：轮廓是**锯齿状**的绒毛，不是光滑椭圆
  · 耳朵大、圆钝、外倾，耳内是**浅粉色**
  · 眼上是两道**棕色眉纹**（定春的招牌，比红色符文更还原）
  · 大眼睛：黑色椭圆 + 白色高光点（略压上眼睑 → 慵懒但不失可爱）
  · 棕色圆鼻 + 微张的嘴，露出一小截粉舌
  · 舌上搁一枚小铜铃（沿用上一版的要求，没在响）
  · 下巴处露出一点**红色项圈**（参考图里定春戴的）

分层：
  背景  深夜墨蓝圆角方形 + 右下角很淡的暖橘光晕（登势酒馆的灯笼从下面照上来）
  下层  横向旧木牌：粗木纹、磨损边角、四角发黑铁钉与锈迹、毛笔字「万事屋」
  上层  定春趴在木牌上沿（只画头与前爪），把牌子压成左低右高
"""
import math

from PIL import Image, ImageDraw, ImageFilter

from render_badge_dog import load_font

# ---------- 配色 ----------
BG_HI = (25, 40, 70)          # 深夜墨蓝
BG_LO = (9, 15, 27)           # 夜黑
GLOW = (242, 160, 86)         # 暖橘（灯笼光）
WOOD_HI = (216, 188, 142)
WOOD_LO = (172, 136, 88)
WOOD_GRAIN = (136, 104, 60)
WOOD_WORN = (126, 96, 58)
NAIL = (56, 52, 46)
NAIL_HI = (108, 104, 96)
RUST = (150, 80, 36)
INK = (28, 23, 18)
FUR_HI = (253, 253, 252)
FUR_LO = (223, 227, 233)
FUR_SHADE = (196, 201, 210)
EAR_PINK = (244, 190, 188)
EAR_PINK_D = (226, 150, 150)
BROW = (178, 134, 96)         # 棕色眉纹
BROW_D = (150, 108, 74)
NOSE = (110, 74, 58)          # 棕色鼻子
MOUTH_IN = (94, 46, 44)       # 口腔
PINK = (236, 150, 158)
PINK_HI = (248, 190, 194)
BELL = (198, 140, 74)
BELL_HI = (236, 196, 132)
COLLAR = (186, 56, 48)        # 红项圈
COLLAR_D = (140, 36, 32)

BRUSH_FONTS = [r"C:\Windows\Fonts\STXINGKA.TTF",
               r"C:\Windows\Fonts\STKAITI.TTF",
               r"C:\Windows\Fonts\STZHONGS.TTF"]

SS = 4


def vgrad(size, c1, c2):
    n = 128
    small = Image.new("RGB", (n, n))
    px = small.load()
    for y in range(n):
        t = y / (n - 1)
        row = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        for x in range(n):
            px[x, y] = row
    return small.resize((size, size), Image.LANCZOS)


def _radial(size, cx, cy, rx, ry, color, alpha):
    n = 128
    small = Image.new("L", (n, n), 0)
    px = small.load()
    for y in range(n):
        for x in range(n):
            dx = (x / n - cx) / rx
            dy = (y / n - cy) / ry
            d = (dx * dx + dy * dy) ** 0.5
            if d < 1:
                px[x, y] = int(alpha * (1 - d) ** 1.7)
    layer = Image.new("RGBA", (size, size), color + (255,))
    layer.putalpha(small.resize((size, size), Image.LANCZOS))
    return layer


def _soft_shadow(img, box, blur, alpha, fill=(0, 0, 0)):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).ellipse(box, fill=fill + (alpha,))
    img.alpha_composite(lay.filter(ImageFilter.GaussianBlur(blur)))


def _fluffy(d, cx, cy, rx, ry, tufts=18, amp=0.075, fill=None, rot=0.0,
            inner_fill=None, inner_scale=0.90):
    """锯齿边的绒毛椭圆——定春的毛就是这个轮廓，不能画成光滑椭圆。"""
    pts_outer, pts_inner = [], []
    total = tufts * 2
    for i in range(total):
        a = 2 * math.pi * i / total + rot
        k_out = 1.0 + amp * (1.0 if i % 2 == 0 else -0.30)
        k_in = k_out * inner_scale
        pts_outer.append((cx + math.cos(a) * rx * k_out, cy + math.sin(a) * ry * k_out))
        pts_inner.append((cx + math.cos(a) * rx * k_in, cy + math.sin(a) * ry * k_in))
    if fill is not None:
        d.polygon(pts_outer, fill=fill)
    if inner_fill is not None:
        d.polygon(pts_inner, fill=inner_fill)


# ---------------- 木牌 ----------------

def _board_layer(S, detail, tilt=4.0):
    """独立的 S×S 图层：横向旧木牌 + 铁钉锈迹 + 毛笔字，最后整体微旋转。"""
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cx, cy = S * 0.5, S * 0.700
    w, h = S * 0.88, S * 0.300
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    r = S * 0.030

    plate = vgrad(S, WOOD_HI, WOOD_LO).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], radius=r, fill=255)
    plate.putalpha(mask)
    lay.alpha_composite(plate)
    d = ImageDraw.Draw(lay, "RGBA")

    if detail == "full":
        for i in range(7):
            fy = y0 + h * (0.10 + 0.132 * i)
            amp = h * 0.030 * (1 if i % 2 else -1)
            pts = []
            for k in range(41):
                u = k / 40
                pts.append((x0 + w * u, fy + math.sin(u * math.pi * 2.2 + i) * amp))
            d.line(pts, fill=WOOD_GRAIN + (86,), width=max(1, int(S * 0.0042)))
        for kx, ky, kr in ((0.20, 0.62, 0.014), (0.78, 0.78, 0.011)):
            d.ellipse([S * kx - S * kr, S * ky - S * kr * 0.7,
                       S * kx + S * kr, S * ky + S * kr * 0.7],
                      outline=WOOD_WORN + (120,), width=max(1, int(S * 0.0035)))

    if detail != "micro":
        chips = [((x0, y0), 0.055, 0.035), ((x1, y1), -0.05, -0.03),
                 ((x0 + w * 0.62, y0), 0.045, 0.028), ((x1 - w * 0.30, y1), -0.04, -0.026)]
        for (px, py), dw, dh in chips:
            d.polygon([(px, py), (px + dw * S, py), (px, py + dh * S)], fill=WOOD_WORN + (60,))
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=(96, 70, 40, 150),
                            width=max(1, int(S * 0.007)))

    nail_inset_x, nail_inset_y = w * 0.075, h * 0.30
    for sx in (x0 + nail_inset_x, x1 - nail_inset_x):
        for sy in (y0 + nail_inset_y, y1 - nail_inset_y):
            nr = S * 0.0145
            if detail == "full":
                for k in range(9):
                    ang = (sx * 7 + sy * 13 + k * 2.1) % 6.28
                    rr = nr * (1.5 + (k % 3) * 0.7)
                    d.ellipse([sx + math.cos(ang) * rr - nr * 0.3,
                               sy + math.sin(ang) * rr - nr * 0.3,
                               sx + math.cos(ang) * rr + nr * 0.3,
                               sy + math.sin(ang) * rr + nr * 0.3], fill=RUST + (150,))
            d.ellipse([sx - nr, sy - nr, sx + nr, sy + nr], fill=NAIL + (255,))
            d.ellipse([sx - nr * 0.45, sy - nr * 0.45, sx, sy], fill=NAIL_HI + (170,))

    fs = S * {"full": 0.150, "simple": 0.165, "micro": 0.190}[detail]
    f = load_font(BRUSH_FONTS, int(fs))
    text = "万事屋"
    gaps = S * 0.012
    widths = [d.textlength(ch, font=f) for ch in text]
    total = sum(widths) + gaps * (len(text) - 1)
    tx = cx - total / 2
    ty = cy + h * 0.045
    for i, ch in enumerate(text):
        jitter = (-0.012, 0.004, -0.006)[i % 3] * S
        jrot = (-2.5, 1.5, -1.0)[i % 3]
        cw = max(4, int(widths[i] + fs * 0.35))
        chimg = Image.new("RGBA", (cw, int(fs * 1.5)), (0, 0, 0, 0))
        cd = ImageDraw.Draw(chimg)
        cd.text((cw / 2, chimg.height / 2), ch, font=f, fill=INK + (255,), anchor="mm")
        if detail == "full":
            cd.text((cw / 2 + fs * 0.012, chimg.height / 2 + fs * 0.010), ch, font=f,
                    fill=INK + (90,), anchor="mm")
        chimg = chimg.rotate(jrot, resample=Image.BICUBIC, expand=True)
        lay.alpha_composite(chimg, (int(tx + cw / 2 - chimg.width / 2),
                                    int(ty + jitter - chimg.height / 2)))
        tx += widths[i] + gaps

    if detail == "full":
        d.line([(cx + total * 0.34, ty + fs * 0.30), (cx + total * 0.30, ty + fs * 0.42)],
               fill=INK + (215,), width=max(2, int(fs * 0.13)))

    if tilt:
        lay = lay.rotate(tilt, resample=Image.BICUBIC, center=(lay.width / 2, lay.height / 2))
    return lay


# ---------------- 定春 ----------------

def _dog_layer(S, detail):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cx = S * 0.5
    full = detail == "full"

    # ---- 身体：横跨牌子上沿的一团绒毛 ----
    _fluffy(d, cx, S * 0.470, S * 0.400, S * 0.086, tufts=26, amp=0.10, fill=FUR_HI + (255,))
    d.ellipse([cx - S * 0.39, S * 0.492, cx + S * 0.39, S * 0.566], fill=FUR_LO + (90,))

    # ---- 耳朵：大、圆钝、外倾，耳内浅粉（先画耳，再被头压住根部）----
    for sgn in (-1, 1):
        tx, ty = cx + sgn * S * 0.196, S * 0.104
        b1 = (cx + sgn * S * 0.072, S * 0.246)
        b2 = (cx + sgn * S * 0.238, S * 0.272)
        # 耳外缘做成锯齿，和身上的毛一致
        pts = []
        for k in range(9):
            u = k / 8
            bx = b1[0] + (tx - b1[0]) * u
            by = b1[1] + (ty - b1[1]) * u
            pts.append((bx + sgn * (S * 0.006 if k % 2 else -S * 0.002), by))
        pts.append((tx + sgn * S * 0.014, ty + S * 0.020))
        for k in range(9):
            u = k / 8
            bx = (tx + sgn * S * 0.014) + (b2[0] - tx) * u
            by = (ty + S * 0.020) + (b2[1] - ty) * u
            pts.append((bx + sgn * (S * 0.004 if k % 2 else -S * 0.004), by))
        d.polygon(pts, fill=FUR_HI + (255,))
        # 内耳：浅粉，比外耳小一圈
        ip = []
        for k in range(9):
            u = k / 8
            ip.append((b1[0] + sgn * S * 0.030 + (tx - b1[0] - sgn * S * 0.022) * u,
                       b1[1] - S * 0.030 + (ty - b1[1] + S * 0.060) * u))
        ip.append((tx - sgn * S * 0.004, ty + S * 0.062))
        for k in range(9):
            u = k / 8
            ip.append((tx - sgn * S * 0.004 + (b2[0] - sgn * S * 0.052 - tx) * u,
                       ty + S * 0.062 + (b2[1] - S * 0.035 - ty) * u))
        d.polygon(ip, fill=EAR_PINK + (255,))
        d.polygon([(x - sgn * S * 0.002, y + S * 0.006) for x, y in ip],
                  fill=EAR_PINK_D + (150,))

    # ---- 头：锯齿绒毛轮廓 + 底部薄暗面 ----
    hx, hy, hrx, hry = cx, S * 0.376, S * 0.238, S * 0.200
    _fluffy(d, hx, hy, hrx, hry, tufts=30, amp=0.072, fill=FUR_HI + (255,), rot=0.11)
    # 只在最下缘压一点点暗，别罩到脸上
    d.ellipse([hx - hrx * 0.80, hy + hry * 0.78, hx + hrx * 0.80, hy + hry * 1.02],
              fill=FUR_LO + (80,))

    # ---- 腮帮被牌边压得鼓出来 ----
    for sgn in (-1, 1):
        _fluffy(d, hx + sgn * S * 0.130, S * 0.496, S * 0.066, S * 0.044,
                tufts=12, amp=0.055, fill=FUR_HI + (255,))

    # ---- 棕色眉纹（定春的招牌，代替之前的红色符文）----
    if detail != "micro":
        for sgn in (-1, 1):
            bx, by = hx + sgn * S * 0.098, S * 0.322
            pts = []
            for k in range(13):
                u = k / 12
                px = bx + (u - 0.5) * S * 0.078 * sgn
                py = by - math.sin(u * math.pi) * S * 0.026 - (u - 0.5) * S * 0.016
                wdt = S * (0.0135 - 0.006 * abs(u - 0.5) * 2)
                pts.append((px, py - wdt))
            for k in range(12, -1, -1):
                u = k / 12
                px = bx + (u - 0.5) * S * 0.078 * sgn
                py = by - math.sin(u * math.pi) * S * 0.026 - (u - 0.5) * S * 0.016
                wdt = S * (0.0135 - 0.006 * abs(u - 0.5) * 2)
                pts.append((px, py + wdt))
            d.polygon(pts, fill=BROW + (255,))
            if full:
                d.line([(bx - sgn * S * 0.030, by - S * 0.020),
                        (bx - sgn * S * 0.010, by - S * 0.034)],
                       fill=BROW_D + (150,), width=max(1, int(S * 0.004)))

    # ---- 大眼睛：黑色椭圆 + 高光（上眼睑略压 → 慵懒）----
    for sgn in (-1, 1):
        ex, ey = hx + sgn * S * 0.100, S * 0.404
        rx, ry = S * 0.0415, S * 0.0505
        d.ellipse([ex - rx, ey - ry, ex + rx, ey + ry], fill=INK + (255,))
        # 眼睑：压掉最上面一点
        d.ellipse([ex - rx * 1.04, ey - ry * 1.10, ex + rx * 1.04, ey - ry * 0.66],
                  fill=FUR_HI + (255,))
        if detail != "micro":
            d.ellipse([ex - rx * 0.62, ey - ry * 0.52, ex - rx * 0.02, ey - ry * 0.02],
                      fill=(255, 255, 255, 235))
            d.ellipse([ex + rx * 0.22, ey + ry * 0.12, ex + rx * 0.56, ey + ry * 0.42],
                      fill=(255, 255, 255, 165))

    # ---- 口鼻 ----
    d.ellipse([cx - S * 0.100, S * 0.438, cx + S * 0.100, S * 0.545], fill=FUR_HI + (255,))
    d.arc([cx - S * 0.100, S * 0.432, cx + S * 0.100, S * 0.550], start=15, end=165,
          fill=FUR_SHADE + (95,), width=max(1, int(S * 0.0045)))
    # 棕色鼻子（圆钝）
    ny = S * 0.458
    d.ellipse([cx - S * 0.030, ny - S * 0.020, cx + S * 0.030, ny + S * 0.022], fill=NOSE + (255,))
    d.ellipse([cx - S * 0.022, ny - S * 0.014, cx + S * 0.004, ny + S * 0.004],
              fill=(158, 114, 94, 200))

    # ---- 张嘴 + 舌 + 铃 ----
    if detail != "micro":
        # 口腔
        d.ellipse([cx - S * 0.052, S * 0.486, cx + S * 0.052, S * 0.556], fill=MOUTH_IN + (255,))
        # 上唇线
        d.line([(cx, ny + S * 0.020), (cx, S * 0.494)], fill=INK + (190,),
               width=max(1, int(S * 0.005)))
        # 舌
        d.ellipse([cx - S * 0.038, S * 0.512, cx + S * 0.038, S * 0.566], fill=PINK + (255,))
        d.line([(cx, S * 0.524), (cx, S * 0.552)], fill=PINK_HI + (190,),
               width=max(1, int(S * 0.005)))
    if full:
        # 铜铃：搁在舌头上（不响）
        bxx, byy, br = cx + S * 0.002, S * 0.548, S * 0.023
        _soft_shadow(lay, [bxx - br * 1.6, byy - br * 0.4, bxx + br * 1.6, byy + br * 1.5],
                     S * 0.010, 95)
        d.ellipse([bxx - br * 0.34, byy - br * 1.42, bxx + br * 0.34, byy - br * 0.72],
                  outline=(126, 86, 38, 240), width=max(1, int(S * 0.0045)))
        d.ellipse([bxx - br, byy - br, bxx + br, byy + br], fill=BELL + (255,))
        d.ellipse([bxx - br, byy - br, bxx + br, byy + br], outline=(116, 78, 34, 240),
                  width=max(1, int(S * 0.0045)))
        d.line([(bxx - br * 0.80, byy + br * 0.40), (bxx + br * 0.80, byy + br * 0.40)],
               fill=(108, 72, 30, 235), width=max(1, int(S * 0.005)))
        d.ellipse([bxx - br * 0.66, byy - br * 0.60, bxx - br * 0.16, byy - br * 0.10],
                  fill=BELL_HI + (225,))

    # ---- 红项圈：从下巴下露出一道（被牌沿挡掉一半）----
    if detail != "micro":
        cy0 = S * 0.560
        cw = S * 0.132
        _soft_shadow(lay, [cx - cw * 1.05, cy0 + S * 0.014, cx + cw * 1.05, cy0 + S * 0.062],
                     S * 0.014, 110)
        d.rounded_rectangle([cx - cw, cy0, cx + cw, cy0 + S * 0.032],
                            radius=S * 0.014, fill=COLLAR + (255,))
        d.rounded_rectangle([cx - cw, cy0 + S * 0.018, cx + cw, cy0 + S * 0.032],
                            radius=S * 0.007, fill=COLLAR_D + (205,))
        d.ellipse([cx - S * 0.015, cy0 + S * 0.001, cx + S * 0.015, cy0 + S * 0.032],
                  outline=BELL_HI + (235,), width=max(1, int(S * 0.0048)))

    # ---- 前爪：搭在牌沿，爪尖圆钝下垂 ----
    for sgn in (-1, 1):
        px = cx + sgn * S * 0.312
        pw, ph = S * 0.108, S * 0.098
        ptop = S * 0.516
        _soft_shadow(lay, [px - pw * 1.10, ptop + ph * 0.60, px + pw * 1.10, ptop + ph * 1.15],
                     S * 0.012, 105)
        _fluffy(d, px, ptop + ph * 0.5, pw * 0.5, ph * 0.5, tufts=14, amp=0.09,
                fill=FUR_HI + (255,))
        d.ellipse([px - pw * 0.42, ptop + ph * 0.52, px + pw * 0.42, ptop + ph * 0.98],
                  fill=FUR_LO + (110,))
        for k in (-1, 0, 1):
            fx = px + k * pw * 0.27
            d.line([(fx, ptop + ph * 0.58), (fx, ptop + ph * 0.90)],
                   fill=FUR_SHADE + (170,), width=max(1, int(S * 0.0040)))
            d.ellipse([fx - S * 0.0085, ptop + ph * 0.94, fx + S * 0.0085, ptop + ph * 1.05],
                      fill=(178, 186, 198, 235))

    return lay


# ---------------- 合成 ----------------

def render_sign(size, detail="full"):
    ss = 2 if size >= 512 else SS
    S = size * ss

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg = vgrad(S, BG_HI, BG_LO).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=int(S * 0.225), fill=255)
    img.alpha_composite(Image.composite(bg, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    if detail != "micro":
        img.alpha_composite(_radial(S, 0.82, 1.00, 0.70, 0.56, GLOW, 54))
        img.alpha_composite(_radial(S, 0.86, 1.02, 0.36, 0.32, GLOW, 76))

    _soft_shadow(img, [S * 0.05, S * 0.575, S * 0.95, S * 0.895], S * 0.020, 120)
    img.alpha_composite(_board_layer(S, detail))
    img.alpha_composite(_dog_layer(S, detail))

    return img.resize((size, size), Image.LANCZOS)
