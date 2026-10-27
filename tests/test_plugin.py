"""插件层：组件声明、清单一致性、命令和 Hook 行为。宿主侧规则用上游 MaiBot 源码校验。"""

import json
import logging
import re
from types import SimpleNamespace

import pytest

from conftest import ROOT, load_plugin_module, load_upstream_module

plugin_mod = load_plugin_module()
MANIFEST = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))


class FakeSend:
    def __init__(self, image_ok=True):
        self.texts: list[str] = []
        self.images: list[str] = []
        self.image_ok = image_ok

    async def text(self, text, stream_id, **kwargs):
        self.texts.append(text)
        return True

    async def image(self, image_base64, stream_id, **kwargs):
        self.images.append(image_base64)
        return self.image_ok


class FakeConfig:
    def __init__(self, values):
        self.values = values

    async def get(self, key, default=None):
        return self.values.get(key, default)


def make_ctx(tmp_path, bot_config=None, image_ok=True):
    return SimpleNamespace(
        logger=logging.getLogger("memewiki.plugin.test"),
        paths=SimpleNamespace(data_dir=str(tmp_path / "data"), runtime_dir=str(tmp_path / "run")),
        config=FakeConfig(bot_config or {"bot.nickname": "麦麦", "bot.alias_names": ["小麦"]}),
        send=FakeSend(image_ok),
    )


@pytest.fixture
async def plugin(tmp_path):
    p = plugin_mod.create_plugin()
    p._set_context(make_ctx(tmp_path))
    p.set_plugin_config({})
    await p.on_load()
    yield p
    await p.on_unload()


def set_config(p, **sections):
    data = p.get_plugin_config_data()
    for section, values in sections.items():
        data.setdefault(section, {}).update(values)
    p.set_plugin_config(data)


# ---------------- 清单与组件 ----------------


def test_manifest_matches_code():
    assert MANIFEST["version"] == plugin_mod.VERSION
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*", MANIFEST["id"])
    assert MANIFEST["id"].split(".", 1)[1] == ROOT.name
    assert (ROOT / MANIFEST["changelog"]).exists()
    for dep in MANIFEST["dependencies"]:
        assert set(dep) == {"type", "name", "version_spec"}
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
    assert sorted(f"{d['name']}{d['version_spec']}" for d in MANIFEST["dependencies"]) == sorted(req)


def test_components_declared():
    p = plugin_mod.create_plugin()
    comps = {(c["component_type"] if "component_type" in c else c.get("type"), c["name"]) for c in p.get_components()}
    names = {name for _kind, name in comps}
    assert {"search_meme_wiki", "meme", "slang", "terms_list", "terms_save", "terms_delete",
            "memewiki_prefetch_observer", "memewiki_planner_inject"} <= names


def test_default_config():
    cfg = plugin_mod.create_plugin().get_default_config()
    assert cfg["plugin"]["enabled"] is True
    assert cfg["inject"]["prefetch_mode"] == "off"
    assert cfg["lookup"]["reply_mode"] == "image"
    assert cfg["sync"]["auto_sync_enable"] is False


def test_webui_json_validates_with_host_schema():
    schema = load_upstream_module("src/plugin_runtime", "webui_schema")
    extension = schema.load_webui_extension(str(ROOT))
    assert extension is not None
    page = extension.pages[0]
    assert page.id == "slang"
    # 页面引用的 API 都真实存在
    declared = {c["name"] for c in plugin_mod.create_plugin().get_components()}
    for binding in [*page.queries.values(), *page.actions.values()]:
        assert binding.api in declared


# ---------------- 生命周期 ----------------


async def test_on_load_uses_bot_names_as_wake_words(plugin):
    assert plugin.service.options.wake_words == ["麦麦", "小麦"]
    assert (await plugin.service.prefetch("麦麦 大水理论是什么梗")).query == "大水理论"


async def test_on_load_survives_config_error(tmp_path):
    class Boom:
        async def get(self, key, default=None):
            raise RuntimeError("no config capability")

    p = plugin_mod.create_plugin()
    ctx = make_ctx(tmp_path)
    ctx.config = Boom()
    p._set_context(ctx)
    p.set_plugin_config({})
    await p.on_load()
    assert p.service.options.wake_words == []
    await p.on_unload()


async def test_config_update_applies_options(plugin):
    set_config(plugin, lookup={"online_lookup": False}, inject={"wake_words": ["鲸鱼"]})
    await plugin.on_config_update("self", {}, "1.0.0")
    assert plugin.service.options.online_lookup is False
    assert plugin.service.options.wake_words[-1] == "鲸鱼"


# ---------------- Tool ----------------


async def test_tool_returns_content(plugin):
    result = await plugin.search_meme_wiki(query="大水理论")
    assert result["success"] is True and "大水理论" in result["content"]


# ---------------- /梗 ----------------


