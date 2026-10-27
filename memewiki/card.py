"""词条卡片：PIL 画 900px 宽的 PNG，返回字节，不落盘。"""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .textify import clip

# 按顺序找第一个能用的字体（Linux Noto / 文泉驿 → Windows / macOS 常见字体）
_FONT_CANDIDATES_REG = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


class NoCJKFontError(RuntimeError):
    """系统里找不到中文字体；画出来会是方框字，调用方应退回文字回复。"""


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (_FONT_CANDIDATES_BOLD if bold else []) + _FONT_CANDIDATES_REG
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    raise NoCJKFontError("找不到可用的中文字体")


# 不能出现在行首的标点：放不下时挤在上一行末尾（略超出一点宽度，比标点打头好看）
_NO_LINE_START = set("，。、；：？！）》」』】〉”’…,.;:?!)]}%")


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    out = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        cur = ""
        for ch in para:
            if font.getlength(cur + ch) <= width or (cur and ch in _NO_LINE_START):
                cur += ch
            else:
                if cur:
                    out.append(cur)
                cur = ch
        if cur:
            out.append(cur)
    return out


def render_card(data: dict) -> bytes:
    W, PAD = 900, 44
    font_title = _font(42, bold=True)
    font_sec = _font(26, bold=True)
    font_body = _font(24)
    font_meta = _font(20)
    font_small = _font(18)
    inner = W - PAD * 2

    blocks: list[tuple] = []   # (kind, lines, font, color, gap)
    blocks.append(("title", _wrap(data["title"], font_title, inner), font_title, (17, 24, 39), 0))
    if data["tags"]:
        blocks.append(("meta", _wrap("、".join(data["tags"][:8]), font_meta, inner), font_meta, (107, 114, 128), 12))
    if data["origin"]:
        blocks.append(("sec", ["来源"], font_sec, (37, 99, 235), 26))
        blocks.append(("body", _wrap(clip(data["origin"], 520), font_body, inner), font_body, (55, 65, 81), 10))
    if data["usage"]:
        sec_title = "含义" if data.get("custom") else "用法"
        blocks.append(("sec", [sec_title], font_sec, (37, 99, 235), 22))
        blocks.append(("body", _wrap(clip(data["usage"], 700), font_body, inner), font_body, (31, 41, 55), 10))
    if not data["origin"] and not data["usage"] and data.get("other"):
        blocks.append(("body", _wrap(clip(data["other"], 700), font_body, inner), font_body, (31, 41, 55), 22))
    if data["note"]:
        blocks.append(("meta", _wrap(data["note"], font_meta, inner), font_meta, (146, 64, 14), 14))
    if data["extra"]:
        blocks.append(("meta", _wrap("相关：" + "、".join(data["extra"]), font_meta, inner), font_meta, (107, 114, 128), 14))

    footer = data.get("custom") or f"cnmeme.wiki/词条/{data['slug']}"
    total = PAD
    for kind, lines, font, _color, gap in blocks:
        total += gap + int(font.size * 1.5) * len(lines) + (12 if kind == "title" else 0)
    total += 22 + int(font_small.size * 1.5) + PAD

    img = Image.new("RGB", (W, total), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 8], fill=(37, 99, 235))

    y = PAD
    for kind, lines, font, color, gap in blocks:
        y += gap
        lh = int(font.size * 1.5)
        for line in lines:
            draw.text((PAD, y), line, font=font, fill=color)
            y += lh
        if kind == "title":
            draw.line([(PAD, y + 10), (W - PAD, y + 10)], fill=(229, 231, 235), width=2)
            y += 12

    draw.line([(PAD, total - PAD - 26), (W - PAD, total - PAD - 26)], fill=(229, 231, 235), width=2)
    draw.text((PAD, total - PAD - 20), footer, font=font_small, fill=(156, 163, 175))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
