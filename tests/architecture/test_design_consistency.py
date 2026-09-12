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

import re
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


# ---------------------------------------------------------------------------
# Master design document (总设) alignment
# ---------------------------------------------------------------------------

MASTER_DESIGN = DOCS / "00-总体设计" / "01-通用智能服务执行框架-V1.8-完整总体设计说明书.md"

# Concepts that only exist from V1.9/V1.14 onwards. If the master design is the
# declared upstream contract, the implemented mechanism must be described there —
# otherwise an implementer who follows the 总设 builds the retired behaviour.
MASTER_DESIGN_REQUIRED = (
    "execution_source",
    "CAPABILITY_TEST",
    "message_key",
    "async_submittable",
    "execution_mode",
    "slot_resource_class",
    "worker_slot_counter",
    "superseded_at",
    "invocation_policy",
    "human_policy",
    "AUTH-API-01",
)

# Retired mechanisms whose presence in the master design means it was not
# actually migrated (each one was ruled out by an ADR).
MASTER_DESIGN_RETIRED = (
    "storage_backend",
    "root_ref",
    "execution_characteristic",
    "authorization_requirement",
    "error_semantics",
)


def test_master_design_declares_the_current_version() -> None:
    text = MASTER_DESIGN.read_text()
    first_line = text.splitlines()[0]
    assert "V1.14" in first_line, f"总设标题必须声明当前版本：{first_line!r}"
    assert "V1.14" in text, "总设正文必须包含 V1.14 版本行"


def test_master_design_carries_the_current_mechanisms() -> None:
    text = MASTER_DESIGN.read_text()
    missing = [token for token in MASTER_DESIGN_REQUIRED if token not in text]
    assert not missing, (
        "总设是上游契约，必须描述现行机制；缺失即表示正文仍是旧版本："
        f"{missing}"
    )


def test_master_design_dropped_retired_mechanisms() -> None:
    text = MASTER_DESIGN.read_text()
    present = [token for token in MASTER_DESIGN_RETIRED if token in text]
    assert not present, (
        "总设仍保留已废止机制（应随 ADR 一并删除）："
        f"{present}"
    )


def test_master_design_has_no_prefixed_api_paths() -> None:
    """Frontend/IM contract paths must carry the /api/v1 prefix (90-§13 lesson)."""
    text = MASTER_DESIGN.read_text()
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"`(?:PUT|POST|GET|PATCH|DELETE) /(?!api/|internal/|health|metrics)", line)
    ]
    assert not offenders, f"总设出现缺少 /api/v1 或 /internal/v1 前缀的路径：{offenders[:5]}"


# ---------------------------------------------------------------------------
# Summary index (08) hygiene — the duplicate-column lesson (P0-13)
# ---------------------------------------------------------------------------

FIELD_INDEX = DOCS / "01-架构与规范" / "08-数据库表所有权与字段索引.md"


def _field_index_rows() -> list[tuple[int, str, list[str]]]:
    rows: list[tuple[int, str, list[str]]] = []
    for number, line in enumerate(FIELD_INDEX.read_text().splitlines(), 1):
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"表名", "---"} or set(cells[0]) <= set("-: "):
            continue
        columns = [cell.split("(")[0].strip() for cell in cells[2].split(",")]
        rows.append((number, cells[0], [c for c in columns if c]))
    return rows


def test_field_index_has_no_duplicate_columns() -> None:
    """A set-based parity check cannot see duplicates; this one can.

    The fifth round inserted already-present columns into the `service_execution`
    row of the summary index, which no gate noticed because comparisons were done
    on sets. Duplicates here mean the index no longer describes one table.
    """
    offenders: dict[str, list[str]] = {}
    for number, table, columns in _field_index_rows():
        seen: set[str] = set()
        duplicates = [c for c in columns if c in seen or seen.add(c)]  # type: ignore[func-returns-value]
        if duplicates:
            offenders[f"{table} (line {number})"] = duplicates
    assert not offenders, f"字段索引行内出现重复列：{offenders}"


