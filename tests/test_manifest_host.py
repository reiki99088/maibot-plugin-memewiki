"""用宿主 MaiBot 自己的 ManifestValidator 校验 _manifest.json。

manifest_validator 只从 src.* 引用几个读路径 / 版本号 / 日志的函数，这里打桩替换，
清单模型和兼容性检查跑的都是宿主原代码。
"""

import json

import pytest

from conftest import ROOT


@pytest.fixture
def validator_mod(host_runner):
    return host_runner("manifest_validator")


def manifest():
    return json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))


def test_manifest_passes_host_validator(validator_mod, tmp_path):
    validator = validator_mod.ManifestValidator(
        host_version="1.3.15", sdk_version="2.16.0", project_root=tmp_path, log_errors=False
    )
    parsed = validator.parse_manifest(manifest())
    assert parsed is not None, validator.errors
    assert parsed.id == "reiki99088.maibot-plugin-memewiki"


def test_host_out_of_range_is_rejected(validator_mod, tmp_path):
    validator = validator_mod.ManifestValidator(
        host_version="1.3.12", sdk_version="2.16.0", project_root=tmp_path, log_errors=False
    )
    assert validator.parse_manifest(manifest()) is None


def test_sdk_out_of_range_only_warns(validator_mod, tmp_path):
    """宿主对 SDK 版本越界只告警放行（manifest_validator 里的「已放行」），不拒绝加载。"""
    validator = validator_mod.ManifestValidator(
        host_version="1.3.15", sdk_version="2.15.0", project_root=tmp_path, log_errors=False,
        log_compat_warnings=False,
    )
    assert validator.parse_manifest(manifest()) is not None
    assert any("SDK" in w for w in validator.warnings)


def test_old_version_field_is_rejected(validator_mod, tmp_path):
    """反证：交接文档里写的 `version` 字段宿主不认，必须是 `version_spec`。"""
    data = manifest()
    data["dependencies"] = [{"type": "python_package", "name": "aiohttp", "version": ">=3.9"}]
    validator = validator_mod.ManifestValidator(
        host_version="1.3.15", sdk_version="2.16.0", project_root=tmp_path, log_errors=False
    )
    assert validator.parse_manifest(data) is None
