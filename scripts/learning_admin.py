#!/usr/bin/env python3
# mypy: disable-error-code="unused-ignore"
"""learning_admin：学习链路的运维命令面（stdout JSON，stderr 原因，operator 必填）。

子命令：list / extract / candidates / replay / activate / retire / explain。
只操作指定 tenant/user；replay 走生产 CaseRunner（来源 Run + 冻结 Snapshot）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx
import sqlalchemy as sa

from muad_agent_runtime.application.experience_candidates import ExperienceCandidates  # type: ignore[import-untyped]
from muad_agent_runtime.application.golden_set import GoldenSetExtractor  # type: ignore[import-untyped]
from muad_agent_runtime.application.learning_activation import ActivationPolicy, LearningActivation  # type: ignore[import-untyped]
from muad_agent_runtime.application.learning_case_runner import (  # type: ignore[import-untyped]
    ProductionCaseRunner,
    database_credentials_resolver,
)
from muad_agent_runtime.application.learning_replay import LearningReplay  # type: ignore[import-untyped]
from muad_agent_runtime.infrastructure.db import get_session_factory  # type: ignore[import-untyped]
from muad_agent_runtime.infrastructure.models.runtime import EvalReport, UserMemory  # type: ignore[import-untyped]

EXPERIENCE_CATEGORIES = ("PROCEDURE", "FAILURE_PATTERN", "VERIFICATION")


async def _list(factory: Any, tenant_id: str, user_id: UUID, state: str | None, family: str | None) -> list[dict[str, Any]]:
    async with factory() as session:
        statement = sa.select(UserMemory).where(
            UserMemory.tenant_id == tenant_id,
            UserMemory.user_id == user_id,
            UserMemory.category.in_(EXPERIENCE_CATEGORIES),
            UserMemory.is_deleted.is_(False),
        )
        if state:
            statement = statement.where(UserMemory.activation_state == state)
        if family:
            statement = statement.where(UserMemory.task_signature == family)
        rows = list((await session.scalars(statement.order_by(UserMemory.memory_key))).all())
    return [
        {
            "memory_key": row.memory_key,
            "category": row.category,
            "activation_state": row.activation_state,
            "task_signature": row.task_signature,
            "trigger_text": row.trigger_text,
        }
        for row in rows
    ]


async def _explain(factory: Any, tenant_id: str, user_id: UUID, key: str) -> dict[str, Any] | None:
    async with factory() as session:
        row = await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.memory_key == key,
            )
        )
        reports = list(
            (
                await session.scalars(
                    sa.select(EvalReport)
                    .where(EvalReport.tenant_id == tenant_id, EvalReport.experience_key == key)
                    .order_by(EvalReport.version.desc())
                    .limit(5)
                )
            ).all()
        )
    if row is None:
        return None
    return {
        "memory_key": row.memory_key,
        "activation_state": row.activation_state,
        "task_signature": row.task_signature,
        "evidence": row.evidence_json,
        "reports": [
            {"version": report.version, "decision": report.decision_json, "metrics": report.metrics_json}
            for report in reports
        ],
    }


async def _replay(
    factory: Any, tenant_id: str, user_id: UUID, key: str, base_config_hash: str,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    runner = ProductionCaseRunner(
        factory, resolve_credentials=database_credentials_resolver(factory), transport=transport
    )
    replay = LearningReplay(factory, runner)
    async with factory() as session:
        row = await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.memory_key == key,
            )
        )
    if row is None or not row.task_signature:
        return {"status": "NOT_FOUND"}
    decision = await replay.run(
        tenant_id,
        row.task_signature,
        experience_key=key,
        base_config_hash=base_config_hash,
    )
    return {
        "status": decision.status,
        "improvement": decision.improvement,
        "baseline": decision.baseline.as_json(),
        "candidate": decision.candidate.as_json(),
    }


async def _run(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None) -> int:
    factory = get_session_factory()
    user_id = UUID(args.user_id)
    if args.command == "list":
        print(json.dumps(await _list(factory, args.tenant_id, user_id, args.state, args.task_family)))
        return 0
    if args.command == "extract":
        report = await GoldenSetExtractor(factory).extract(args.tenant_id, task_family=args.task_family)
        print(json.dumps({"inserted": report.inserted, "skipped": report.skipped, "incomplete": report.incomplete}))
        return 0
    if args.command == "candidates":
        candidate_report = await ExperienceCandidates(factory).generate(
            args.tenant_id, task_family=args.task_family
        )
        print(json.dumps({"created": candidate_report.created, "skipped": candidate_report.skipped}))
        return 0
    if args.command == "replay":
        result = await _replay(factory, args.tenant_id, user_id, args.experience_key, args.base_config_hash, transport)
        print(json.dumps(result))
        return 0 if result.get("status") in {"PASSED", "REJECTED", "INCOMPLETE"} else 1
    if args.command == "activate":
        if not args.operator:
            raise ValueError("--operator is required")
        outcome = await LearningActivation(factory, policy=ActivationPolicy()).activate_manually(
            args.tenant_id,
            user_id,
            args.experience_key,
            base_config_hash=args.base_config_hash,
            operator=args.operator,
        )
        print(json.dumps({"status": outcome.status, "activation_state": outcome.activation_state}))
        return 0 if outcome.activation_state == "ACTIVE" else 1
    if args.command == "retire":
        if not args.operator or not args.reason:
            raise ValueError("--operator and --reason are required")
        outcome = await LearningActivation(factory).retire(
            args.tenant_id,
            user_id,
            args.experience_key,
            reason=args.reason,
            operator=args.operator,
            base_config_hash=args.base_config_hash,
        )
        print(json.dumps({"status": outcome.status, "activation_state": outcome.activation_state}))
        return 0
    if args.command == "explain":
        detail = await _explain(factory, args.tenant_id, user_id, args.experience_key)
        print(json.dumps(detail))
        return 0 if detail is not None else 1
    raise ValueError(f"unknown command: {args.command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "extract", "candidates", "replay", "activate", "retire", "explain"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--tenant-id", required=True)
        cmd.add_argument("--user-id", required=True)
        if name == "list":
            cmd.add_argument("--state")
            cmd.add_argument("--task-family")
        if name in ("extract", "candidates"):
            cmd.add_argument("--task-family")
        if name in ("replay", "activate", "retire", "explain"):
            cmd.add_argument("--experience-key", required=True)
            cmd.add_argument("--base-config-hash", default="cli")
        if name in ("activate", "retire"):
            cmd.add_argument("--operator", required=True)
        if name == "retire":
            cmd.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args, transport=transport))
    except Exception as exc:  # noqa: BLE001 - CLI 边界：错误走 stderr 与退出码
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
