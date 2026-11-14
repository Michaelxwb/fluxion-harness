"""Deterministic user-memory v2 evaluation primitives (TASK-008)."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class QueryLabel:
    query_id: str
    user_id: str
    required: tuple[str, ...]
    forbidden: tuple[str, ...] = ()
    acceptable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    query_id: str
    injected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QueryScore:
    query_id: str
    user_id: str
    recall: float | None
    precision: float | None
    required_injected: int
    required_total: int
    strict_tp: int
    strict_fp: int


@dataclass(frozen=True, slots=True)
class Aggregate:
    query_macro_recall: float | None
    query_macro_precision: float | None
    user_macro_recall: float | None
    user_macro_precision: float | None
    pooled_recall: float | None
    pooled_precision: float | None
    n_queries: int
    n_na_recall: int
    n_na_precision: int


def score_query(label: QueryLabel, outcome: QueryOutcome) -> QueryScore:
    injected = set(outcome.injected)
    required = set(label.required)
    forbidden = set(label.forbidden)
    required_injected = len(required & injected)
    strict_tp = required_injected
    strict_fp = len((forbidden | (injected - required - set(label.acceptable))) & injected)
    recall = required_injected / len(required) if required else None
    denominator = strict_tp + strict_fp
    precision = strict_tp / denominator if denominator else None
    return QueryScore(
        query_id=label.query_id,
        user_id=label.user_id,
        recall=recall,
        precision=precision,
        required_injected=required_injected,
        required_total=len(required),
        strict_tp=strict_tp,
        strict_fp=strict_fp,
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _user_macro(scores: list[QueryScore], field: Literal["recall", "precision"]) -> float | None:
    by_user: dict[str, list[float]] = {}
    for score in scores:
        value = getattr(score, field)
        if value is not None:
            by_user.setdefault(score.user_id, []).append(value)
    user_means = [_mean(values) for values in by_user.values()]
    return _mean([value for value in user_means if value is not None])


def aggregate(scores: list[QueryScore]) -> Aggregate:
    recalls = [score.recall for score in scores if score.recall is not None]
    precisions = [score.precision for score in scores if score.precision is not None]
    required_total = sum(score.required_total for score in scores)
    required_injected = sum(score.required_injected for score in scores)
    strict_tp = sum(score.strict_tp for score in scores)
    strict_fp = sum(score.strict_fp for score in scores)
    return Aggregate(
        query_macro_recall=_mean(recalls),
        query_macro_precision=_mean(precisions),
        user_macro_recall=_user_macro(scores, "recall"),
        user_macro_precision=_user_macro(scores, "precision"),
        pooled_recall=required_injected / required_total if required_total else None,
        pooled_precision=strict_tp / (strict_tp + strict_fp) if strict_tp + strict_fp else None,
        n_queries=len(scores),
        n_na_recall=sum(score.recall is None for score in scores),
        n_na_precision=sum(score.precision is None for score in scores),
    )


def paired_user_bootstrap_delta(
    baseline: list[QueryScore],
    updated: list[QueryScore],
    *,
    field: Literal["recall", "precision"] = "recall",
    reps: int = 2000,
    seed: int = 7,
) -> tuple[float, tuple[float, float]]:
    if reps < 1:
        raise ValueError("reps must be positive")
    base_by_user: dict[str, list[QueryScore]] = {}
    updated_by_user: dict[str, list[QueryScore]] = {}
    for score in baseline:
        base_by_user.setdefault(score.user_id, []).append(score)
    for score in updated:
        updated_by_user.setdefault(score.user_id, []).append(score)
    users = sorted(set(base_by_user) & set(updated_by_user))
    if not users:
        raise ValueError("no paired users")
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(reps):
        sampled = [users[rng.randrange(len(users))] for _ in users]
        base_scores = [score for user in sampled for score in base_by_user[user]]
        updated_scores = [score for user in sampled for score in updated_by_user[user]]
        base_value = _mean([value for score in base_scores if (value := getattr(score, field)) is not None])
        updated_value = _mean(
            [value for score in updated_scores if (value := getattr(score, field)) is not None]
        )
        if base_value is not None and updated_value is not None:
            deltas.append(updated_value - base_value)
    deltas.sort()
    point = (
        _mean([value for score in updated if (value := getattr(score, field)) is not None]) or 0.0
    ) - (_mean([value for score in baseline if (value := getattr(score, field)) is not None]) or 0.0)
    low = deltas[max(0, int(reps * 0.025) - 1)]
    high = deltas[min(len(deltas) - 1, int(reps * 0.975))]
    return point, (low, high)


def load_fixture(path: Path) -> tuple[list[QueryLabel], dict[str, QueryOutcome]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    labels = [
        QueryLabel(
            query_id=item["query_id"],
            user_id=item["user_id"],
            required=tuple(item.get("required", ())),
            forbidden=tuple(item.get("forbidden", ())),
            acceptable=tuple(item.get("acceptable", ())),
        )
        for item in payload["queries"]
    ]
    outcomes = {
        key: QueryOutcome(query_id=value["query_id"], injected=tuple(value.get("injected", ())))
        for key, value in payload["outcomes"].items()
    }
    return labels, outcomes


# ---------------------------------------------------------------------------
# Retrieval runs: the frozen baseline (untouched select_experience) and v2.
# ---------------------------------------------------------------------------

EXPERIENCE_KINDS = frozenset({"PROCEDURE", "FAILURE_PATTERN", "VERIFICATION"})
_KIND_TO_LEGACY = {"PREFERENCE": "PREFERENCE", "FACT": "EXPLICIT", "NOTE": "WORK_STYLE"}
EVAL_NOW = datetime(2026, 11, 14, tzinfo=UTC)


def _record_v2(item: dict) -> object:
    from muad_agent_core.memory.selection import MemoryRecord
    from muad_contracts.memory import MemoryCardinality, MemoryPriority, ScopeState

    return MemoryRecord(
        memory_key=item["memory_key"],
        memory_kind=item["memory_kind"],
        predicate=item["predicate"],
        scope_state=ScopeState(item.get("scope_state", "ABSENT")),
        scope=dict(item.get("scope", {})),
        content=dict(item.get("content", {})),
        title=item.get("title", ""),
        summary=item.get("summary", ""),
        tags=tuple(item.get("tags", ())),
        cardinality=MemoryCardinality(item.get("cardinality", "SINGLE")),
        priority=MemoryPriority(item.get("priority", "OPTIONAL")),
        author=item.get("author", "USER"),
        version=int(item.get("version", 1)),
        updated_at=EVAL_NOW,
        expires_at=None,
        enabled=bool(item.get("enabled", True)),
    )


def _record_v1(item: dict) -> object:
    """Baseline projection: the baseline only understands legacy categories."""

    from muad_agent_core.memory.rank import ExperienceRecord
    from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory

    content = dict(item.get("content", {}))
    value = content.get("value", content.get("text", ""))
    if not isinstance(value, str) or not value:
        value = item.get("title") or "-"
    return ExperienceRecord(
        memory_key=item["memory_key"],
        content=FactContent(value=value),
        author=MemoryAuthor(item.get("author", "USER")),
        category=MemoryCategory(_KIND_TO_LEGACY.get(item["memory_kind"], "WORK_STYLE")),
        version=int(item.get("version", 1)),
        updated_at=EVAL_NOW,
        last_confirmed_at=EVAL_NOW,
        expires_at=None,
        enabled=bool(item.get("enabled", True)),
        is_deleted=False,
        explicit_request=False,
        activation_state="ACTIVE",
        task_signature=None,
        trigger_text=None,
    )


def run_selection(memories: list[dict], query: str, *, algorithm: str, top_k: int = 10) -> tuple[str, ...]:
    """Select the injected keys under one algorithm; identical inputs and budget."""

    if algorithm == "v2":
        from muad_agent_core.memory.selection import QueryContext, select_memory

        selected = select_memory(
            [_record_v2(item) for item in memories],
            QueryContext(query=query, now=EVAL_NOW),
            top_k=top_k,
        )
        return tuple(item.record.memory_key for item in selected)
    if algorithm == "baseline":
        from muad_agent_core.memory.rank import select_experience

        selection = select_experience(
            [_record_v1(item) for item in memories],
            query=query,
            now=EVAL_NOW,
            inject_inferred=True,
            preference_items=top_k,
        )
        return tuple(record.memory_key for record in selection.records)
    raise ValueError(f"unknown algorithm: {algorithm}")


def evaluate(path: Path, *, top_k: int = 10) -> dict:
    """Run baseline and v2 on one fixture and return the reproducible report."""

    import hashlib

    raw = path.read_bytes()
    payload = json.loads(raw)
    facts = [item for item in payload["memories"] if item["memory_kind"] not in EXPERIENCE_KINDS]
    experience = [item for item in payload["memories"] if item["memory_kind"] in EXPERIENCE_KINDS]
    labels = [
        QueryLabel(
            query_id=item["query_id"],
            user_id=item["user_id"],
            required=tuple(item.get("required", ())),
            forbidden=tuple(item.get("forbidden", ())),
            acceptable=tuple(item.get("acceptable", ())),
        )
        for item in payload["queries"]
    ]
    report: dict = {
        "manifest": {
            "fixture": str(path),
            "fixture_sha256": hashlib.sha256(raw).hexdigest(),
            "top_k": top_k,
            "mode": "oracle",
        },
        "experience_lane": {
            "memory_keys": sorted(item["memory_key"] for item in experience),
            "injected": {
                query["query_id"]: list(
                    run_selection(experience, query.get("query", ""), algorithm="baseline")
                )
                for query in payload["queries"]
            }
            if experience
            else {},
        },
    }
    scores: dict[str, list[QueryScore]] = {}
    for algorithm in ("baseline", "v2"):
        runs = []
        for index, label in enumerate(labels):
            query = payload["queries"][index].get("query", "")
            injected = run_selection(facts, query, algorithm=algorithm, top_k=top_k)
            runs.append(score_query(label, QueryOutcome(label.query_id, injected)))
        scores[algorithm] = runs
        report[algorithm] = {
            "aggregate": _asdict(aggregate(runs)),
            "queries": [_asdict(score) for score in runs],
        }
    recall_delta, recall_ci = paired_user_bootstrap_delta(
        scores["baseline"], scores["v2"], field="recall", reps=200, seed=11
    )
    precision_delta, precision_ci = paired_user_bootstrap_delta(
        scores["baseline"], scores["v2"], field="precision", reps=200, seed=11
    )
    report["delta"] = {
        "recall": {"point": recall_delta, "ci": list(recall_ci)},
        "precision": {"point": precision_delta, "ci": list(precision_ci)},
    }
    report["manifest"]["extractor"] = (
        "STRUCTURED_FIXTURE" if "structured_output" in payload else "SKIPPED_NO_MODEL"
    )
    return report


def _asdict(value: object) -> dict:
    from dataclasses import asdict

    return asdict(value)
