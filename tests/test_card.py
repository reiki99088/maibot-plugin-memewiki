import io

import pytest
from PIL import Image

from memewiki import card
from memewiki.store import MemeStore

from conftest import BUNDLED_DB


@pytest.fixture(autouse=True)
def need_cjk_font():
    try:
        card._font(20)
    except card.NoCJKFontError:
        pytest.skip("本机没有中文字体")


@pytest.mark.parametrize("title", ["尼罗河二圣", "命苦笑"])   # 命苦笑：用法和出处都为空的词条
def test_render_bundled_entry(tmp_path, logger, title):
    store = MemeStore(tmp_path, BUNDLED_DB, logger)
    png = card.render_card(store.row(title))
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width > 300 and image.height > 100


def test_render_user_term():
    from memewiki.textify import user_term_data

    data = user_term_data("狗东", {"meaning": "京东", "author": "qq:1", "author_name": "小明", "ts": "2026-10-27 12:00:00"})
    assert Image.open(io.BytesIO(card.render_card(data))).format == "PNG"


def test_wrap_keeps_punctuation_off_line_start():
    font = card._font(26)
    text = "该梗起源于网友对一个特定微笑表情的解读，呈现出一种为了笑而笑的用力感。" * 6
    lines = card._wrap(text, font, 300)
    assert len(lines) > 3
    assert not any(line[0] in card._NO_LINE_START for line in lines)