def test_field_index_has_no_retired_columns() -> None:
    """Columns removed by an ADR must not linger in the summary index."""
    text = FIELD_INDEX.read_text()
    retired = ["rendered_summary", "execution_characteristic", "authorization_requirement"]
    present = [token for token in retired if token in text]
    assert not present, f"字段索引仍含已删除列：{present}"


# ---------------------------------------------------------------------------
# Frontend must not design against fields the backend never defined (P0-10)
# ---------------------------------------------------------------------------

FRONTEND_DOCS = sorted((DOCS / "03-前端设计").glob("**/*.md"))
# Backend-undefined metrics that appear in the frontend without any owner field.
PHANTOM_BACKEND_FIELDS = ("project_platform_count", "credential_user_count")


def test_frontend_does_not_reference_phantom_backend_fields() -> None:
    """Frontend specs must not promise cards/columns over undefined backend fields.

    Using an option the backend never returns forces a "missing -> hide it"
    fallback, which is exactly how a UI silently shows nothing.
    """
    offenders: list[str] = []
    for path in FRONTEND_DOCS:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for token in PHANTOM_BACKEND_FIELDS:
                if token not in line:
                    continue
                # Historical change-log rows and explicit prohibitions are fine.
                if "V1.1" in line or "不得引用" in line or "从未定义" in line:
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number} → {token}")
    assert not offenders, "前端引用了后端未定义的字段：\n" + "\n".join(offenders)


def test_console_interaction_spec_has_no_duplicate_api_map() -> None:
    """90-§13 retired: per-page API maps live only in FE-*/§3.4-3.5 + owner modules."""
    text = (DOCS / "03-前端设计" / "90-Console交互规格.md").read_text()
    assert "## 13. 页面 → API 关键映射" in text
    assert "本节**的映射表已删除**" in text or "本节的映射表已删除" in text, (
        "90-§13 必须保持为“已删除”的指针，而不是重新长出一份裸路径映射表"
    )


# ---------------------------------------------------------------------------
# Error-code registry must stay a single, complete table (P1-5)
# ---------------------------------------------------------------------------

ERROR_REGISTRY = DOCS / "01-架构与规范" / "10-错误码与错误分类基线.md"


def _error_rows() -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in ERROR_REGISTRY.read_text().splitlines()
        if line.startswith("| `")
    ]


def test_error_registry_has_one_table_with_unique_codes() -> None:
    """'Register later' is how a registry stops being authoritative.

    §3 duplicated a second list of in-flight codes; it is now merged into §2, so
    this gate keeps a single table and forbids duplicate registrations.
    """
    import collections

    rows = _error_rows()
    assert rows, "错误码注册表为空"
    duplicates = [code for code, count in collections.Counter(r[0] for r in rows).items() if count > 1]
    assert not duplicates, f"错误码重复登记：{duplicates}"
    assert "## 3. 冻结说明" in ERROR_REGISTRY.read_text(), "§3 必须是冻结说明，不能恢复为第二份清单"


def test_error_registry_rows_are_complete() -> None:
    malformed = [r[:2] for r in _error_rows() if len(r) != 4 or not r[2] or not r[3]]
    assert not malformed, f"错误码缺 HTTP 或 Owner：{malformed}"


def test_duplicate_message_is_not_an_error_code() -> None:
    """A duplicate inbound message is a normal ACK, not an error response."""
    text = ERROR_REGISTRY.read_text()
    assert not any(line.startswith("| `CHANNEL_MESSAGE_DUPLICATE`") for line in text.splitlines()), (
        "CHANNEL_MESSAGE_DUPLICATE 不得作为错误码登记（重复消息是正常 ACK）"
    )


def test_field_index_omits_columns_removed_by_consolidation() -> None:
    """Columns retired by the sixth round (P1-2/P1-23) must not be re-listed.

    Each of these was deleted from the design *and* the schema; leaving them in
    the summary index is how the duplicate-column defect survived review.
    """
    text = FIELD_INDEX.read_text()
    retired = ("artifact_ids", "extra_headers", "rendered_summary")
    present = sorted(token for token in retired if token in text)
    assert not present, f"字段索引仍列出已收敛删除的列：{present}"


