import pytest

from memewiki import gate


@pytest.mark.parametrize(
    ("raw", "query"),
    [
        ("大水理论是什么梗", "大水理论"),
        ("寿来是啥意思？", "寿来"),
        ("这个寿来是啥意思？", "这个寿来"),  # 与上游一致只剥结尾的“这个”；本地库按包含匹配仍能命中「寿来」
        ("飞八分钱到底什么来头", "飞八分钱"),
        ("杂鱼", "杂鱼"),
        ("@麦麦 杂鱼杂鱼", "杂鱼杂鱼"),
        ("这是什么梗", None),            # 指代词，不知道问的是哪个
        ("哈哈哈", None),                # 停用词
        ("我今天去吃了顿很好吃的火锅，你觉得呢", None),
    ],
)
def test_extract_meme_query(raw, query):
    assert gate.extract_meme_query(raw) == query


def test_wake_words_are_stripped_longest_first():
    assert gate.extract_meme_query("小鲸鱼，大水理论是什么梗", ["鲸鱼", "小鲸鱼"]) == "大水理论"
    assert gate.extract_meme_query("麦麦 杂鱼", ["麦麦"]) == "杂鱼"


@pytest.mark.parametrize(
    ("query", "base"),
    [("杂鱼杂鱼", "杂鱼"), ("杂鱼杂鱼杂鱼", "杂鱼"), ("666666", "666"), ("已取餐已取餐", "已取餐"), ("666", "666"), ("杂鱼～", "杂鱼")],
)
def test_normalize_query(query, base):
    assert gate.normalize_query(query) == base


# 上游 README 的同一串 8 条消息：前 6 条（折叠后）值得联网，后两条被闸门挡掉
@pytest.mark.parametrize(
    ("raw", "worth"),
    [
        ("杂鱼", True),
        ("杂鱼杂鱼", True),
        ("杂鱼杂鱼杂鱼", True),
        ("666", True),
        ("666666", True),
        ("已取餐", True),
        ("不是你自己发的", False),
        ("这杂鱼是你说的", False),
    ],
)
def test_readme_sequence(raw, worth):
    query = gate.normalize_query(gate.extract_meme_query(raw))
    assert gate.worth_search(raw, query, 10) is worth


def test_worth_search_rules():
    assert gate.worth_search("我是谁", "我是谁") is False             # 闲聊高频误报
    assert gate.worth_search("你今天怎么样", "你今天怎么样") is False
    assert gate.worth_search("这个词什么意思", "这个词") is True      # 明确在问，无条件放行
    assert gate.worth_search("一二三四五六七八九十十一", "一二三四五六七八九十十一", 10) is False
    assert gate.worth_search("别再发了好吗", "别再发了好吗") is False