async def test_meme_command_sends_image(plugin):
    ok, note, intercept = await plugin.meme_cmd(stream_id="s1", matched_groups={"term": "尼罗河二圣"})
    assert ok and intercept
    send = plugin.ctx.send
    if send.images:
        assert note.startswith("已发送词条卡片")
    else:  # 没有中文字体时退回文字
        assert "尼罗河二圣" in send.texts[-1]


async def test_meme_command_falls_back_to_text(tmp_path):
    p = plugin_mod.create_plugin()
    p._set_context(make_ctx(tmp_path, image_ok=False))
    p.set_plugin_config({})
    await p.on_load()
    await p.meme_cmd(stream_id="s1", matched_groups={"term": "尼罗河二圣"})
    assert "尼罗河二圣" in p.ctx.send.texts[-1]
    await p.on_unload()


async def test_meme_command_usage_and_miss(plugin):
    set_config(plugin, lookup={"online_lookup": False})
    await plugin.on_config_update("self", {}, "1.0.0")
    await plugin.meme_cmd(stream_id="s1", matched_groups={})
    assert plugin.ctx.send.texts[-1].startswith("用法")
    await plugin.meme_cmd(stream_id="s1", matched_groups={"term": "完全不存在的词条xyz"})
    assert "没有收录" in plugin.ctx.send.texts[-1]


def test_command_patterns():
    comps = {c["name"]: c for c in plugin_mod.create_plugin().get_components()}

    def pattern(name):
        meta = comps[name].get("metadata", comps[name])
        return re.compile(meta["command_pattern"] if "command_pattern" in meta else meta["pattern"])

    m = pattern("meme").search("/梗 大水理论")
    assert m and m.group("term") == "大水理论"
    assert pattern("meme").search("/梗").group("term") is None
    assert pattern("meme").search("/梗百科") is None
    m = pattern("slang").search("/黑话 狗东 京东")
    assert m.group("rest") == "狗东 京东"


# ---------------- /黑话 ----------------


async def test_slang_add_list_delete(plugin):
    msg = {"message_info": {"user_info": {"user_nickname": "小明"}}}
    await plugin.slang_cmd(stream_id="s", platform="qq", user_id="1", matched_groups={"rest": "狗东 京东"}, message=msg)
    assert "已记下「狗东」" in plugin.ctx.send.texts[-1] and "小明" in plugin.ctx.send.texts[-1]
    assert "京东" in await plugin.service.lookup("今天狗东好慢")

    await plugin.slang_cmd(stream_id="s", matched_groups={"rest": "列表"})
    assert "共 1 条" in plugin.ctx.send.texts[-1]

    await plugin.slang_cmd(stream_id="s", platform="qq", user_id="1", matched_groups={"rest": "删除 狗东"})
    assert "只有管理员" in plugin.ctx.send.texts[-1]

    set_config(plugin, lookup={"admin_users": ["QQ:1"]})
    await plugin.slang_cmd(stream_id="s", platform="qq", user_id="1", matched_groups={"rest": "删除 狗东"})
    assert plugin.ctx.send.texts[-1] == "已删除「狗东」。"
    assert "狗东" not in plugin.service.terms.items


async def test_slang_local_operator_can_delete(plugin):
    await plugin.slang_cmd(stream_id="s", user_id="1", matched_groups={"rest": "狗东 京东"})
    await plugin.slang_cmd(stream_id="s", is_local_operator=True, matched_groups={"rest": "删除 狗东"})
    assert plugin.ctx.send.texts[-1] == "已删除「狗东」。"


async def test_slang_validation(plugin):
    await plugin.slang_cmd(stream_id="s", matched_groups={"rest": "狗东"})
    assert "还差含义" in plugin.ctx.send.texts[-1]
    await plugin.slang_cmd(stream_id="s", matched_groups={"rest": "狗 京东"})
    assert plugin.service.terms.items == {}


# ---------------- WebUI API ----------------


async def test_webui_api(plugin):
    assert await plugin.api_terms_list() == {"count": 0, "items": []}
    assert (await plugin.api_terms_save(name="狗东", meaning="京东"))["existed"] is False
    listed = await plugin.api_terms_list()
    assert listed["count"] == 1 and listed["items"][0]["author"]
    with pytest.raises(ValueError):
        await plugin.api_terms_save(name="狗", meaning="京东")
    assert (await plugin.api_terms_delete(name="狗东"))["count"] == 0
    with pytest.raises(ValueError):
        await plugin.api_terms_delete(name="狗东")


# ---------------- 预查注入 ----------------


def host_items(text="大水理论是什么梗"):
    return [{
        "item_type": "UserMessageItem",
        "meta": {"item_id": "u1", "logical_turn_id": None, "timestamp": "2026-10-27T12:00:00"},
        "parts": [{"type": "text", "text": text}],
    }]


