from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import contextlib
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUNDLED_DB = ROOT / "assets" / "memes.db"
# 上游 MaiBot 只读快照，用来按宿主真实规则校验 webui.json 和 Hook 返回的 Context Item
UPSTREAM_MAIBOT = Path(
    os.environ.get("MAIBOT_SRC", ROOT.parent.parent / "Work" / "memewiki-maibot-port" / "upstream" / "MaiBot")
)

sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """测试一律不联网：要测在线路径就 monkeypatch client.search / client.detail。"""
    from memewiki.online import CnmemeClient

    async def blocked(self, url, params=None):
        raise AssertionError(f"测试里发起了真实网络请求: {url} {params}")

    monkeypatch.setattr(CnmemeClient, "get", blocked)


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("memewiki.test")


@pytest.fixture
def service(tmp_path, logger):
    from memewiki.service import MemeWikiService, ServiceOptions

    svc = MemeWikiService(
        data_dir=tmp_path / "data",
        bundled_db=BUNDLED_DB,
        logger=logger,
        user_agent="memewiki-test",
        options=ServiceOptions(online_lookup=False),
    )
    svc.load()
    return svc


def load_plugin_module():
    """按 MaiBot Runner 的方式加载 plugin.py：插件目录是合成包，目录本身不进 sys.path。"""
    name = "reiki99088_maibot_plugin_memewiki"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "plugin.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_upstream_module(relative_dir: str, module: str):
    """不执行宿主包的 __init__，只导入纯数据模块（它们只依赖标准库 / pydantic）。"""
    pkg_dir = UPSTREAM_MAIBOT / relative_dir
    if not pkg_dir.is_dir():
        pytest.skip(f"没有上游 MaiBot 快照: {pkg_dir}")
    pkg_name = "_upstream_" + relative_dir.replace("/", "_")
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(pkg_dir)]
        sys.modules[pkg_name] = pkg
    return importlib.import_module(f"{pkg_name}.{module}")


RUNNER_DIR = UPSTREAM_MAIBOT / "src" / "plugin_runtime" / "runner"


@pytest.fixture
def host_runner(monkeypatch, tmp_path):
    """宿主 Runner 侧的 manifest_validator / plugin_loader 原代码，只把它们引用的 src.* 杂项打桩。

    返回一个 load(name) 函数，按 src.plugin_runtime.runner.<name> 导入宿主模块。
    """
    if not (RUNNER_DIR / "plugin_loader.py").exists():
        pytest.skip("没有上游 MaiBot 快照")
    pytest.importorskip("packaging")

    def stub(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    stub("src", __path__=[])
    stub("src.common", __path__=[])
    stub("src.common.paths", get_instance_root=lambda: tmp_path, project_metadata_path=lambda root: root / "pyproject.toml")
    stub("src.common.logger", get_logger=logging.getLogger)
    stub("src.common.startup_diagnostics", diagnostic_stage=lambda name: contextlib.nullcontext())
    stub("src.plugin_runtime", __path__=[], detect_host_application_version=lambda project_root=None: "1.3.15")
    stub("src.plugin_runtime.local_sdk", read_local_sdk_version=lambda environ=None, project_root=None: None)
    stub("src.plugin_runtime.runner", __path__=[str(RUNNER_DIR)])

    def load(name):
        full = f"src.plugin_runtime.runner.{name}"
        if full in sys.modules:
            return sys.modules[full]
        spec = importlib.util.spec_from_file_location(full, RUNNER_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full, module)
        spec.loader.exec_module(module)
        return module

    load("plugin_enabled")
    load("manifest_validator")
    return load
