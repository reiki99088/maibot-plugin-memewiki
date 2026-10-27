"""词条正文整理：HTML → 纯文本、按小标题拆段、拼成给模型 / 给用户看的文本。"""

from __future__ import annotations

import html as htmllib
import re
from typing import Any


def html_to_text(raw: str) -> str:
    s = re.sub(r"<h2[^>]*>", "\n## ", raw or "", flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</p>|<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s[:n] + "…" if len(s) > n else s


def split_sections(text: str) -> dict[str, str]:
    """把词条正文按小标题拆开，分出「用法」「出处」「其它」。"""
    parts = re.split(r"\n##\s*", "\n" + (text or ""))
    usage, origin, other = [], [], []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        head, _, rest = part.partition("\n")
        head = head.strip()
        if any(k in head for k in ("用法", "怎么用", "如何用", "例句", "接梗")):
            usage.append(rest.strip() or head)
        elif any(k in head for k in ("来源", "出处", "背景", "起源", "介绍")):
            origin.append(rest.strip() or head)
        else:
            other.append((head + "\n" + rest).strip() if rest else head)
    return {
        "usage": "\n".join(usage).strip(),
        "origin": "\n".join(origin).strip(),
        "other": "\n".join(other).strip(),
    }


def db_row_data(row: tuple) -> dict[str, Any]:
    """memes 表一行 → 词条数据。"""
    title, slug, tags, usage, origin, other, summary = row
    usage = usage or ""
    origin = origin or ""
    if not usage and not origin:
        other = other or summary or ""
    return {
        "title": title,
        "tags": [t for t in (tags or "").split("、") if t],
        "usage": usage,
        "origin": origin,
        "other": other or "",
        "note": "",
        "extra": [],
        "slug": slug or "",
    }


def entry_data(item: dict, q: str, note: str = "", extra_titles: list[str] | None = None) -> dict[str, Any]:
    """cnmeme.wiki API 的 item → 词条数据。"""
    body = html_to_text(item.get("content_html") or "") or (item.get("summary") or "")
    sections = split_sections(body)
    usage = sections.get("usage") or ""
    origin = sections.get("origin") or ""
    other = sections.get("other") or ""
    if not usage and not origin:
        other = body
    return {
        "title": item.get("title") or q,
        "tags": [str(t) for t in (item.get("tags") or [])],
        "usage": usage.strip(),
        "origin": origin.strip(),
        "other": other.strip(),
        "note": (note or "").strip(),
        "extra": extra_titles or [],
        "slug": item.get("slug", ""),
    }


def who_text(entry: dict) -> str:
    uid = str(entry.get("author") or "")
    nick = str(entry.get("author_name") or "")
    if uid and nick:
        return f"{uid} / {nick}"
    return uid or nick or "群友"


def user_term_data(name: str, entry: dict) -> dict[str, Any]:
    """自定义黑话 → 词条数据（卡片和文字回复共用）。"""
    return {
        "title": name,
        "tags": ["群内黑话"],
        "usage": str(entry.get("meaning") or ""),
        "origin": "",
        "other": "",
        "note": "",
        "extra": [],
        "slug": "",
        "custom": f"群内黑话 · 由 {who_text(entry)} 添加",
    }


def user_term_text(name: str, entry: dict) -> str:
    return f"【群内黑话】{name}\n{entry.get('meaning', '')}\n（由 {who_text(entry)} 定义）"


def entry_text(data: dict, for_llm: bool = True) -> str:
    lines = [f"【梗百科】{data['title']}"]
    if data["note"]:
        lines.append(data["note"])
    if data["tags"]:
        lines.append("标签：" + "、".join(data["tags"]))

    if for_llm:
        # 给模型看：用法在前，方便直接接梗
        if data["usage"]:
            head = "【含义】" if data.get("custom") else "【怎么用 / 怎么接】"
            lines.append(head + "\n" + clip(data["usage"], 600))
        if data["origin"]:
            lines.append("【出处（仅用于理解，别照搬）】\n" + clip(data["origin"], 450))
    else:
        # 给用户看：先来源，后用法
        if data["origin"]:
            lines.append("【来源】\n" + clip(data["origin"], 450))
        if data["usage"]:
            head = "【含义】" if data.get("custom") else "【用法】"
            lines.append(head + "\n" + clip(data["usage"], 600))

    if data["other"]:
        lines.append(clip(data["other"], 400))
    if data["extra"]:
        lines.append("同批检索到的其它相关词条：" + "、".join(data["extra"]))
    lines.append(data.get("custom") or f"cnmeme.wiki/词条/{data['slug']}")
    return "\n".join(lines)
