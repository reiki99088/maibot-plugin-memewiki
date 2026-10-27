"""用宿主 Runner 的 PluginLoader 原代码加载插件：清单校验 → 依赖解析 → 合成包导入 → create_plugin → 契约检查。

这一步不启动 MaiBot 主程序，只验证「插件目录放进 plugins/ 后 Runner 能不能把它加载成插件实例」。
"""

import logging
import shutil
from types import SimpleNamespace

from conftest import ROOT

COPY_IGNORE = shutil.ignore_patterns(".venv", ".tmp", ".git", ".pytest_cache", "__pycache__", "tests", "config.toml")


def install_plugin(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(ROOT, plugins / ROOT.name, ignore=COPY_IGNORE)
    return plugins


async def test_runner_loads_plugin(host_runner, tmp_path):
    loader_mod = host_runner("plugin_loader")
    plugins = install_plugin(tmp_path)

    loader = loader_mod.PluginLoader(host_version="1.3.15")
    loaded = loader.discover_and_load([str(plugins)])

    assert loader.failed_plugins == {}
    assert [meta.plugin_id for meta in loaded] == ["reiki99088.maibot-plugin-memewiki"]
    instance = loaded[0].instance
    assert type(instance).__name__ == "MemeWikiPlugin"

    # Runner 随后注入上下文与配置、调用 on_load；这里用最小假上下文走一遍
    ctx = SimpleNamespace(
        logger=logging.getLogger("memewiki.runner.test"),
        paths=SimpleNamespace(data_dir=str(tmp_path / "data"), runtime_dir=str(tmp_path / "run")),
        config=SimpleNamespace(get=_async_return(None)),
    )
    instance._set_context(ctx)
    instance.set_plugin_config(instance.get_default_config())
    await instance.on_load()
    try:
        result = await instance.search_meme_wiki(query="尼罗河二圣")
        assert result["success"] and "尼罗河二圣" in result["content"]
        # 读的是装到 plugins/ 里的那份内置库，而不是仓库里的
        assert str(plugins) in str(instance.service.store.readable_db_path())
    finally:
        await instance.on_unload()


def test_runner_rejects_broken_contract(host_runner, tmp_path):
    """反证：去掉必需的 on_config_update 后宿主应拒绝加载，确认上一个测试不是空跑。"""
    loader_mod = host_runner("plugin_loader")
    plugins = install_plugin(tmp_path)
    entry = plugins / ROOT.name / "plugin.py"
    entry.write_text(entry.read_text(encoding="utf-8") + "\ndel MemeWikiPlugin.on_config_update\n", encoding="utf-8")
    loader = loader_mod.PluginLoader(host_version="1.3.15")
    assert loader.discover_and_load([str(plugins)]) == []
    assert "on_config_update" in loader.failed_plugins["reiki99088.maibot-plugin-memewiki"]


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


def test_no_legacy_data_dir_in_plugin():
    """插件目录下有 data/ 时，宿主 Runner 每次加载都会警告「旧式数据目录，建议迁移」。"""
    assert not (ROOT / "data").exists()
