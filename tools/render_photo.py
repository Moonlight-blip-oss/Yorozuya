# -*- coding: utf-8 -*-
"""Yorozuya 图标 · 照片版（直接采用手绘原图）

原图：`icon/source_sadaharu.jpg`（1920×1920，圆形徽章几乎撑满画布）
处理：居中圆形裁切（半径 = 边长 × 0.494，保住毛边墨线）+ 3px 羽化 → 各尺寸直接缩放。
"""
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "icon" / "source_sadaharu.jpg"
MASTER = 1024
_CACHE = {}


def _master():
    if "m" in _CACHE:
        return _CACHE["m"]
    if not os.path.exists(SOURCE):
        raise FileNotFoundError("找不到原图：%s" % SOURCE)
    img = Image.open(SOURCE).convert("RGB")
    w, h = img.size
    side = min(w, h)
    img = img.crop([(w - side) // 2, (h - side) // 2,
                    (w + side) // 2, (h + side) // 2])

    # 圆形遮罩（边缘羽化，让徽章浮在任何底色上都干净）
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    r = int(side * 0.494)
    cx = cy = side / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(side / 1920 * 4.5))

    out = img.convert("RGBA")
    out.putalpha(mask)
    out = out.resize((MASTER, MASTER), Image.LANCZOS)
    _CACHE["m"] = out
    return out


def render_photo(size, detail="full"):
    return _master().resize((int(size), int(size)), Image.LANCZOS)


if __name__ == "__main__":
    render_photo(512).save(str(ROOT / "icon" / "_try_photo.png"))
    print("rendered icon/_try_photo.png")