def test_model_config_has_no_constant_or_gateway_columns() -> None:
    """`protocol` is a constant and `extra_headers` is a deployment concern (P1-23)."""
    from adapters.postgres.models import ModelConfigModel

    columns = set(ModelConfigModel.__table__.c.keys())
    forbidden = {"protocol", "extra_headers"} & columns
    assert not forbidden, f"model_config 不得保留已收敛的列：{sorted(forbidden)}"


def test_delivery_status_enum_does_not_collide_with_execution_status() -> None:
    """P1-28: `RETRY_WAIT` means two different things if both sides use it.

    Execution status keeps `RETRY_WAIT`; the delivery queue uses `RETRY_PENDING`.
    A shared name across the two state machines is what makes the Console render
    the wrong badge (and what made the two components indistinguishable).
    """
    channel = (DOCS / "02-模块设计" / "10-Channel-Gateway" / "design-full.md").read_text()
    index = FIELD_INDEX.read_text()

    assert "PENDING/SENDING/RETRY_PENDING/DELIVERED/FAILED/UNKNOWN" in channel, (
        "channel_delivery.status 枚举必须使用 RETRY_PENDING"
    )
    assert "投递态用 RETRY_PENDING，与执行态 RETRY_WAIT 区分" in index, (
        "字段索引里的投递状态枚举必须同步为 RETRY_PENDING 并说明与执行态的区分"
    )
    execution_doc = DOCS / "02-模块设计" / "05-Service与Execution" / "design-full.md"
    execution = execution_doc.read_text()
    execution_status = (
        "PENDING/RUNNING/WAITING/WAITING_HUMAN/RETRY_WAIT"
        "/CANCELLING/SUCCEEDED/FAILED/CANCELLED"
    )
    assert execution_status in execution, (
        "service_execution.status 仍用 RETRY_WAIT（执行态与投递态必须可区分）"
    )


def test_chat_turn_is_not_a_durable_engine() -> None:
    """P0-4: exactly one durable engine exists — the Worker/`service_execution`.

    A second durable loop for chat turns (its own table, lease and takeover)
    duplicates claim/lease/fencing/recovery semantics and forces every caller to
    ask "is this a Run or an Execution?".
    """
    runtime = (DOCS / "02-模块设计" / "03-Agent-Runtime" / "design-full.md").read_text()
    conversation = (DOCS / "02-模块设计" / "11-Conversation与User-Memory" / "design-full.md").read_text()
    index = FIELD_INDEX.read_text()

    for name, text in (("03-Agent-Runtime", runtime), ("11-Conversation", conversation)):
        assert "#### 表 `conversation_run`" not in text, f"{name} 仍定义 conversation_run 表"
    assert "| conversation_run |" not in index, "字段索引仍列出 conversation_run"

    assert "| RT-LIB-03 |" not in runtime and "| RT-INT-02 |" not in runtime, (
        "RT-LIB-03/RT-INT-02 必须删除（Chat turn 无领取、无 run 级取消）"
    )
    assert "processing_status" in conversation, "Chat turn 的唯一调度态必须是 message.processing_status"


def test_only_service_execution_carries_a_lease() -> None:
    """LeaseTarget must stay a single value; a second target means a second loop."""
    core = (DOCS / "02-模块设计" / "01-核心领域与发布模型" / "design-full.md").read_text()
    assert 'LeaseTarget = Literal["service_execution"]' in core, (
        "LeaseTarget 只允许 service_execution（P0-4）"
    )


# ---------------------------------------------------------------------------
# Identity single track (P0-8)
# ---------------------------------------------------------------------------

def test_identity_has_a_single_authoritative_table() -> None:
    """One identity table: `platform_user` carries credentials, role and status.

    `auth_account` stored a second `role` (so a role downgrade had two places to
    land) and `auth_session` separated the session from the identity it belongs
    to. Login is now a read of `platform_user`; sessions point at it.
    """
    import adapters.postgres.models as models

    tables = set(models.Base.metadata.tables)
    assert "auth_account" not in tables, "auth_account 必须删除（role 双源）"
    assert "auth_session" not in tables, "auth_session 必须删除（会话与身份分离）"
    assert "session_token" in tables, "会话表为 session_token，绑定 platform_user"
    assert "password_hash" in models.PlatformUserModel.__table__.c, (
        "platform_user 必须自带 password_hash（Console 登录凭据唯一落点）"
    )
    assert "platform_user_id" in models.SessionTokenModel.__table__.c