def host_kwargs(session_id="sess"):
    return {
        "items": host_items(),
        "item_schema_version": 1,
        "tool_definitions": [{"type": "function", "function": {"name": "search_meme_wiki"}}],
        "selected_history_count": 1,
        "built_message_count": 1,
        "selection_reason": "test",
        "session_id": session_id,
    }


def host_deserialize(kwargs):
    """复刻宿主 deserialize_prompt_items + 锚点校验（chat_loop_service 1269 行附近）。"""
    item_mod = load_upstream_module("src/llm_models/payload_content", "context_item")
    snapshot = load_upstream_module("src/llm_models/payload_content", "context_snapshot")
    protocol = load_upstream_module("src/llm_models/payload_content", "context_protocol")
    if kwargs.get("item_schema_version") != item_mod.CONTEXT_ITEM_SCHEMA_VERSION:
        raise ValueError(f"item_schema_version 不受支持: {kwargs.get('item_schema_version')!r}")
    items = [snapshot.deserialize_context_item_snapshot(raw) for raw in kwargs["items"]]
    protocol.validate_context_items(items, protocol.ContextProtocolMode.REQUEST_CONTEXT)
    return items


def inbound(text, session_id="sess", **extra):
    return {"session_id": session_id, "processed_plain_text": text, "is_command": False, "is_notify": False, **extra}


async def test_prefetch_off_by_default(plugin):
    await plugin.observe_inbound(message=inbound("大水理论是什么梗"))
    assert plugin._pending == {}
    assert await plugin.inject_planner(**host_kwargs()) == {"action": "continue"}


async def test_prefetch_hook_injects_valid_item(plugin):
    set_config(plugin, inject={"prefetch_mode": "hook"})
    await plugin.observe_inbound(message=inbound("麦麦，大水理论是什么梗"))
    assert plugin._pending["sess"][1] == "大水理论"

    original = host_kwargs()
    result = await plugin.inject_planner(**original)
    new_kwargs = result["modified_kwargs"]
    # 宿主整体替换 kwargs，其它参数必须原样带回
    for key in ("item_schema_version", "tool_definitions", "session_id", "selection_reason"):
        assert new_kwargs[key] == original[key]
    assert len(new_kwargs["items"]) == 2
    items = host_deserialize(new_kwargs)
    assert type(items[-1]).__name__ == "UserMessageItem"
    assert "大水理论" in new_kwargs["items"][-1]["parts"][0]["text"]

    # 别的会话不受影响
    assert await plugin.inject_planner(**host_kwargs("other")) == {"action": "continue"}


async def test_prefetch_skips_commands_and_clears_stale(plugin):
    set_config(plugin, inject={"prefetch_mode": "hook"})
    await plugin.observe_inbound(message=inbound("/梗 大水理论", is_command=True))
    assert plugin._pending == {}
    await plugin.observe_inbound(message=inbound("大水理论是什么梗"))
    assert "sess" in plugin._pending
    await plugin.observe_inbound(message=inbound("我今天去吃了顿很好吃的火锅，你觉得呢"))
    assert "sess" not in plugin._pending      # 新消息不像梗，旧预查作废


async def test_prefetch_expired(plugin, monkeypatch):
    set_config(plugin, inject={"prefetch_mode": "hook"})
    await plugin.observe_inbound(message=inbound("大水理论是什么梗"))
    expire = plugin._pending["sess"][0]
    monkeypatch.setattr(plugin_mod.time, "time", lambda: expire + 1)
    assert await plugin.inject_planner(**host_kwargs()) == {"action": "continue"}
    assert plugin._pending == {}


async def test_guidance_toggle(plugin):
    set_config(plugin, inject={"prefetch_mode": "hook", "inject_guidance": False})
    await plugin.observe_inbound(message=inbound("大水理论是什么梗"))
    text = (await plugin.inject_planner(**host_kwargs()))["modified_kwargs"]["items"][-1]["parts"][0]["text"]
    assert plugin_mod.prompts.DEFAULT_GUIDANCE not in text


def test_bad_item_would_be_rejected_by_host():
    """反证：确认复刻的宿主校验真的会拒绝坏数据，而不是什么都放过。"""
    with pytest.raises(Exception):
        host_deserialize({**host_kwargs(), "items": [*host_items(), *host_items()]})   # 重复 item_id
    with pytest.raises(ValueError):
        host_deserialize({"items": host_items()})                                      # 缺版本号


async def test_meme_command_without_cjk_font(plugin, monkeypatch):
    def no_font(data):
        raise plugin_mod.render_card.__globals__["NoCJKFontError"]("没有中文字体")

    monkeypatch.setattr(plugin_mod, "render_card", no_font)
    ok, _note, _ = await plugin.meme_cmd(stream_id="s1", matched_groups={"term": "尼罗河二圣"})
    assert ok and plugin.ctx.send.images == []
    assert "尼罗河二圣" in plugin.ctx.send.texts[-1]
