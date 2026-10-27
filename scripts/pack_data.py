#!/usr/bin/env python3
"""把抓好的词条打包成随插件分发的 SQLite 数据库。

用法：
    python scripts/pack_data.py --source /root/cnmeme --out data/memes.db

产物 `memes.db` 含一张表：
    memes(title PRIMARY KEY, slug, tags, usage, origin, other, summary)
以及 meta(built_at, count)。

「用法 / 出处」在这里就拆好，运行时不用再解析 HTML。
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path


def html_to_text(raw: str) -> str:
    s = re.sub(r"<h2[^>]*>", "\n## ", raw or "", flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</p>|<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def split_sections(text: str) -> dict[str, str]:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="抓取数据目录（含 items.json 与 details/）")
    ap.add_argument("--out", required=True, help="输出 db 路径")
    args = ap.parse_args()

    src = Path(args.source).expanduser()
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再整体替换：插件每次查询都会重开只读连接，不能让它读到建了一半的库
    build = out.with_name(out.name + ".building")
    if build.exists():
        build.unlink()

    items_path = src / "items.json"
    detail_dir = src / "details"
    if not items_path.exists() or not detail_dir.is_dir():
        print(f"缺少数据：{items_path} 或 {detail_dir}")
        return 1

    items = json.loads(items_path.read_text(encoding="utf-8")).get("items") or []
    print(f"列表 {len(items)} 条，详情目录 {detail_dir}")

    conn = sqlite3.connect(build)
    conn.execute(
        "CREATE TABLE memes ("
        "title TEXT PRIMARY KEY, slug TEXT, tags TEXT, usage TEXT, origin TEXT, other TEXT, summary TEXT)"
    )
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    written = missing = 0
    for idx, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        slug = (it.get("slug") or "").strip()
        if not title or not slug:
            continue
        path = detail_dir / (urllib.parse.quote(slug, safe="") + ".json")
        try:
            if not path.exists():
                missing += 1
                continue
            item = json.loads(path.read_text(encoding="utf-8")).get("item") or {}
        except OSError:
            # slug 太长（文件名超过 255 字节）等情况，直接跳过
            missing += 1
            continue
        except Exception:
            missing += 1
            continue
        body = html_to_text(item.get("content_html") or "") or (item.get("summary") or "")
        sec = split_sections(body)
        conn.execute(
            "INSERT OR REPLACE INTO memes VALUES (?,?,?,?,?,?,?)",
            (
                title,
                slug,
                "、".join(item.get("tags") or []),
                sec["usage"],
                sec["origin"],
                sec["other"],
                item.get("summary") or "",
            ),
        )
        written += 1
        if idx % 1000 == 0:
            print(f"  已处理 {idx}/{len(items)}")

    conn.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('count', ?)", (str(written),))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    build.replace(out)

    size_mb = out.stat().st_size / 1048576
    print(f"写入 {written} 条（缺详情 {missing}），db {size_mb:.1f} MB → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
