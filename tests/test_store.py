import json
import sqlite3

from memewiki.store import MemeStore

from conftest import BUNDLED_DB


def make_store(tmp_path, logger, bundled=BUNDLED_DB):
    store = MemeStore(tmp_path / "data", bundled, logger)
    return store


def test_bundled_db_info(tmp_path, logger):
    info = make_store(tmp_path, logger).reload()
    assert info["count"] == "6668"
    assert info["built_at"] == "2026-10-26 19:07:22"


def test_exact_match(tmp_path, logger):
    store = make_store(tmp_path, logger)
    assert store.match("尼罗河二圣") == "尼罗河二圣"
    data = store.row("尼罗河二圣")
    assert data["title"] == "尼罗河二圣" and data["slug"]


def test_contained_title_takes_longest(tmp_path, logger):
    store = make_store(tmp_path, logger)
    assert store.match("你说你是什么冠军") == "你是什么冠军"


def test_prefix_of_title_does_not_match(tmp_path, logger):
    # 消息只是标题的前缀不算命中
    assert make_store(tmp_path, logger).match("你是什么") is None


def test_single_char_title_only_matches_exactly(tmp_path, logger):
    store = make_store(tmp_path, logger)
    assert store.match("给") == "给"            # 库里确有单字词条「给」
    assert store.match("给我跪下") is None      # 句子里包含时，单字标题不参与匹配


def test_synced_db_takes_priority(tmp_path, logger):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "memes.db")
    conn.execute("CREATE TABLE memes (title TEXT PRIMARY KEY, slug TEXT, tags TEXT, usage TEXT, origin TEXT, other TEXT, summary TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO memes VALUES ('新梗测试', 'xin', '测试', '这样用', '', '', '')")
    conn.execute("INSERT INTO meta VALUES ('count', '1')")
    conn.commit()
    conn.close()
    store = make_store(tmp_path, logger)
    assert store.reload()["count"] == "1"
    assert store.match("新梗测试") == "新梗测试"
    assert store.match("尼罗河二圣") is None


def test_index_json_fallback(tmp_path, logger):
    data_dir = tmp_path / "data"
    (data_dir / "details").mkdir(parents=True)
    (data_dir / "index.json").write_text(json.dumps({"titles": {"测试梗": "ce-shi"}}), encoding="utf-8")
    store = make_store(tmp_path, logger, bundled=tmp_path / "missing.db")
    store.reload()
    assert not store.has_db()
    assert store.match("这就是测试梗吧") == "测试梗"
    assert store.slug_for("测试梗") == "ce-shi"
    store.save_detail("ce-shi", {"item": {"title": "测试梗", "slug": "ce-shi"}})
    assert store.local_detail("ce-shi")["title"] == "测试梗"


def test_no_open_handle_after_query(tmp_path, logger):
    """每次查询都关连接：查完之后库文件可以被替换（Windows 上常驻句柄会 WinError 32）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "memes.db"
    db.write_bytes(BUNDLED_DB.read_bytes())
    store = make_store(tmp_path, logger)
    assert store.match("尼罗河二圣")
    replacement = data_dir / "memes.db.building"
    replacement.write_bytes(db.read_bytes())
    replacement.replace(db)
    db.unlink()