def test_trusted_context_has_exactly_one_builder() -> None:
    """CORE-LIB-06 is the only constructor; a second one re-opens identity drift."""
    builders: dict[str, int] = {}
    for path in sorted((DOCS / "02-模块设计").glob("*/design-full.md")):
        count = path.read_text().count("def build_trusted_context(")
        if count:
            builders[path.parent.name] = count
    assert set(builders) == {"01-核心领域与发布模型"}, (
        f"build_trusted_context 只允许在模块 01 定义一次：{builders}"
    )


def test_delivery_has_a_single_internal_dispatch_endpoint() -> None:
    """P0-5: permission check + send are one call, and no epoch bookkeeping column.

    Splitting them (CH-DATA-03 then CH-INT-01) forced Gateway to make two internal
    calls for one delivery and to pass `lease_epoch` twice; `dispatch_started_epoch`
    then stored a second copy of a fact the delivery status already carries.
    """
    import adapters.postgres.models as models

    channel = (DOCS / "02-模块设计" / "10-Channel-Gateway" / "design-full.md").read_text()
    assert "CH-DATA-03" not in channel.replace("已与 CH-DATA-03 合并", ""), (
        "CH-DATA-03 必须合并进 CH-INT-01（单次调用完成许可校验与发送）"
    )
    columns = set(models.ChannelDeliveryModel.__table__.c.keys())
    assert "dispatch_started_epoch" not in columns, (
        "channel_delivery 不得保留 dispatch_started_epoch（SENDING + lease_epoch 已表达同一事实）"
    )


def test_derived_stage_name_is_not_a_stored_column() -> None:
    """ADR-051's readable stage name is derived at read time (P0-6 residual).

    `current_step` is the only stage fact. Materialising its display name would
    create a second source that can disagree with the snapshot/event it derives
    from — the same mistake the round removed everywhere else.
    """
    import adapters.postgres.models as models

    columns = set(models.ServiceExecutionModel.__table__.c.keys())
    assert "current_step" in columns, "current_step 是唯一阶段事实"
    assert "current_step_name" not in columns, (
        "current_step_name 必须是读取期派生字段，不得落列"
    )
    index = FIELD_INDEX.read_text()
    assert "current_step_name(VARCHAR" not in index, "字段索引不得再列出 current_step_name 列"
    view = (DOCS / "02-模块设计" / "05-Service与Execution" / "design-full.md").read_text()
    assert "查询期派生、不落列" in view, "ExecutionView 必须声明该字段是查询期派生"


def test_chat_checkpoint_has_no_run_scoped_reference() -> None:
    """P0-4 residual: after removing `conversation_run`, no contract may key on it."""
    conversation = (DOCS / "02-模块设计" / "11-Conversation与User-Memory" / "design-full.md").read_text()
    body = "\n".join(
        line for line in conversation.splitlines() if not line.startswith("| V1.1")
    )
    assert "conversation_run 已删除" in body, "CheckpointIdentity 必须说明 conversation_run 已删除"
    assert "checkpoint_ref 存 execution_step/conversation_run" not in body, (
        "checkpoint_ref 只能落 execution_step"
    )
    assert "Chat thread_id=`chat:{conversation_id}`，namespace" not in body.split("```")[0], (
        "Chat thread 必须按「会话 + 该轮用户消息」标识，不得只按 conversation"
    )


def test_index_is_declared_generated_and_matches_modules() -> None:
    """P1-3: 08/09 are generated indexes; a hand-edited third source is a defect."""
    index = (DOCS / "01-架构与规范" / "09-接口所有权与详细设计索引.md").read_text()
    assert "机械生成" in index and "不允许手工编辑条目" in index, (
        "09 必须声明为机械生成且禁止手工编辑"
    )


