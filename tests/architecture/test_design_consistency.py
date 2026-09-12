"""Documentation consistency guards for the fifth review round (ADR-062..068).

The round's root cause was the same defect written two contradictory ways in two
documents (visible-scope union vs intersection, Console cancel path, grant
overwrite vs 409, delivery aggregation, proposal uniqueness). Consistency rules
that only live in prose regress silently, so the specific contradictory phrasings
are pinned here: a document may describe the retired behaviour only as history
(explicitly marked), never as the rule.

These are intentionally narrow, high-signal checks — they encode decisions, not
document style.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
DOCS = REPO_ROOT / "docs"

DESIGN_DOCS = sorted(DOCS.glob("*/*.md"))
FRONTEND_DOCS = sorted((DOCS / "03-前端设计").glob("**/*.md"))

# History/decision records are allowed to quote the retired wording.
HISTORY_PREFIXES = ("05-变更记录/", "04-追溯与验收/")


def _rule_docs(paths: list[Path]) -> list[Path]:
    return [
        path
        for path in paths
        if not any(str(path.relative_to(DOCS)).startswith(prefix) for prefix in HISTORY_PREFIXES)
    ]


def _hits(paths: list[Path], needle: str) -> list[str]:
    hits: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if needle in line:
                # Keep the full line: guards match on markers that may sit far to
                # the right of the hit, and truncation would silently disable them.
                hits.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    return hits


# Phrases that mention the retired wording only to retire it.
_HISTORICAL_MARKERS = (
    "不得写成交集",
    "笔误",
    "删除全部交集",
    "删除交集表述",
    "交集（INTERSECT）改为",
    "改为并集",
    "删除全部交集表述",
)


def test_builder_visible_scope_is_never_described_as_an_intersection() -> None:
    """ADR-052/ADR-067/D13: the visible scope is a UNION; 'intersection' is retired."""
    hits = [
        hit
        for hit in _hits(_rule_docs(DESIGN_DOCS + FRONTEND_DOCS), "交集")
        if not any(marker in hit for marker in _HISTORICAL_MARKERS)
    ]
    assert not hits, (
        "Builder 执行可见范围必须写并集（引用已废止措辞时请显式标注“不得写成交集”）:\n"
        + "\n".join(hits)
    )


def test_console_terminate_uses_the_cancel_endpoint() -> None:
    """ADR-065/D12: Console must call EXE-API-03 for running executions."""
    hits = _hits(_rule_docs(FRONTEND_DOCS), "Console 不调用")
    assert not hits, "Console 必须调用 EXE-API-03 取消运行态执行：\n" + "\n".join(hits)


def test_grant_editing_is_not_documented_as_a_collection_overwrite() -> None:
    """ADR-064/D15: grants are single-entity operations, not PUT collection overwrite."""
    hits = _hits(_rule_docs(FRONTEND_DOCS + DESIGN_DOCS), "全量集合覆盖")
    assert not hits, "授权编辑必须描述为单条操作（ADR-064）：\n" + "\n".join(hits)


def test_proposal_uniqueness_predicate_includes_superseded_at() -> None:
    """ADR-067/D8: every 'current proposal' predicate must exclude superseded rows."""
    needles = ("status='PENDING' AND is_deleted=false", 'status = \'PENDING\' AND is_deleted = false')
    hits = [hit for needle in needles for hit in _hits(_rule_docs(DESIGN_DOCS + FRONTEND_DOCS), needle)]
    assert not hits, (
        "提案唯一谓词必须包含 superseded_at IS NULL（ADR-067/D8）：\n" + "\n".join(hits)
    )


def test_implementation_auth_mode_is_not_placed_inside_config() -> None:
    """D3: auth_mode is a top-level implementation field, never a config key."""
    hits = _hits(_rule_docs(DESIGN_DOCS), "`config.auth_mode` 声明")
    assert not hits, "auth_mode 是 implementation 顶层字段：\n" + "\n".join(hits)


def test_decisions_register_lists_the_round_adrs() -> None:
    decisions = (DOCS / "00-总体设计" / "03-核心设计决策.md").read_text()
    missing = [
        adr
        for adr in ("ADR-062", "ADR-063", "ADR-064", "ADR-065", "ADR-066", "ADR-067", "ADR-068")
        if f"## {adr}" not in decisions
    ]
    assert not missing, f"第五轮 ADR 未登记：{missing}"


def test_capability_test_execution_source_is_documented_everywhere_it_is_decided() -> None:
    """ADR-063/D10: the three execution sources must agree across owner documents."""
    owners = {
        "docs/02-模块设计/05-Service与Execution/design-full.md": "CAPABILITY_TEST",
        "docs/02-模块设计/07-Capability-Runtime/design-full.md": "CAPABILITY_TEST",
        "docs/03-前端设计/04-能力管理/design-frontend.md": "CAPABILITY_TEST",
        "docs/03-前端设计/10-执行记录/design-frontend.md": "CAPABILITY_TEST",
    }
    missing = [path for path, token in owners.items() if token not in (REPO_ROOT / path).read_text()]
    assert not missing, f"以下 Owner 文档未同步能力测试执行源：{missing}"


@pytest.mark.parametrize(
    "path",
    [
        "docs/02-模块设计/05-Service与Execution/design-full.md",
        "docs/02-模块设计/06-Worker-Engine/design-full.md",
        "docs/02-模块设计/07-Capability-Runtime/design-full.md",
        "docs/02-模块设计/09-Auth与项目平台/design-full.md",
        "docs/02-模块设计/10-Channel-Gateway/design-full.md",
        "docs/02-模块设计/12-Project-Integration与Registry/design-full.md",
        "docs/02-模块设计/18-用户与Agent授权/design-full.md",
    ],
)
def test_touched_modules_record_the_round_in_the_change_log(path: str) -> None:
    text = (REPO_ROOT / path).read_text()
    assert "V1.14.1 第五轮 Review 裁决修复" in text, f"{path} 缺少本轮变更行"
