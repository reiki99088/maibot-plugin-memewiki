#!/usr/bin/env python3
"""同步 cnmeme.wiki 公开词库到本地：列表 → 详情 → 索引。

用法：
    python scripts/sync_memes.py --data-dir <数据目录>              # 必填；MaiBot 下是 data/plugins/<插件ID>
    python scripts/sync_memes.py --data-dir <数据目录> --list-only  # 只更新标题索引，不抓详情
    python scripts/sync_memes.py --data-dir <数据目录> --sleep 0.5  # 放慢请求（默认 0.35 秒）

产物：
    <data-dir>/index.json      标题 → slug 字典（插件启动时加载）
    <data-dir>/items.json      列表快照（pack_data.py 重建 SQLite 时用）
    <data-dir>/details/*.json  词条详情（命中时才读）
    <data-dir>/memes.db        重建后的 SQLite（pack_data.py 产出）

增量：已存在的详情文件会跳过，只补新的；中途 Ctrl-C 可直接重跑。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://cnmeme.wiki/api/v1/public/memes"
UA = "MaiBot-memewiki/0.1.0 (+https://github.com/reiki99088/maibot-plugin-memewiki)"
PAGE_LIMIT = 30
DEFAULT_SLEEP = 0.35
MAX_RETRY = 3


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def api_get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_get_retry(url: str, what: str) -> dict | None:
    for attempt in range(MAX_RETRY):
        try:
            return api_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("Retry-After") or 5)
                log(f"  429 限流，等 {wait}s（{what}）")
                time.sleep(wait)
                continue
            log(f"  HTTP {exc.code}（{what}）")
            return None
        except Exception as exc:  # noqa: BLE001
            log(f"  请求失败：{type(exc).__name__} {exc}（{what}）")
            time.sleep(1 + attempt)
    return None


def fetch_list(sleep: float) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        data = api_get_retry(f"{API_BASE}?limit={PAGE_LIMIT}&offset={offset}", f"list@{offset}")
        if not data:
            break
        batch = data.get("items") or []
        if not batch:
            break
        items.extend(batch)
        offset += PAGE_LIMIT
        if offset % 600 == 0:
            log(f"列表进度 {len(items)} 条")
        if not data.get("has_more"):
            break
        time.sleep(sleep)
    log(f"列表完成：{len(items)} 条")
    return items


def write_index(data_dir: Path, items: list[dict]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for it in items:
        title = (it.get("title") or "").strip()
        slug = it.get("slug") or ""
        if title and slug and title not in titles:
            titles[title] = slug

    detail_dir = data_dir / "details"
    cached = len(list(detail_dir.glob("*.json"))) if detail_dir.is_dir() else 0
    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "built_at": built_at,
        "count": len(titles),
        "details_cached": cached,
        "source": API_BASE,
        "titles": titles,
    }
    out = data_dir / "index.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)

    # pack_data.py 读 items.json（列表快照），必须一起落盘，否则重建 SQLite 会失败
    items_out = data_dir / "items.json"
    items_tmp = items_out.with_suffix(".tmp")
    items_tmp.write_text(
        json.dumps({"built_at": built_at, "count": len(items), "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    items_tmp.replace(items_out)

    log(
        f"索引已写入 {out}（{len(titles)} 条，详情缓存 {cached} 条，{out.stat().st_size // 1024} KB）"
        f" + items.json（{items_out.stat().st_size // 1024} KB）"
    )
    return titles


MAX_NAME_BYTES = 240   # ext4/xfs 单个文件名上限 255 字节，留点余量


def _detail_name(slug: str) -> str | None:
    """slug → 详情文件名；整句当 slug 的极端词条会超过文件名上限，直接跳过缓存。"""
    name = urllib.parse.quote(slug, safe="") + ".json"
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        return None
    return name


def fetch_details(data_dir: Path, titles: dict[str, str], sleep: float, limit: int = 0) -> None:
    detail_dir = data_dir / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)

    pending: list[tuple[str, str]] = []
    too_long = 0
    for title, slug in titles.items():
        name = _detail_name(slug)
        if name is None:
            too_long += 1
            continue
        if not (detail_dir / name).exists():
            pending.append((title, slug))
    if limit:
        pending = pending[:limit]

    total = len(pending)
    if too_long:
        log(f"跳过 {too_long} 条 slug 过长（文件名超限）的词条")
    if not total:
        log("详情已是最新，无需抓取")
        return
    log(f"待抓详情 {total} 条")

    ok = fail = 0
    for idx, (title, slug) in enumerate(pending, 1):
        url = API_BASE + "/" + urllib.parse.quote(slug, safe="")
        data = api_get_retry(url, slug[:40])
        if not data or not data.get("item"):
            fail += 1
        else:
            name = _detail_name(slug)
            try:
                if name is None:
                    raise OSError("文件名超限")
                (detail_dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                ok += 1
            except OSError as exc:
                fail += 1
                log(f"  写盘失败（{type(exc).__name__}）：{slug[:30]}")
        if idx % 100 == 0 or idx == total:
            log(f"详情进度 {idx}/{total}  成功 {ok} 失败 {fail}")
        time.sleep(sleep)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 cnmeme.wiki 词库到本地")
    parser.add_argument("--data-dir", required=True, help="插件数据目录（MaiBot 下为 data/plugins/<插件ID>），不要指向插件源码目录")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    parser.add_argument("--list-only", action="store_true", help="只更新索引，不抓详情")
    parser.add_argument("--limit", type=int, default=0, help="本次最多抓多少条详情（0 = 全部）")
    parser.add_argument("--reuse-items", action="store_true", help="复用 data-dir 里已有的 items.json，不重新拉列表")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    log(f"数据目录：{data_dir}")

    items: list[dict] = []
    if args.reuse_items:
        cached = data_dir / "items.json"
        if cached.exists():
            items = json.loads(cached.read_text(encoding="utf-8")).get("items") or []
            log(f"复用已有列表：{len(items)} 条（{cached}）")
    if not items:
        items = fetch_list(args.sleep)
    if not items:
        log("没取到列表，退出")
        return 1

    titles = write_index(data_dir, items)
    if args.list_only:
        return 0

    fetch_details(data_dir, titles, args.sleep, args.limit)
    write_index(data_dir, items)

    # 重建 SQLite（插件优先读它）
    pack = Path(__file__).with_name("pack_data.py")
    if pack.exists():
        log("重建 SQLite 词库…")
        rc = subprocess.run(
            [sys.executable, str(pack), "--source", str(data_dir), "--out", str(data_dir / "memes.db")],
        ).returncode
        if rc != 0:
            log(f"警告：pack_data 退出码 {rc}，memes.db 可能未更新")

    log("同步完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