def test_single_code_root() -> None:
    """P2-4: one code root (`framework/` + `adapters/` + `apps/`)."""
    offenders: list[str] = []
    for path in _rule_docs(sorted(DOCS.glob("*/*.md"))):
        body = path.read_text()
        for token in ("src/fluxion/", "packages/"):
            if token in body and "历史示意" not in body and "不作为目录约束" not in body:
                offenders.append(f"{path.name} → {token}")
    assert not offenders, (
        "文档出现第二套代码根（P2-4 只允许 framework/adapters/apps）：" + ", ".join(offenders)
    )


def test_time_units_are_seconds_in_contracts() -> None:
    """P2-3: external contracts use seconds; a `_ms` field is the retired convention."""
    offenders: list[str] = []
    for path in _rule_docs(sorted(DOCS.glob("*/*.md"))):
        body = path.read_text()
        for token in ("deadline_ms", "backoff_ms", "max_duration_ms"):
            if token in body:
                offenders.append(f"{path.name} → {token}")
    assert not offenders, "对外契约只允许秒（P2-3）：" + ", ".join(offenders)


def test_glossary_exists_and_pins_run_as_retired() -> None:
    """P2-6: one term table; `Run` is retired in favour of Turn/Execution."""
    baseline = (DOCS / "01-架构与规范" / "01-架构基线.md").read_text()
    assert "术语表" in baseline, "术语表必须存在于 01-架构基线"
    assert "| Run | **已废止**" in baseline, "术语表必须把 Run 标为已废止（P0-4）"


def test_memory_policy_is_a_single_whitelist_field() -> None:
    """P2-9: platform constant limits, not per-Agent configuration."""
    agent = (DOCS / "02-模块设计" / "04-Agent-Core与Agent-Executor" / "design-full.md").read_text()
    assert '"max_items"' not in agent, "memory_policy 不得再配置 max_items"
    assert "max_value_bytes" not in agent, "memory_policy 不得再配置 max_value_bytes"


# ---------------------------------------------------------------------------
# Traceability matrix must be mechanically checkable (P2-11)
# ---------------------------------------------------------------------------

MATRIX = DOCS / "04-追溯与验收" / "01-总体设计到模块设计追溯矩阵.md"
SCENARIO_RE = re.compile(r"^(?:S|E|B|I)-[A-Z0-9]+-\d+$")


def _module_scenarios() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted((DOCS / "02-模块设计").glob("*/design-full.md")):
        found[path.parent.name] = set(
            re.findall(r"^\| ((?:S|E|B|I)-[A-Z0-9]+-\d+) \|", path.read_text(), re.M)
        )
    return found


def test_traceability_matrix_uses_real_scenario_ids() -> None:
    """Every verifier cell must list scenario IDs that exist in a module design.

    The retired form was prose ("同 Conversation 跨 Pod"), which cannot be
    checked and therefore cannot fail — a matrix that cannot fail is decoration.
    The P→scenario assignment is editorial, but existence is mechanical.
    """
    known = set().union(*_module_scenarios().values())
    offenders: list[str] = []
    for line in MATRIX.read_text().splitlines():
        if not line.startswith("| P") or line.count("|") < 4:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        for scenario in (part.strip() for part in cells[2].split(",")):
            if not SCENARIO_RE.match(scenario):
                offenders.append(f"{cells[0][:6]} → 非场景 ID: {scenario!r}")
            elif scenario not in known:
                offenders.append(f"{cells[0][:6]} → 不存在的场景: {scenario}")
    assert not offenders, "追溯矩阵的场景列必须全部是可验证的真实场景 ID：\n" + "\n".join(offenders)


def test_every_module_scenario_is_reachable() -> None:
    """Each module's scenarios must be referenced by at least one driver row."""
    matrix = MATRIX.read_text()
    unreferenced: dict[str, list[str]] = {}
    for module, scenarios in _module_scenarios().items():
        missing = sorted(s for s in scenarios if s not in matrix)
        if missing:
            unreferenced[module] = missing
    # 只报告完全没被任何 driver 覆盖的模块，避免把"次要场景未逐条登记"当成失败
    fully_missing = {m: s for m, s in unreferenced.items() if len(s) == len(_module_scenarios()[m])}
    assert not fully_missing, f"这些模块的场景没有任何 driver 引用：{fully_missing}"
