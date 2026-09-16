# -*- coding: utf-8 -*-
"""Yorozuya 图标 v5 · 圆形徽章（定春）

自上而下 / 自下而上分六层：
  ① 圆形底：浅米黄 → 淡茶色的径向渐变 + 和纸纤维纹理
  ② 底纹：半透明淡墨大字「银」
  ③ 左下斜插洞爷湖木刀（只露缠绳刀柄）、右下探出新八圆眼镜（淡蓝反光）
  ④ 中心：几乎撑满圆形的定春头部（蓬松白毛 / 微垂耳 / 眯眼细缝 / 倒三角鼻 / 小舌 / 问号呆毛）
  ⑤ 鲜红项圈 → 黄铜铃（横竖刻纹）→ 菱形小木牌（毛笔「万事屋」）
  ⑥ 外圈：带飞白与磨损缺口的手绘墨线边框

手绘描边的做法：先用较宽的墨线沿轮廓走一圈，再用**内缩一圈**的浅色填充盖上去，
只留下墨线的外半边 → 得到宽度均匀、边缘略有起伏的扁平描边（贴纸感）。
"""
import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# ---------------- 配色（白 / 暖木 / 红 / 黑 / 银）----------------
CREAM = (250, 244, 228)
TEA = (233, 213, 178)
TEA_EDGE = (216, 190, 148)
FIBER_D = (190, 162, 114)
FIBER_L = (255, 251, 238)
INK = (52, 46, 40)
INK_WATER = (108, 106, 104)
FUR = (254, 253, 251)
FUR_SH = (226, 221, 213)
EAR_PINK = (240, 180, 182)
NOSE_PINK = (228, 144, 154)
NOSE_PINK_D = (198, 108, 122)
TONGUE = (234, 148, 158)
TONGUE_HI = (248, 186, 192)
MOUTH_IN = (106, 56, 56)
RED = (198, 54, 52)
RED_D = (150, 32, 30)
BRASS = (198, 152, 76)
BRASS_D = (140, 100, 42)
BRASS_L = (238, 206, 138)
WOOD = (214, 176, 122)
WOOD_D = (166, 128, 78)
WOOD_L = (238, 214, 170)
WOOD_LINE = (150, 112, 64)
GLASS_INK = (58, 56, 54)
BROW = (146, 122, 118)          # 定春的招牌眉毛（淡褐）
EYE_BG = (34, 36, 44)
EYE_BLUE = (92, 122, 154)       # 瞳下半的青灰反光
EYE_BLUE2 = (138, 168, 198)
UMB_HI = (170, 140, 210)        # 神乐的紫伞
UMB = (126, 94, 174)
UMB_LO = (86, 58, 126)
UMB_LINE = (74, 48, 108)
POLE = (198, 164, 110)
POLE_D = (140, 106, 60)
LENS = (252, 254, 255)
LENS_BLUE = (168, 202, 232)

BRUSH_FONTS = [r"C:\Windows\Fonts\STXINGKA.TTF",
               r"C:\Windows\Fonts\STKAITI.TTF",
               r"C:\Windows\Fonts\STZHONGS.TTF"]

# ---------------- 构图参数 ----------------
R_CLIP = 0.452          # 内容圆半径
R_RING = 0.457          # 墨线圆环半径
HEAD_C = (0.500, 0.470)
HEAD_R = (0.318, 0.274)
OUTLINE = 0.0155        # 主墨线总宽（相对 S）


def load_font(cands, size):
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _lerp(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


# ---------------- 基础工具 ----------------

def _disc_mask(S, r):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).ellipse([S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r], fill=255)
    return m


def _disc_base(S, r_mid=0.62):
    n = 160
    small = Image.new("RGB", (n, n))
    px = small.load()
    c, R = (n - 1) / 2, n / 2
    for y in range(n):
        for x in range(n):
            d = math.hypot(x - c, y - c) / R
            col = (_lerp(CREAM, TEA, (d / r_mid) ** 1.2) if d <= r_mid
                   else _lerp(TEA, TEA_EDGE, min(1.0, (d - r_mid) / (1 - r_mid))))
            px[x, y] = col
    return small.resize((S, S), Image.LANCZOS).convert("RGBA")


def _poly_mask(S, pts, blur=0):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    if blur:
        m = m.filter(ImageFilter.GaussianBlur(blur))
    return m


