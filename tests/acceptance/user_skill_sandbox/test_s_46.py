"""S-46：本需求新增的 Skill/Workspace 代码仍在渠道中立性扫描范围内。

真实边界：不 mock——直接运行仓库里已有的架构扫描
`tests/architecture/test_channel_neutrality.py` 三条断言，并对本需求改动过的核心域
文件逐一确认被扫描覆盖、没有拿到任何 `ALLOWED_SURFACES` 渠道例外。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.architecture import test_channel_neutrality as neutrality

from .non_interference_support import REQUIREMENT_CORE_FILES, ROOT

pytestmark = pytest.mark.integration

#: 需求代码所在部署单元：核心域（runtime/worker）+ 被核心域直接装配的 agent-core。
SCANNED_UNITS = neutrality.CORE_DOMAIN + ("packages/agent-core",)


def _core_paths() -> list[Path]:
    paths: list[Path] = []
    for relative in REQUIREMENT_CORE_FILES:
        path = ROOT / relative
        assert path.is_file(), f"requirement core file is missing: {relative}"
        assert relative.startswith(SCANNED_UNITS), (
            f"new skill/workspace code must live in the scanned core units, not {relative}"
        )
        paths.append(path)
    return paths


def test_s46_core_domain_carries_no_channel_vocabulary() -> None:
    """A-S-46-1：核心域（含本需求新增代码）零渠道专有字样。"""
    neutrality.test_b09_core_domain_carries_no_channel_vocabulary()
    offenders = {
        path.relative_to(ROOT).as_posix(): neutrality._token_hits(path)
        for path in _core_paths()
        if neutrality._token_hits(path)
    }
    assert offenders == {}, f"skill/workspace code leaks channel vocabulary: {offenders}"


def test_s46_core_domain_never_fabricates_a_channel_value() -> None:
    """A-S-46-2：核心域不向 `ResolveDefinitionRequest.channel` 传字面量。"""
    neutrality.test_b09_core_domain_never_fabricates_a_channel_value()
    fabricated = {
        path.relative_to(ROOT).as_posix(): neutrality._fabricated_channels(path)
        for path in _core_paths()
        if neutrality._fabricated_channels(path)
    }
    assert fabricated == {}, f"skill/workspace code fabricates channel values: {fabricated}"


def test_s46_channel_vocabulary_only_lives_on_allowed_surfaces() -> None:
    """A-S-46-3：渠道字样只出现在既有允许面；新 Skill/Workspace 代码不得新增例外。"""
    neutrality.test_b09_channel_vocabulary_only_lives_on_the_allowed_surfaces()
    allowed = [
        relative for relative in REQUIREMENT_CORE_FILES if neutrality._allowed(relative)
    ]
    assert allowed == [], f"skill/workspace code must not extend channel surfaces: {allowed}"
