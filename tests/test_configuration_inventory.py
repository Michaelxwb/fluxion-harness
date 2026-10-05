"""Keep the configuration audit tied to real source declarations, without services."""

from __future__ import annotations

import ast
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/configuration-inventory.csv"


def _rows() -> list[dict[str, str]]:
    with CATALOG.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_configuration_inventory_covers_real_python_constants() -> None:
    rows = _rows()
    recorded = {
        (r["path"], int(r["line"]), r["symbol"], r["default"]) for r in rows if r["kind"] == "python-constant"
    }
    actual: set[tuple[str, int, str, str]] = set()
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.strip("_").isupper():
                        actual.add(
                            (str(path.relative_to(ROOT)), node.lineno, target.id, ast.unparse(node.value))
                        )
    assert actual, "The audit must contain real source declarations"
    assert recorded == actual, f"Missing/stale constants: {actual ^ recorded}"


def test_configuration_inventory_covers_shared_settings_and_policy_fields() -> None:
    rows = _rows()
    recorded = {
        (r["path"], int(r["line"]), r["default"])
        for r in rows
        if r["kind"] in {"shared-setting", "policy-field"}
    }
    actual: set[tuple[str, int, str]] = set()
    classes = {
        "SharedSettings",
        "AgentPolicy",
        "BudgetPolicy",
        "EgressPolicy",
        "SnipSettings",
        "ToolResultSettings",
        "MicroSettings",
        "SummarySettings",
        "MemorySettings",
        "CompactionSettings",
    }
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            for cls in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(cls, ast.ClassDef) or cls.name not in classes:
                    continue
                for node in cls.body:
                    if (
                        isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and node.value is not None
                        and node.target.id != "model_config"
                    ):
                        actual.add((str(path.relative_to(ROOT)), node.lineno, ast.unparse(node.value)))
    assert recorded == actual, f"Missing/stale settings: {actual ^ recorded}"


def test_configuration_inventory_has_valid_locations_and_classification() -> None:
    rows = _rows()
    keys = {(r["path"], r["line"], r["symbol"], r["kind"]) for r in rows}
    assert len(keys) == len(rows), "Duplicate audit declaration"
    for row in rows:
        path = ROOT / row["path"]
        assert path.is_file(), row
        assert 0 < int(row["line"]) <= len(path.read_text(encoding="utf-8").splitlines()), row
        assert row["category"] in {"business", "business-resource", "environment", "code"}, row
        assert row["reason"].strip(), row
        assert ".env" not in path.name, "Do not inventory actual secret values"
    shared = {r["symbol"]: r["category"] for r in rows if r["kind"] == "shared-setting"}
    assert shared["database_url"] == "environment"
    chunks = [r for r in rows if r["symbol"] in {"UPLOAD_CHUNK_BYTES", "MAX_UPLOAD_CHUNKS"}]
    assert {r["symbol"] for r in chunks} == {"UPLOAD_CHUNK_BYTES", "MAX_UPLOAD_CHUNKS"}
    assert all(r["category"] == "code" for r in chunks)


def test_configuration_inventory_classification_matches_migration_facts() -> None:
    """分类必须反映迁移后的事实（design §2.3.2 九分组 / §2.4 四类归属），钉住稳定主张。"""
    rows = _rows()

    # 1) 平台设置 schema（contracts）的默认值来源 -> business。
    schema = [r for r in rows if r["path"].endswith("contracts/src/muad_contracts/platform_settings.py")]
    schema_fields = [r for r in schema if r["kind"] == "policy-field"]
    assert schema_fields, "平台设置 schema 的默认值必须进入盘点"
    assert all(r["category"] == "business" for r in schema_fields), schema_fields

    # 2) Agent 执行预算默认（平台设置 agent 分组来源）-> business。
    agent = [
        r for r in rows
        if r["path"].endswith("agent-core/src/muad_agent_core/agent/runner.py")
        and r["kind"] == "policy-field"
    ]
    assert {r["symbol"] for r in agent} == {"max_turns", "max_tool_calls", "deadline_ms", "max_model_retries"}
    assert all(r["category"] == "business" for r in agent), agent
    agent_defaults = [
        r for r in rows
        if r["path"].endswith("agent-core/src/muad_agent_core/model/budget.py")
        and r["symbol"].startswith("DEFAULT_")
    ]
    assert {r["symbol"] for r in agent_defaults} == {
        "DEFAULT_MAX_TURNS", "DEFAULT_MAX_TOOL_CALLS", "DEFAULT_DEADLINE_MS", "DEFAULT_MAX_MODEL_RETRIES"
    }
    assert all(r["category"] == "business" for r in agent_defaults), agent_defaults

    # 3) 仍在 SharedSettings 的环境类字段 -> environment；迁移掉的业务键不再出现。
    shared = [r for r in rows if r["kind"] == "shared-setting"]
    assert all(r["path"] == "packages/common/src/muad_common/settings.py" for r in shared)
    assert all(r["category"] == "environment" for r in shared), shared
    migrated = {"default_locale", "default_timezone", "artifact_retention_days",
                "mcp_max_tools_per_server", "im_progress_interval_sec"}
    assert migrated.isdisjoint({r["symbol"] for r in shared}), "已迁移业务键不得留在启动 settings"

    # 4) 保守默认（settings=environment）不得回潮：没有任何 policy-field 仍是 environment。
    stale = [r for r in rows if r["kind"] == "policy-field" and r["category"] == "environment"]
    assert not stale, stale

    # 5) 协议 / 第三方硬上限 / schema 不变量 -> code。
    code_symbols = {r["symbol"] for r in rows if r["category"] == "code"}
    assert {"MIN_PASSWORD_LENGTH_FLOOR", "RECALL_MAX_LIMIT"} <= code_symbols

    # 6) 留在各自服务的实现常量（本期不迁入平台设置，design §2.4 技术债③）-> code。
    local = [r for r in rows if r["symbol"] in {
        "ZIP_BYTES_LIMIT", "UNPACKED_BYTES_LIMIT", "ENTRY_LIMIT",
        "MAX_ATTACHMENT_BYTES", "MAX_ATTACHMENTS_PER_MESSAGE",
    }]
    assert len(local) == 5
    assert all(r["category"] == "code" for r in local), local

    # 7) 资源级配置 -> business-resource。
    resource = [r for r in rows if r["kind"] == "resource-field"]
    assert resource and all(r["category"] == "business-resource" for r in resource), resource