def _clip(base, lay, mask):
    a = lay.getchannel("A")
    lay = lay.copy()
    lay.putalpha(ImageChops.multiply(a, mask))
    return Image.alpha_composite(base, lay)


def _smooth(pts, samples=12):
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for j in range(samples):
            t = j / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(P[-2])
    return out


def _taper(d, pts, w0, w1, color, alpha=255):
    """变宽笔画（呆毛、绒毛、眯眼细缝都用它）。"""
    n = len(pts)
    if n < 2:
        return
    left, right = [], []
    for i, p in enumerate(pts):
        t = i / (n - 1)
        w = (w0 + (w1 - w0) * t) / 2
        if i == 0:
            dx, dy = pts[1][0] - p[0], pts[1][1] - p[1]
        elif i == n - 1:
            dx, dy = p[0] - pts[-2][0], p[1] - pts[-2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        left.append((p[0] + nx * w, p[1] + ny * w))
        right.append((p[0] - nx * w, p[1] - ny * w))
    d.polygon(left + right[::-1], fill=color + (alpha,))
    for p, w in ((pts[0], w0 / 2), (pts[-1], w1 / 2)):
        d.ellipse([p[0] - w, p[1] - w, p[0] + w, p[1] + w], fill=color + (alpha,))


def _crescent(d, p0, p1, bulge, thick, color, alpha=255):
    """弯月形：p0→p1 之间向上拱起，外弧比内弧多凸出 thick（两端自然收成尖）——
    定春那两道招牌眉毛就是这个形状。"""
    n = 26
    mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    c_out = (mid[0], mid[1] - bulge)
    c_in = (mid[0], mid[1] - bulge + thick)

    def quad(a, c, b, t):
        return ((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t ** 2 * b[0],
                (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t ** 2 * b[1])

    outer = [quad(p0, c_out, p1, i / n) for i in range(n + 1)]
    inner = [quad(p0, c_in, p1, i / n) for i in range(n + 1)]
    d.polygon(outer + inner[::-1], fill=color + (alpha,))


def _umbrella_layer(S, ang, center):
    """神乐的紫伞：五瓣油纸伞面 + 伞骨 + 竹伞杆（整支旋转后贴）。"""
    r = S * 0.132
    dome = r * 0.56
    sag = r * 0.34
    L = int(S * 1.18)
    lay = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    mx = L * 0.5
    ay = L * 0.5 - sag * 0.55          # 伞面居中于图层（旋转以伞面为中心）

    # 伞杆（顶端到下方，下段会被定春挡住）
    pw = max(2, int(S * 0.0125))
    d.line([(mx, ay - dome - S * 0.062), (mx, L * 0.985)], fill=POLE + (255,), width=pw)
    d.line([(mx - S * 0.002, ay - dome - S * 0.062), (mx - S * 0.002, L * 0.985)],
           fill=POLE_D + (150,), width=max(1, int(S * 0.0035)))
    # 伞尖
    d.polygon([(mx, ay - dome - S * 0.082), (mx - S * 0.013, ay - dome - S * 0.016),
               (mx + S * 0.013, ay - dome - S * 0.016)], fill=UMB_LO + (255,))

    # 伞面：上弧 + 五瓣波浪下沿
    up = [(mx - r * math.cos(math.pi * i / 60), ay - dome * math.sin(math.pi * i / 60))
          for i in range(61)]
    low = []
    for i in range(73):
        th = math.pi * i / 72
        x = mx + r * math.cos(th)
        y = ay + sag * math.sin(th) + 0.075 * r * abs(math.sin(5 * th)) ** 0.7
        low.append((x, y))
    d.polygon(up + low, fill=UMB + (255,))
    # 受光面
    d.polygon([(mx - r * 0.92, ay - dome * 0.30), (mx - r * 0.30, ay - dome * 0.92),
               (mx - r * 0.10, ay - dome * 0.86), (mx - r * 0.60, ay + sag * 0.36)],
              fill=UMB_HI + (110,))
    # 伞骨
    for k in range(6):
        th = math.pi * (k + 0.5) / 6
        xe = mx + r * math.cos(th)
        ye = ay + sag * math.sin(th) + 0.075 * r * abs(math.sin(5 * th)) ** 0.7
        d.line([(mx, ay - dome * 0.72), (xe, ye)], fill=UMB_LINE + (175,),
               width=max(1, int(S * 0.0050)))
    # 外形描边
    d.line(up + low, fill=UMB_LO + (255,), width=max(2, int(S * 0.0092)), joint="curve")

    lay = lay.rotate(ang, resample=Image.BICUBIC)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.alpha_composite(lay, (int(center[0] * S - L / 2), int(center[1] * S - L / 2)))
    return out


# ---------------- ① 和纸纤维 ----------------

def _fibers(S, rng, detail):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    w = max(1, int(S * 0.0016))
    count = {"full": 640, "simple": 260, "micro": 0}[detail]
    for i in range(count):
        light = i % 5 == 0
        col = FIBER_L if light else FIBER_D
        a0, a1 = ((16, 42) if light else (10, 26))
        ang = rng.uniform(0, math.pi)
        aa = rng.uniform(0, 2 * math.pi)
        rr = math.sqrt(rng.random()) * S * 0.468
        x, y = S / 2 + math.cos(aa) * rr, S / 2 + math.sin(aa) * rr
        L = rng.uniform(0.012, 0.055) * S
        bend = rng.uniform(-0.010, 0.010) * S
        d.line([(x, y),
                (x + math.cos(ang) * L * 0.5 - math.sin(ang) * bend,
                 y + math.sin(ang) * L * 0.5 + math.cos(ang) * bend),
                (x + math.cos(ang) * L, y + math.sin(ang) * L)],
               fill=col + (rng.randint(a0, a1),), width=w, joint="curve")
    for _ in range({"full": 90, "simple": 40, "micro": 0}[detail]):
        aa = rng.uniform(0, 2 * math.pi)
        rr = math.sqrt(rng.random()) * S * 0.45
        x, y = S / 2 + math.cos(aa) * rr, S / 2 + math.sin(aa) * rr
        r = rng.uniform(0.0012, 0.0032) * S
        d.ellipse([x - r, y - r, x + r, y + r], fill=FIBER_D + (rng.randint(14, 34),))
    return lay


# ---------------- ② 淡墨「银」--------------

IK_SIZE, IK_CY, IK_ALPHA, IK_ROT = 1.10, 0.40, 46, -8


def _ink_char(S, detail):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if detail == "micro":
        return lay
    f = load_font(BRUSH_FONTS, int(S * IK_SIZE))
    tmp = Image.new("RGBA", (int(S * 0.92), int(S * 0.92)), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((tmp.width / 2, tmp.height / 2), "银", font=f,
                             fill=INK_WATER + (IK_ALPHA if detail == "full" else int(IK_ALPHA * 0.7),), anchor="mm")
    tmp = tmp.rotate(IK_ROT, resample=Image.BICUBIC)
    lay.alpha_composite(tmp, (int(S / 2 - tmp.width / 2), int(S * IK_CY - tmp.height / 2)))
    return lay


# ---------------- ③ 洞爷湖木刀（只露缠绳刀柄）----------------

def _sword_layer(S, ang, center):
    total = 0.46 * S
    side = int(total * 1.5)
    lay = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cy = side / 2
    x0 = (side - total) / 2
    x1 = x0 + total
    bw = S * 0.072
    hl = total * 0.215
    hx = x0 + hl
    d.polygon([(hx, cy - bw * 0.50), (x1 - S * 0.028, cy - bw * 0.40),
               (x1, cy - bw * 0.24), (x1, cy + bw * 0.24),
               (x1 - S * 0.028, cy + bw * 0.40), (hx, cy + bw * 0.50)],
              fill=WOOD_L + (255,))
    d.polygon([(hx, cy + bw * 0.06), (x1 - S * 0.028, cy + bw * 0.10),
               (x1 - S * 0.028, cy + bw * 0.40), (hx, cy + bw * 0.50)],
              fill=WOOD + (255,))
    for k in (-0.22, 0.02, 0.24):
        d.line([(hx + S * 0.006, cy + bw * k), (x1 - S * 0.036, cy + bw * k)],
               fill=WOOD_LINE + (105,), width=max(1, int(S * 0.0035)))
    # 刀背高光 + 刀刃暗线，让那一小截刀身看得出是"刀"
    d.line([(hx + S * 0.006, cy - bw * 0.38), (x1 - S * 0.032, cy - bw * 0.28)],
           fill=(252, 240, 214, 180), width=max(1, int(S * 0.005)))
    d.line([(hx + S * 0.006, cy + bw * 0.44), (x1 - S * 0.030, cy + bw * 0.34)],
           fill=WOOD_D + (200,), width=max(1, int(S * 0.0040)))
    hy = bw * 0.60
    d.rounded_rectangle([x0, cy - hy, hx, cy + hy], radius=hy * 0.5, fill=WOOD + (255,))
    d.rounded_rectangle([x0, cy - hy, hx, cy + hy], radius=hy * 0.5,
                        outline=WOOD_D + (255,), width=max(1, int(S * 0.0035)))
    ww = max(2, int(S * 0.0088))
    for i in range(4):
        wx = x0 + hy * 0.70 + (hl - hy * 1.40) * i / 3
        d.line([(wx, cy - hy * 0.92), (wx + hy * 0.68, cy + hy * 0.92)],
               fill=(74, 56, 44, 235), width=ww)
    d.rounded_rectangle([x0 - S * 0.013, cy - hy * 0.74, x0 + S * 0.007, cy + hy * 0.74],
                        radius=hy * 0.32, fill=WOOD_D + (255,))
    d.rounded_rectangle([hx - S * 0.012, cy - hy * 1.22, hx + S * 0.010, cy + hy * 1.22],
                        radius=hy * 0.28, fill=(96, 70, 44, 255))
    lay = lay.rotate(ang, resample=Image.BICUBIC)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.alpha_composite(lay, (int(center[0] * S - side / 2), int(center[1] * S - side / 2)))
    return out


# ---------------- ③ 新八圆眼镜 ----------------

def _glasses_layer(S, ang, center):
    side = int(S * 0.375)
    lay = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    c = side / 2
    lr = S * 0.047
    off = S * 0.058
    fw = max(2, int(S * 0.0082))
    d.line([(c + off + lr * 0.85, c + lr * 0.10), (c + off + lr * 2.6, c + lr * 1.7)],
           fill=GLASS_INK + (245,), width=fw)
    for sx in (-1, 1):
        ccx = c + sx * off
        d.ellipse([ccx - lr, c - lr, ccx + lr, c + lr], fill=LENS + (95,))
        d.pieslice([ccx - lr * 0.86, c - lr * 0.30, ccx + lr * 0.86, c + lr * 0.86],
                   0, 180, fill=LENS_BLUE + (170,))
        d.line([(ccx - lr * 0.58, c + lr * 0.20), (ccx + lr * 0.02, c - lr * 0.56)],
               fill=(255, 255, 255, 200), width=max(2, int(S * 0.0062)))
        d.ellipse([ccx - lr, c - lr, ccx + lr, c + lr], outline=GLASS_INK + (250,), width=fw)
    d.line([(c - off + lr, c - lr * 0.12), (c + off - lr, c - lr * 0.12)],
           fill=GLASS_INK + (250,), width=fw)
    lay = lay.rotate(ang, resample=Image.BICUBIC)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.alpha_composite(lay, (int(center[0] * S - side / 2), int(center[1] * S - side / 2)))
    return out


# ---------------- ④ 定春头部 ----------------

def _fur_contour(cx, cy, rx, ry, lobes=22, amp=0.042, phase=0.0, jitter=0.0020, n=460):
    """柔和弧线拼出的蓬松轮廓（顶部/两侧蓬、下巴平缓）；抖动是确定性的，
    这样内缩轮廓与墨线轮廓能严丝合缝地对齐。"""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        soft = 0.62 + 0.38 * (0.5 - 0.5 * math.sin(a))
        b = abs(math.sin((a * lobes + phase) / 2)) ** 0.50
        k = 1.0 + amp * soft * (b - 0.42)
        k += jitter * (math.sin(a * 23 + 1.7) + 0.55 * math.sin(a * 41 + 0.3))
        pts.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))
    return pts


def _ear(rot_deg, base, length, width, bend=0.6, droop=0.42, n=30):
    """一片耳朵（局部：基部在原点、尖端朝上），返回两条边的点。尖端外弯 + 微垂。"""
    inner, outer = [], []
    for i in range(n + 1):
        t = i / n
        w = width * math.sin(math.pi * (t ** 0.45)) ** 0.85 * (1 - t * 0.05)
        u = max(0.0, (t - 0.64) / 0.36)
        dx = bend * width * (u ** 1.6)
        dy = droop * length * (u ** 2.1)
        inner.append((-w / 2 + dx, -length * t + dy))
        outer.append((w / 2 + dx, -length * t + dy))
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)

    def tr(p):
        return (base[0] + p[0] * ca - p[1] * sa, base[1] + p[0] * sa + p[1] * ca)

    return [tr(p) for p in inner], [tr(p) for p in outer]


def _head_layer(S, detail, rng):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cx, cy = HEAD_C[0] * S, HEAD_C[1] * S
    rx, ry = HEAD_R[0] * S, HEAD_R[1] * S
    W = OUTLINE * S
    half = W / 2
    phase = rng.uniform(0, 1.2)

    lobes = 32 if detail == "full" else 17
    amp = 0.040 if detail == "full" else 0.050
    cont = _fur_contour(cx, cy, rx, ry, lobes=lobes, amp=amp, phase=phase)
    cont_in = _fur_contour(cx, cy, rx - half, ry - half, lobes=lobes, amp=amp, phase=phase)

    ears = []
    for sx in (-1, 1):
        base = (cx + sx * S * 0.150, cy - ry * 0.72)
        e_in, e_out = _ear(sx * 30, base, S * 0.250, S * 0.168, bend=sx * 0.50, droop=0.32)
        p_in, p_out = _ear(sx * 30, base, S * 0.250 - half, S * 0.168 - half * 2.2,
                           bend=sx * 0.50, droop=0.32)
        ears.append((e_in, e_out, e_in + e_out[::-1], p_in + p_out[::-1]))
        # 耳内浅粉
        k_in, k_out = _ear(sx * 30, (base[0] + sx * S * 0.010, base[1] - S * 0.048),
                           S * 0.132, S * 0.068, bend=sx * 0.50, droop=0.32)
        ears[-1] += (k_in + k_out[::-1],)

    # a) 耳朵墨线
    for e_in, e_out, _p, _q, _k in ears:
        _taper(d, e_in, W, W * 0.70, INK, 240)
        _taper(d, e_out, W, W * 0.70, INK, 240)
    # b) 头部墨线（三段宽度略有差异 → 手绘感）
    pts = _smooth(cont[::4], 5)
    n = len(pts)
    for a, b, w in ((0, n // 3, W * 0.95), (n // 3 - 1, 2 * n // 3, W * 1.04),
                    (2 * n // 3 - 1, n - 1, W * 0.90)):
        seg = pts[a:b + 1]
        if len(seg) > 1:
            _taper(d, seg, w, w, INK, 240)
    # c) 呆毛 + 耳侧绒毛（墨线 → 白芯，根部稍后会被头部填充盖住）
    t0 = cy - ry * 1.00
    hook = _smooth([(cx + 0.016 * S, t0 + S * 0.072),     # 根部（会被头部填充盖住）
                    (cx + S * 0.012, t0 - S * 0.010),
                    (cx - S * 0.020, t0 - S * 0.062),
                    (cx - S * 0.060, t0 - S * 0.096),
                    (cx - S * 0.098, t0 - S * 0.104),
                    (cx - S * 0.118, t0 - S * 0.082)], 24)
    _taper(d, hook, S * 0.019, S * 0.008, INK, 242)
    _taper(d, hook, S * 0.0125, S * 0.004, FUR, 255)
    for sx, e_in in ((-1, ears[0][0]), (1, ears[1][0])):
        tip = e_in[-1]
        tp = _smooth([(tip[0] - sx * S * 0.010, tip[1] + S * 0.012),
                      (tip[0] + sx * S * 0.026, tip[1] - S * 0.028),
                      (tip[0] + sx * S * 0.048, tip[1] - S * 0.072)], 14)
        _taper(d, tp, S * 0.036, S * 0.014, INK, 240)
        _taper(d, tp, S * 0.028, S * 0.007, FUR, 255)
    # d) 耳朵白填充（顺手擦掉穿过耳朵的头部墨线）
    for _a, _b, _p, poly_i, _k in ears:
        d.polygon(poly_i, fill=FUR + (255,))
    # e) 头部白填充（擦掉耳线在头内的部分 + 呆毛/绒毛的根部）
    d.polygon(cont_in, fill=FUR + (255,))
    # f) 下巴一点柔和阴影，仍在扁平风范围内
    shade = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shade, "RGBA").polygon(
        _fur_contour(cx, cy + ry * 0.24, rx * 0.90, ry * 0.84, lobes=lobes, amp=amp, phase=phase),
        fill=FUR_SH + (68,))
    shade.putalpha(ImageChops.multiply(shade.getchannel("A"),
                                       _poly_mask(S, cont, blur=S * 0.008)))
    lay.alpha_composite(shade)
    # g) 耳内浅粉
    for *_, k_poly in ears:
        d.polygon(k_poly, fill=EAR_PINK + (255,))

    # h) 睁开的大眼睛：圆瞳 + 下半青灰反光 + 两处高光（照参考图）
    ey = cy - ry * 0.035
    for sx in (-1, 1):
        ex = cx + sx * rx * 0.415
        erx, ery = S * 0.0665, S * 0.0775
        d.ellipse([ex - erx, ey - ery, ex + erx, ey + ery], fill=EYE_BG + (255,))
        d.ellipse([ex - erx * 0.86, ey - ery * 0.16, ex + erx * 0.86, ey + ery * 0.90],
                  fill=EYE_BLUE + (105,))
        d.ellipse([ex - erx * 0.62, ey + ery * 0.16, ex + erx * 0.62, ey + ery * 0.86],
                  fill=EYE_BLUE2 + (80,))
        d.ellipse([ex - erx * 0.62, ey - ery * 0.70, ex - erx * 0.08, ey - ery * 0.18],
                  fill=(255, 255, 255, 250))
        d.ellipse([ex + erx * 0.14, ey + ery * 0.26, ex + erx * 0.54, ey + ery * 0.68],
                  fill=(255, 255, 255, 215))
        d.ellipse([ex - erx, ey - ery, ex + erx, ey + ery],
                  outline=(46, 48, 56, 170), width=max(1, int(S * 0.0035)))

    # i) 招牌眉毛：两道淡褐色弯月（定春的特征，比红符文更还原）
    for sx in (-1, 1):
        ex = cx + sx * rx * 0.415
        y0 = ey - S * 0.126
        _crescent(d,
                  (ex + sx * S * 0.068, y0 + S * 0.018),
                  (ex - sx * S * 0.060, y0 + S * 0.032),
                  S * 0.046, S * 0.033, BROW, 252)

    # i) 粉色倒三角鼻
    ny = ey + S * 0.092
    nw, nh = S * 0.084, S * 0.062
    ntri = [(cx - nw / 2, ny - nh / 2), (cx + nw / 2, ny - nh / 2),
            (cx + nw * 0.07, ny + nh * 0.30), (cx - nw * 0.07, ny + nh * 0.30)]
    d.polygon(_smooth(ntri + [ntri[0]], 10), fill=NOSE_PINK + (255,))
    ntri2 = [(cx - nw * 0.22, ny - nh * 0.02), (cx + nw * 0.22, ny - nh * 0.02),
             (cx + nw * 0.07, ny + nh * 0.30), (cx - nw * 0.07, ny + nh * 0.30)]
    d.polygon(_smooth(ntri2 + [ntri2[0]], 10), fill=NOSE_PINK_D + (255,))

    # j) 微张的嘴 + 一小截舌头
    my = ny + S * 0.064
    mw, mh = S * 0.100, S * 0.064
    mouth = [(cx - mw / 2, my), (cx + mw / 2, my),
             (cx + mw * 0.30, my + mh * 0.46), (cx - mw * 0.30, my + mh * 0.46)]
    d.polygon(mouth, fill=MOUTH_IN + (255,))
    d.ellipse([cx - mw / 2, my - mh * 0.30, cx + mw / 2, my + mh * 0.16],
              fill=MOUTH_IN + (255,))
    for sx in (-1, 1):
        d.polygon([(cx + sx * mw * 0.30, my - mh * 0.04),
                   (cx + sx * mw * 0.44, my - mh * 0.04),
                   (cx + sx * mw * 0.35, my + mh * 0.20)], fill=(252, 252, 250, 255))
    d.ellipse([cx - mw * 0.36, my - mh * 0.04, cx + mw * 0.36, my + mh * 0.62],
              fill=TONGUE + (255,))
    d.ellipse([cx - mw * 0.20, my + mh * 0.16, cx + mw * 0.20, my + mh * 0.50],
              fill=TONGUE_HI + (255,))
    return lay


# ---------------- ⑤ 项圈 / 铜铃 / 菱形木牌 ----------------

def _collar_bell_plaque(S, detail, rng):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    cx = S * 0.5
    ow = max(2, int(S * 0.0068))

    top = _smooth([(S * 0.358, S * 0.742), (cx, S * 0.716), (S * 0.642, S * 0.742)], 24)
    bot = _smooth([(S * 0.642, S * 0.780), (cx, S * 0.814), (S * 0.358, S * 0.780)], 24)
    d.polygon(top + [(S * 0.646, S * 0.760)] + bot + [(S * 0.354, S * 0.760)],
              fill=RED + (255,))
    d.polygon([(p[0], p[1] + S * 0.021) for p in top] + [(S * 0.646, S * 0.780)] + bot
              + [(S * 0.354, S * 0.780)], fill=RED_D + (255,))
    _taper(d, _smooth(top[::3], 6), ow, ow, INK, 228)
    _taper(d, _smooth(bot[::3], 6), ow * 0.85, ow * 0.85, INK, 200)

    px, py = cx, S * 0.896
    hw, hh = S * 0.186, S * 0.056
    dia = [(px, py - hh), (px + hw, py), (px, py + hh), (px - hw, py)]
    d.polygon(dia, fill=WOOD_L + (255,))
    d.polygon([(px, py - hh * 0.55), (px + hw * 0.72, py), (px, py + hh), (px - hw * 0.72, py)],
              fill=WOOD + (255,))
    for k in (-0.26, 0.04, 0.34):
        d.line([(px - hw * (0.52 - abs(k) * 0.4), py + hh * k),
                (px + hw * (0.52 - abs(k) * 0.4), py + hh * k)],
               fill=WOOD_D + (110,), width=max(1, int(S * 0.003)))
    _taper(d, dia + [dia[0]], ow * 0.85, ow * 0.85, INK, 235)

    if detail != "micro":
        f = load_font(BRUSH_FONTS, int(S * 0.062))
        for i, ch in enumerate("万事屋"):
            cxx = px + (i - 1) * S * 0.063
            tmp = Image.new("RGBA", (int(S * 0.095), int(S * 0.095)), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((tmp.width / 2, tmp.height / 2), ch, font=f,
                                     fill=INK + (242,), anchor="mm")
            tmp = tmp.rotate(rng.uniform(-7, 7), resample=Image.BICUBIC)
            lay.alpha_composite(tmp, (int(cxx + rng.uniform(-0.004, 0.004) * S - tmp.width / 2),
                                      int(py + rng.uniform(-0.005, 0.005) * S - tmp.height / 2)))

    bx, by, br = cx, S * 0.826, S * 0.040
    d.ellipse([bx - br * 0.28, by - br * 1.34, bx + br * 0.28, by - br * 0.82],
              outline=BRASS_D + (245,), width=max(2, int(S * 0.006)))
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=BRASS + (255,))
    d.pieslice([bx - br, by - br * 1.02, bx + br, by + br * 0.90], 180, 360,
               fill=BRASS_L + (150,))
    if detail != "micro":
        lw = max(1, int(S * 0.0032))
        for k in (-0.34, 0.0, 0.34):
            d.line([(bx + br * k, by - br * 0.54), (bx + br * k, by + br * 0.52)],
                   fill=BRASS_D + (135,), width=lw)
        for k in (-0.22, 0.22):
            d.line([(bx - br * 0.54, by + br * k), (bx + br * 0.54, by + br * k)],
                   fill=BRASS_D + (135,), width=lw)
    d.ellipse([bx - br * 0.54, by - br * 0.60, bx - br * 0.06, by - br * 0.20],
              fill=(255, 247, 216, 190))
    d.arc([bx - br * 0.86, by - br * 0.08, bx + br * 0.86, by + br * 1.34],
          0, 180, fill=INK + (215,), width=max(1, int(S * 0.0045)))
    d.ellipse([bx - br, by - br, bx + br, by + br], outline=INK + (235,),
              width=max(2, int(S * 0.006)))
    return lay


# ---------------- ⑥ 手绘墨线边框（飞白 + 磨损缺口）----------------

def _ink_ring(S, detail, rng):
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    r = R_RING * S
    base_w = S * (0.019 if detail == "full" else 0.024)
    ph = [rng.uniform(0, 6.283) for _ in range(4)]

    def noise(a):
        return 0.5 + 0.5 * (0.46 * math.sin(3 * a + ph[0])
                            + 0.30 * math.sin(7 * a + ph[1])
                            + 0.24 * math.sin(19 * a + ph[2]))

    gaps = [(rng.uniform(0, 2 * math.pi), math.radians(rng.uniform(2.0, 7.0)))
            for _ in range({"full": 7, "simple": 5, "micro": 4}[detail])]

    def in_gap(ac):
        for c, w in gaps:
            if abs(((ac - c + math.pi) % (2 * math.pi)) - math.pi) < w / 2:
                return True
        return False

    steps = 420
    for i in range(steps):
        a0, a1 = 2 * math.pi * i / steps, 2 * math.pi * (i + 1) / steps
        ac = (a0 + a1) / 2
        if in_gap(ac):
            continue
        n1 = noise(ac)
        w = base_w * (0.55 + 0.80 * n1)
        rr = r * (1 + 0.010 * math.sin(2 * ac + ph[3]) + 0.005 * math.sin(5 * ac + ph[1]))
        alpha = int(150 + 105 * min(1.0, 0.25 + 1.05 * n1))
        for rad, wd, al in ((rr, w, alpha),):
            d.polygon([(S / 2 + math.cos(a0) * (rad - wd / 2), S / 2 + math.sin(a0) * (rad - wd / 2)),
                       (S / 2 + math.cos(a1) * (rad - wd / 2), S / 2 + math.sin(a1) * (rad - wd / 2)),
                       (S / 2 + math.cos(a1) * (rad + wd / 2), S / 2 + math.sin(a1) * (rad + wd / 2)),
                       (S / 2 + math.cos(a0) * (rad + wd / 2), S / 2 + math.sin(a0) * (rad + wd / 2))],
                      fill=INK + (al,))
        # 飞白：内侧叠一道更细更淡的枯笔
        if detail == "full" and n1 > 0.58 and rng.random() < 0.34:
            w2 = w * 0.36
            r2 = rr - w * 0.92
            d.polygon([(S / 2 + math.cos(a0) * (r2 - w2 / 2), S / 2 + math.sin(a0) * (r2 - w2 / 2)),
                       (S / 2 + math.cos(a1) * (r2 - w2 / 2), S / 2 + math.sin(a1) * (r2 - w2 / 2)),
                       (S / 2 + math.cos(a1) * (r2 + w2 / 2), S / 2 + math.sin(a1) * (r2 + w2 / 2)),
                       (S / 2 + math.cos(a0) * (r2 + w2 / 2), S / 2 + math.sin(a0) * (r2 + w2 / 2))],
                      fill=INK + (rng.randint(70, 130),))
    if detail == "full":
        for _ in range(18):
            a = rng.uniform(0, 2 * math.pi)
            rr = r * rng.uniform(0.95, 1.12)
            x, y = S / 2 + math.cos(a) * rr, S / 2 + math.sin(a) * rr
            rad = rng.uniform(0.0012, 0.0036) * S
            d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=INK + (rng.randint(90, 190),))
    return lay


# ---------------- 主渲染 ----------------

def render_sadaharu(size, detail="full"):
    ss = 6 if size <= 64 else (4 if size <= 320 else 3)
    S = size * ss
    rng = random.Random(20260914)
    mask = _disc_mask(S, R_CLIP * S)

    img = _disc_base(S)
    img = _clip(img, _fibers(S, rng, detail), mask)
    img = _clip(img, _ink_char(S, detail), mask)
    if detail != "micro":
        img = _clip(img, _umbrella_layer(S, 40, (0.600, 0.118)), mask)
        img = _clip(img, _sword_layer(S, 40, (0.396, 0.702)), mask)
        img = _clip(img, _glasses_layer(S, -16, (0.822, 0.716)), mask)
    img = _clip(img, _head_layer(S, detail, rng), mask)
    img = _clip(img, _collar_bell_plaque(S, detail, rng), mask)
    if detail == "full":
        vig = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(vig, "RGBA").ellipse(
            [S / 2 - R_CLIP * S, S / 2 - R_CLIP * S, S / 2 + R_CLIP * S, S / 2 + R_CLIP * S],
            outline=(74, 54, 32, 42), width=int(S * 0.040))
        img = _clip(img, vig.filter(ImageFilter.GaussianBlur(S * 0.018)), mask)
    img = Image.alpha_composite(img, _ink_ring(S, detail, rng))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    render_sadaharu(512, "full").save("icon/_try_dogbadge.png")
    print("rendered icon/_try_dogbadge.png")
