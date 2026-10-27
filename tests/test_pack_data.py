"""pack_data.py 端到端：抓取目录 → SQLite → MemeStore 能读。不联网。"""

import json
import subprocess
import sys
import urllib.parse

from memewiki.store import MemeStore

from conftest import ROOT


def test_pack_and_read(tmp_path, logger):
    src = tmp_path / "src"
    details = src / "details"
    details.mkdir(parents=True)
    items = [{"title": "测试梗", "slug": "ce-shi"}, {"title": "缺详情", "slug": "que"}]
    (src / "items.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    (details / (urllib.parse.quote("ce-shi", safe="") + ".json")).write_text(
        json.dumps({"item": {
            "title": "测试梗", "slug": "ce-shi", "tags": ["测试"],
            # 与 cnmeme.wiki 实际返回的格式一致：</h2> 后面有换行
            "content_html": "<h2>用法</h2>\n<p>这样用</p>\n<h2>来源</h2>\n<p>来自测试</p>",
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out = data_dir / "memes.db"
    out.write_bytes(b"old")              # 旧库应被整体替换，而不是先删后写

    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "pack_data.py"), "--source", str(src), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not out.with_name("memes.db.building").exists()

    store = MemeStore(data_dir, tmp_path / "none.db", logger)
    assert store.reload()["count"] == "1"
    row = store.row("测试梗")
    assert row["usage"] == "这样用" and row["origin"] == "来自测试"
