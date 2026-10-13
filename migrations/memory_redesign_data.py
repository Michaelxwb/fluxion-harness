"""0020 data conversion. Reads only the explicit Alembic connection."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from muad_contracts.canonical import canonical_json
from muad_contracts.memory import (
    MEMORY_MAX_BYTES,
    MEMORY_MAX_RECORDS,
    FactContent,
    RememberMemoryInput,
    memory_content_bytes,
)
from muad_contracts.platform_settings import MemoryPolicySettings, parse_platform_settings
from pydantic import JsonValue, ValidationError
from sqlalchemy import Connection, Table
from sqlalchemy.engine import RowMapping

_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+",
    re.I,
)
_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


def _table(bind: Connection, name: str, schema: str = "runtime") -> Table:
    return sa.Table(name, sa.MetaData(), schema=schema, autoload_with=bind)


def _row(values: RowMapping) -> dict[str, object]:
    return {str(key): value for key, value in values.items()}


def _json(value: object) -> JsonValue:
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    raise ValueError("MEMORY_MIGRATION_INVALID_JSON")


def _snapshot(row: Mapping[str, object]) -> dict[str, JsonValue]:
    return {str(key): _json(value) for key, value in row.items() if key != "source_type"}


def _number(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("MEMORY_MIGRATION_INVALID_COUNTER")
    return value


def _date(row: Mapping[str, object], name: str) -> datetime:
    value = row[name]
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("MEMORY_MIGRATION_INVALID_TIME")
    return value


def _text(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise ValueError("MEMORY_MIGRATION_INVALID_IDENTITY")
    return value


def _uuid(row: Mapping[str, object], name: str) -> UUID:
    value = row[name]
    if not isinstance(value, UUID):
        raise ValueError("MEMORY_MIGRATION_INVALID_IDENTITY")
    return value


def _document(value: object) -> dict[str, JsonValue]:
    if value is None:
        return {}
    result = _json(value)
    if not isinstance(result, dict):
        raise ValueError("MEMORY_MIGRATION_INVALID_SETTINGS")
    return result


def _has_active(bind: Connection, table: Table, terminal: tuple[str, ...]) -> bool:
    return (
        bind.execute(
            sa.select(table.c.id)
            .where(table.c.is_deleted.is_(False), table.c.status.not_in(terminal))
            .limit(1)
        ).first()
        is not None
    )


def _preflight_drain(bind: Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE runtime.run_record, runtime.run_interrupt, runtime.user_memory, "
            "task.task_execution IN ACCESS EXCLUSIVE MODE"
        )
    )
    for name, schema, terminal in (
        ("run_record", "runtime", _TERMINAL),
        ("task_execution", "task", _TERMINAL),
        ("run_interrupt", "runtime", ("RESOLVED", "EXPIRED", "CANCELLED")),
        ("tool_operation", "runtime", (*_TERMINAL, "LATE")),
        ("tool_control_outbox", "runtime", ("SENT", "FAILED")),
        ("runtime_result_outbox", "task", ("SENT", "FAILED")),
    ):
        if _has_active(bind, _table(bind, name, schema), terminal):
            raise ValueError("MEMORY_MIGRATION_DRAIN_REQUIRED")
    if "memory_consolidation_job" in sa.inspect(bind).get_table_names("runtime"):
        if _has_active(bind, _table(bind, "memory_consolidation_job"), ("DONE", "SKIPPED", "FAILED")):
            raise ValueError("MEMORY_MIGRATION_DRAIN_REQUIRED")


def preflight_memory(bind: Connection) -> None:
    _preflight_drain(bind)
    table = _table(bind, "user_memory")
    quotas: dict[tuple[str, UUID], tuple[int, int]] = {}
    for raw_row in bind.execute(sa.select(table)).mappings():
        row = _row(raw_row)
        raw = canonical_json(_document(row))
        segments = re.split(r"[.:-]", _text(row, "memory_key"))
        if _SECRET.search(raw) or any(
            segment in ("password", "apikey", "token", "secret") for segment in segments
        ):
            raise ValueError("MEMORY_MIGRATION_FORBIDDEN_CONTENT")
        try:
            content = FactContent.model_validate(row["content_json"])
            RememberMemoryInput.model_validate(
                {"memory_key": row["memory_key"], "category": row["category"], "value": content.value}
            )
        except (ValidationError, KeyError, TypeError):
            raise ValueError("MEMORY_MIGRATION_INVALID_LEGACY_MEMORY") from None
        if _number(row, "version") < 1:
            raise ValueError("MEMORY_MIGRATION_INVALID_LEGACY_VERSION")
        if not row["is_deleted"]:
            scope = (_text(row, "tenant_id"), _uuid(row, "user_id"))
            count, size = quotas.get(scope, (0, 0))
            count, size = count + 1, size + memory_content_bytes(_json(row["content_json"]), None)
            if count > MEMORY_MAX_RECORDS or size > MEMORY_MAX_BYTES:
                raise ValueError("MEMORY_MIGRATION_ABSOLUTE_QUOTA_EXCEEDED")
            quotas[scope] = (count, size)


def _settings_document(raw: object) -> dict[str, JsonValue]:
    document = _document(raw)
    compaction = _document(document.get("compaction"))
    compaction.pop("memory", None)
    compaction.setdefault("max_history_tool_rounds", 6)
    document["compaction"] = compaction
    try:
        return asdict(parse_platform_settings(document))
    except (ValidationError, ValueError, TypeError):
        raise ValueError("MEMORY_MIGRATION_INVALID_SETTINGS") from None


def _convert_setting(
    bind: Connection, settings: Table, audit: Table, row: Mapping[str, object]
) -> MemoryPolicySettings:
    document = _settings_document(row["settings_json"])
    inserted = bind.execute(
        settings.insert()
        .values(
            tenant_id=row["tenant_id"],
            revision=_number(row, "revision") + 1,
            settings_json=document,
            actor_user_id=None,
        )
        .returning(settings.c.id)
    ).scalar_one()
    bind.execute(
        audit.insert().values(
            tenant_id=row["tenant_id"],
            actor_user_id=UUID(int=0),
            resource_type="PLATFORM_SETTING",
            resource_id=inserted,
            action="RESTORE",
            before_json=row["settings_json"],
            after_json=document,
            trace_id="memory-redesign-0020",
        )
    )
    return parse_platform_settings(document).memory


def _agent_snapshot(row: Mapping[str, object]) -> dict[str, JsonValue]:
    return {
        **{
            name: _json(row[name])
            for name in ("key", "name", "description", "instructions", "model_id", "revision", "enabled")
        },
        "runtime_config": _json(row["runtime_config_json"]),
    }


def _migrate_agent_overrides(bind: Connection, audit: Table) -> None:
    agents = _table(bind, "agent_definition", "control")
    for raw in bind.execute(sa.select(agents).where(agents.c.is_deleted.is_(False))).mappings():
        row = _row(raw)
        config = _document(row["runtime_config_json"])
        budget = _document(config.get("budget"))
        compaction = _document(budget.get("compaction"))
        if "memory" in compaction:
            compaction.pop("memory")
            budget["compaction"], config["budget"] = compaction, budget
            updated = (
                bind.execute(
                    agents.update()
                    .where(agents.c.id == row["id"])
                    .values(
                        runtime_config_json=config, revision=agents.c.revision + 1, update_time=sa.func.now()
                    )
                    .returning(*agents.c)
                )
                .mappings()
                .one()
            )
            bind.execute(
                audit.insert().values(
                    tenant_id=row["tenant_id"],
                    actor_user_id=UUID(int=0),
                    resource_type="AGENT",
                    resource_id=row["id"],
                    action="UPDATE",
                    before_json=_agent_snapshot(row),
                    after_json=_agent_snapshot(_row(updated)),
                    trace_id="memory-redesign-0020",
                )
            )


def _migrate_settings(bind: Connection) -> dict[str, MemoryPolicySettings]:
    settings = _table(bind, "platform_setting", "control")
    audit = _table(bind, "config_audit_log", "control")
    latest = bind.execute(
        sa.select(settings)
        .where(settings.c.is_deleted.is_(False))
        .distinct(settings.c.tenant_id)
        .order_by(settings.c.tenant_id, settings.c.revision.desc())
    ).mappings()
    policies: dict[str, MemoryPolicySettings] = {}
    for raw in latest:
        row = _row(raw)
        policies[_text(row, "tenant_id")] = _convert_setting(bind, settings, audit, row)
    _migrate_agent_overrides(bind, audit)
    return policies


def _history(
    bind: Connection,
    table: Table,
    row: Mapping[str, object],
    reason: str,
    legacy: list[dict[str, JsonValue]] | None = None,
) -> None:
    bind.execute(
        table.insert().values(
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            memory_key=row["memory_key"],
            origin_id=row["id"],
            version=row["version"],
            change_reason=reason,
            actor_type="SYSTEM",
            actor_id="0020",
            command_id="memory-redesign:0020",
            after_json=_snapshot(row),
            legacy_snapshots_json=legacy,
        )
    )


def _update_row(
    bind: Connection,
    memory: Table,
    history: Table,
    row: Mapping[str, object],
    changes: dict[str, object],
    reason: str,
) -> Mapping[str, object]:
    updated = (
        bind.execute(
            memory.update()
            .where(memory.c.id == row["id"])
            .values(**changes, version=memory.c.version + 1, update_time=sa.func.now())
            .returning(*memory.c)
        )
        .mappings()
        .one()
    )
    normalized = _row(updated)
    _history(bind, history, normalized, reason)
    return normalized


def _seed_baseline(
    bind: Connection, memory: Table, history: Table, incarnations: list[Mapping[str, object]]
) -> tuple[Mapping[str, object], datetime]:
    chosen = max(
        incarnations, key=lambda row: (not row["is_deleted"], _date(row, "update_time"), str(row["id"]))
    )
    legacy = [{str(key): _json(value) for key, value in row.items()} for row in incarnations]
    discarded = [row["id"] for row in incarnations if row["id"] != chosen["id"]]
    if discarded:
        bind.execute(memory.delete().where(memory.c.id.in_(discarded)))
    baseline = _row(
        bind.execute(
            memory.update()
            .where(memory.c.id == chosen["id"])
            .values(version=max(_number(row, "version") for row in incarnations))
            .returning(*memory.c)
        )
        .mappings()
        .one()
    )
    _history(bind, history, baseline, "MIGRATION_BASELINE", legacy)
    return baseline, _date(chosen, "update_time")


def _migrate_key(
    bind: Connection,
    memory: Table,
    history: Table,
    incarnations: list[Mapping[str, object]],
    policy: MemoryPolicySettings,
    now: datetime,
    revision: int,
) -> tuple[Mapping[str, object], int]:
    baseline, source_time = _seed_baseline(bind, memory, history, incarnations)
    revision += 1
    changed = _update_row(
        bind,
        memory,
        history,
        baseline,
        {
            "author": "AGENT",
            "explicit_request": False,
            "confidence": 2,
            "write_policy": "SERVER_CONTROLLED",
            "expires_at": source_time + timedelta(days=policy.ttl_days_agent),
            "retirement_reason": "ADMIN_DELETE" if baseline["is_deleted"] else None,
            "retirement_revision": revision if baseline["is_deleted"] else None,
        },
        "MIGRATION_POLICY",
    )
    if not changed["is_deleted"] and _date(changed, "expires_at") <= now:
        revision += 1
        changed = _update_row(
            bind,
            memory,
            history,
            changed,
            {
                "is_deleted": True,
                "retirement_reason": "EXPIRE",
                "retirement_revision": revision,
            },
            "EXPIRE",
        )
    return changed, revision


def _migrate_scope(
    bind: Connection, rows: list[Mapping[str, object]], policy: MemoryPolicySettings, now: datetime
) -> None:
    memory, history, scopes = (
        _table(bind, name) for name in ("user_memory", "user_memory_history", "memory_scope")
    )
    scope_id = bind.execute(
        scopes.insert()
        .values(tenant_id=rows[0]["tenant_id"], user_id=rows[0]["user_id"])
        .returning(scopes.c.id)
    ).scalar_one()
    by_key: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_key[_text(row, "memory_key")].append(row)
    revision, active = 0, []
    for incarnations in by_key.values():
        changed, revision = _migrate_key(bind, memory, history, incarnations, policy, now, revision)
        if not changed["is_deleted"]:
            active.append(changed)
    revision = _trim_quota(bind, memory, history, active, policy, revision)
    bind.execute(
        scopes.update().where(scopes.c.id == scope_id).values(revision=revision, update_time=sa.func.now())
    )


def _trim_quota(
    bind: Connection,
    memory: Table,
    history: Table,
    active: list[Mapping[str, object]],
    policy: MemoryPolicySettings,
    revision: int,
) -> int:
    ordered = sorted(active, key=lambda row: (_date(row, "expires_at"), str(row["memory_key"])))
    sizes = [memory_content_bytes(_json(row["content_json"]), None) for row in ordered]
    count, size = len(ordered), sum(sizes)
    for row, row_size in zip(ordered, sizes, strict=True):
        if count <= policy.max_entries_per_user and size <= policy.max_memory_bytes_per_user:
            break
        revision += 1
        _update_row(
            bind,
            memory,
            history,
            row,
            {
                "is_deleted": True,
                "retirement_reason": "QUOTA_EVICT",
                "retirement_revision": revision,
            },
            "QUOTA_EVICT",
        )
        count, size = count - 1, size - row_size
    return revision


def migrate_memory_data(bind: Connection) -> None:
    policies = _migrate_settings(bind)
    memory = _table(bind, "user_memory")
    groups: dict[tuple[str, UUID], list[Mapping[str, object]]] = defaultdict(list)
    for raw_row in bind.execute(sa.select(memory)).mappings():
        row = _row(raw_row)
        groups[(_text(row, "tenant_id"), _uuid(row, "user_id"))].append(row)
    now = bind.execute(sa.select(sa.func.statement_timestamp())).scalar_one()
    for (tenant, _), rows in groups.items():
        _migrate_scope(bind, rows, policies.get(tenant, MemoryPolicySettings()), now)
