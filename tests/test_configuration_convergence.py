"""[E-09] 配置收敛机检：`.env.example` 运维契约与重复默认源消除（无服务、静态断言）。

真实边界：真实 `.env.example`、真实 `SharedSettings` 字段集、真实源码 AST——不 mock 任何一层。
本文件是 E-09 的契约命令（`uv run pytest -q tests/test_configuration_convergence.py`），
逐条对应 TASK-011 的收敛项：环境项收口、`.env.example` 补全、工具结果三常量单一来源、
历史预算单一来源、产物路径单一来源、默认租户单一来源、口令下界单一来源。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from muad_agent_runtime.application.context_builder import BudgetPolicy
from muad_agent_runtime.application.run_service import RunService
from muad_common import SharedSettings
from muad_console_platform.application.dto import PasswordChangeRequest
from muad_contracts.platform_settings import CompactionSettings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

#: 改由业务设置接管、必须从启动 settings 与 `.env.example` 移除的 12 个环境字段
#: （design §4.4 环境项删除清单；谁切换谁摘除）。
MIGRATED_ENV_FIELDS = (
    "context_settings_cache_ttl_sec",
    "im_progress_interval_sec",
    "artifact_retention_days",
    "mcp_max_tools_per_server",
    "task_default_deadline_hours",
    "task_max_attempts",
    "batch_max_concurrency",
    "misfire_grace_sec",
    "delivery_max_attempts",
    "delivery_backoff_base_sec",
    "default_locale",
    "default_timezone",
)

#: 仍留在环境的**服务资源预算上限**（design §4.4 明确保留）。
RETAINED_ENV_FIELDS = ("batch_platform_limit", "im_progress_updates_per_second")

#: 只应存在于 contracts schema、不得再出现的三个重复模块常量。
REMOVED_TOOL_RESULT_CONSTANTS = (
    "TOOL_RESULT_ARTIFACT_BYTES",
    "PREVIEW_HEAD_BYTES",
    "PREVIEW_TAIL_BYTES",
)

#: schema 绝对下界（`_validate_auth` 里 `auth.min_password_length` 的下限）。
MIN_PASSWORD_LENGTH_FLOOR = 8


def _env_example_keys() -> set[str]:
    keys: set[str] = set()
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _python_sources() -> list[Path]:
    return [
        path
        for base in ("apps", "packages")
        for path in (ROOT / base).rglob("*.py")
    ]


def _module_constants() -> set[str]:
    names: set[str] = set()
    for path in _python_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_env_example_is_complete_operations_contract() -> None:
    """`.env.example` 的键集 == `SharedSettings` 的全部环境类字段（键名一致，无遗漏/多余）。"""
    expected = {name.upper() for name in SharedSettings.model_fields}
    assert _env_example_keys() == expected, (
        f"missing={sorted(expected - _env_example_keys())} "
        f"extra={sorted(_env_example_keys() - expected)}"
    )


def test_migrated_environment_keys_are_gone() -> None:
    """12 个业务化的键既不在启动 settings，也不在示例契约里。"""
    fields = set(SharedSettings.model_fields)
    keys = _env_example_keys()
    for name in MIGRATED_ENV_FIELDS:
        assert name not in fields, f"{name} 仍留在 SharedSettings"
        assert name.upper() not in keys, f"{name.upper()} 仍留在 .env.example"


def test_retained_resource_budgets_stay_in_environment() -> None:
    """`batch_platform_limit` / `im_progress_updates_per_second` 是服务资源上限，仍留环境。"""
    fields = set(SharedSettings.model_fields)
    for name in RETAINED_ENV_FIELDS:
        assert name in fields, f"{name} 应保留在 SharedSettings"
        assert name.upper() in _env_example_keys(), f"{name.upper()} 应保留在 .env.example"


def test_tool_result_defaults_have_single_source() -> None:
    """三个工具结果常量不得再作为模块常量存在——schema（`ToolResultSettings`）是唯一来源。"""
    present = _module_constants() & set(REMOVED_TOOL_RESULT_CONSTANTS)
    assert not present, f"重复默认源仍在源码里：{sorted(present)}"


def _class_field_default(path: Path, class_name: str, field_name: str) -> ast.expr | None:
    """取某类某字段的默认值 AST 节点（无默认返回 None）。"""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id == field_name
                ):
                    return item.value
    return None


def test_budget_policy_default_derives_from_compaction_schema() -> None:
    """`BudgetPolicy.max_messages` 与 schema 的 `history_budget_messages` 同源（同一冻结值）。"""
    assert BudgetPolicy().max_messages == CompactionSettings().history_budget_messages
    default = _class_field_default(
        ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py",
        "BudgetPolicy",
        "max_messages",
    )
    assert not isinstance(default, ast.Constant), (
        "BudgetPolicy.max_messages 又写回硬编码默认——必须派生自 schema"
    )


def test_run_service_requires_settings_client() -> None:
    """`RunService` 的 settings_client 是必填参数，不回落隐式 Null。"""
    params = inspect.signature(RunService.__init__).parameters
    assert "settings_client" in params
    assert params["settings_client"].default is inspect.Parameter.empty


def test_no_bare_artifact_path_getenv_defaults() -> None:
    """裸 `getenv` 的第二套产物路径默认（`/mnt/muad-artifacts`、`/var/cache/muad/skills`）已删。"""
    stale = ("/mnt/muad-artifacts", "/var/cache/muad/skills")
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for literal in stale:
            assert literal not in text, f"{path} 仍带第二套产物路径默认 {literal}"


def test_password_dto_floor_matches_schema_lower_bound() -> None:
    """口令 DTO 只守 schema 绝对下界；更严的策略下界由服务层按平台设置执行。"""
    accepted = PasswordChangeRequest(
        current_password="x", new_password="a" * MIN_PASSWORD_LENGTH_FLOOR
    )
    assert accepted.new_password == "a" * MIN_PASSWORD_LENGTH_FLOOR
    try:
        PasswordChangeRequest(current_password="x", new_password="a" * (MIN_PASSWORD_LENGTH_FLOOR - 1))
    except ValidationError:
        return
    raise AssertionError("DTO 未拒绝低于 schema 下界的口令")


def test_cli_default_tenant_reads_shared_settings() -> None:
    """CLI 的 `DEFAULT_TENANT` 与 `SharedSettings.default_tenant_id` 同源。"""
    from muad_console_platform.cli import DEFAULT_TENANT

    assert DEFAULT_TENANT == SharedSettings().default_tenant_id
    cli_source = ROOT / "apps/console-platform/backend/src/muad_console_platform/cli.py"
    for node in ast.parse(cli_source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "DEFAULT_TENANT" for target in node.targets
        ):
            assert not isinstance(node.value, ast.Constant), (
                "CLI DEFAULT_TENANT 又写回字面量——必须读 SharedSettings.default_tenant_id"
            )
            return
    raise AssertionError("CLI 未定义 DEFAULT_TENANT")
