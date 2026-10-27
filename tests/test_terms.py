import json

from memewiki.terms import UserTerms, load_group_slang, lookup_slang, validate_term


def test_validate_term():
    assert validate_term("狗东", "京东") is None
    assert validate_term("狗", "京东")              # 名称至少 2 字
    assert validate_term("一" * 17, "京东")
    assert validate_term("狗东", "")
    assert validate_term("狗东", "长" * 301)


def test_upsert_save_and_backup(tmp_path, logger):
    path = tmp_path / "user_terms.json"
    terms = UserTerms(path, logger)
    terms.load()
    assert terms.upsert("狗东", "京东", "qq:1", "小明") is False
    assert not path.with_name("user_terms.json.bak").exists()   # 第一次写没有旧版本可备份
    assert terms.upsert("狗东", "京东商城", "qq:2", "") is True
    backup = json.loads(path.with_name("user_terms.json.bak").read_text(encoding="utf-8"))
    assert backup["狗东"]["meaning"] == "京东"
    reloaded = UserTerms(path, logger)
    reloaded.load()
    assert reloaded.items["狗东"]["meaning"] == "京东商城"
    assert not path.with_suffix(".tmp").exists()


def test_find_and_remove(tmp_path, logger):
    terms = UserTerms(tmp_path / "user_terms.json", logger)
    terms.upsert("狗东", "京东", "qq:1", "")
    terms.upsert("狗东西", "骂人", "qq:1", "")
    assert terms.find("狗东")[0] == "狗东"
    assert terms.find("你这个狗东西")[0] == "狗东西"      # 句子里取最长的完整词
    assert terms.find("狗") is None
    assert terms.remove("狗东") is True
    assert terms.remove("狗东") is False


def test_broken_file_is_treated_as_empty(tmp_path, logger):
    path = tmp_path / "user_terms.json"
    path.write_text("{oops", encoding="utf-8")
    terms = UserTerms(path, logger)
    terms.load()
    assert terms.items == {}


def test_group_slang(tmp_path, logger):
    path = tmp_path / "group_slang.json"
    assert load_group_slang(path, logger) == {}
    path.write_text(json.dumps({"开黑": "一起打游戏"}, ensure_ascii=False), encoding="utf-8")
    slang = load_group_slang(path, logger)
    assert lookup_slang(slang, "开黑") == "一起打游戏"
    assert lookup_slang(slang, "今晚开黑吗") == "一起打游戏"
    assert lookup_slang(slang, "") is None
