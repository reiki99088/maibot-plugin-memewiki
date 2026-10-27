import asyncio
import json

from memewiki import prompts, service as service_mod
from memewiki.service import ServiceOptions


async def test_lookup_local_hit(service):
    text = await service.lookup("大水理论")
    assert "大水理论" in text and not text.startswith(prompts.NOT_FOUND_PREFIX)


async def test_lookup_miss_offline(service):
    assert (await service.lookup("杂鱼")).startswith(prompts.NOT_FOUND_PREFIX)
    assert await service.lookup("  ") == "查询词为空。"


async def test_priority_slang_then_user_term_then_wiki(service, tmp_path):
    await service.lookup("大水理论")                       # 先把 wiki 结果放进缓存
    service.terms.upsert("大水理论", "群里说的是另一回事", "qq:1", "")
    service.invalidate_cache()
    assert "群里说的是另一回事" in await service.lookup("大水理论")

    (service.data_dir / "group_slang.json").write_text(
        json.dumps({"大水理论": "群梗版本"}, ensure_ascii=False), encoding="utf-8"
    )
    service.load()
    assert await service.lookup("大水理论") == "【群内梗】大水理论\n群梗版本"


async def test_new_term_visible_inside_cached_sentence(service):
    """缓存键是原句：加黑话后必须整表清缓存，不然含这个词的旧句子还是老结果。"""
    sentence = "今天狗东发货好慢"
    assert (await service.lookup(sentence)).startswith(prompts.NOT_FOUND_PREFIX)
    service.terms.upsert("狗东", "京东", "qq:1", "")
    service.invalidate_cache()
    assert "京东" in await service.lookup(sentence)


async def test_find_returns_card_data(service):
    data = await service.find("尼罗河二圣")
    assert data["title"] == "尼罗河二圣"
    service.terms.upsert("狗东", "京东", "qq:1", "小明")
    assert (await service.find("狗东"))["title"] == "狗东"
    assert await service.find("完全不存在的词条xyz") is None


async def test_prefetch_local_hit(service):
    result = await service.prefetch("大水理论是什么梗")
    assert result.query == "大水理论"
    assert result.hint and "大水理论" in result.hint
    assert result.reason == "本地命中"


async def test_prefetch_not_meme_like(service):
    assert await service.prefetch("这是什么梗") is None
    result = await service.prefetch("不是你自己发的")
    assert result is not None and result.hint is None


async def test_prefetch_miss_with_online_disabled(service):
    result = await service.prefetch("杂鱼杂鱼")
    assert result.query == "杂鱼"
    assert "web_search" in result.hint
    service.apply_options(ServiceOptions(online_lookup=False, search_mode="off"))
    service.invalidate_cache()
    result = await service.prefetch("杂鱼")
    assert "web_search" not in result.hint


async def test_prefetch_online_timeout(service, monkeypatch):
    service.apply_options(ServiceOptions(online_lookup=True))
    monkeypatch.setattr(service_mod, "FAST_TIMEOUT", 0.05)

    async def slow_search(query, limit=5):
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(service.client, "search", slow_search)
    result = await service.prefetch("杂鱼")
    assert result.hint is None and "超时" in result.reason
    # 超时也记进未命中缓存，10 分钟内不再联网
    result = await service.prefetch("杂鱼")
    assert "跳过重复联网" in result.reason


async def test_prefetch_online_hit(service, monkeypatch):
    service.apply_options(ServiceOptions(online_lookup=True))

    async def fake_search(query, limit=5):
        return [{"title": "杂鱼", "slug": "za-yu", "summary": "嘲讽对方弱"}]

    async def fake_detail(slug):
        return {"item": {"title": "杂鱼", "slug": slug, "content_html": "<h2>用法</h2>\n<p>嘲讽对方弱</p>"}}

    monkeypatch.setattr(service.client, "search", fake_search)
    monkeypatch.setattr(service.client, "detail", fake_detail)
    result = await service.prefetch("杂鱼")
    assert result.reason == "在线命中" and "嘲讽对方弱" in result.hint
    # 详情落盘，下次不用再调 detail
    assert service.store.local_detail("za-yu")["title"] == "杂鱼"


async def test_online_strict_rejects_unrelated(service, monkeypatch):
    service.apply_options(ServiceOptions(online_lookup=True))

    async def fake_search(query, limit=5):
        return [{"title": "完全无关", "slug": "x"}]

    async def no_detail(slug):
        return None

    monkeypatch.setattr(service.client, "search", fake_search)
    monkeypatch.setattr(service.client, "detail", no_detail)
    assert await service._search_online("我真的特别爱你", strict=True) is None
    assert (await service._search_online("我真的特别爱你"))["title"] == "完全无关"
