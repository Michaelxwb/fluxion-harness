"""TASK-008: query-macro primary aggregation and paired user bootstrap."""

from __future__ import annotations

from pathlib import Path

from scripts.memory_eval import (
    QueryLabel,
    QueryOutcome,
    aggregate,
    load_fixture,
    paired_user_bootstrap_delta,
    score_query,
)


def _scores():
    labels = [
        QueryLabel("q1", "u1", ("m1",)),
        QueryLabel("q2", "u1", tuple(f"m{i}" for i in range(1, 10))),
    ]
    outcomes = {
        "q1": QueryOutcome("q1", ("m1",)),
        "q2": QueryOutcome("q2", ()),
    }
    return [score_query(label, outcomes[label.query_id]) for label in labels]


def test_query_macro_differs_from_pooled_and_is_primary():
    agg = aggregate(_scores())
    assert agg.query_macro_recall == 0.5
    assert agg.user_macro_recall == 0.5
    assert agg.pooled_recall == 0.1
    assert agg.n_na_recall == 0


def test_paired_bootstrap_returns_delta_ci_for_metric():
    base = _scores()
    updated = [
        score_query(QueryLabel("q1", "u1", ("m1",)), QueryOutcome("q1", ("m1",))),
        score_query(
            QueryLabel("q2", "u1", tuple(f"m{i}" for i in range(1, 10))),
            QueryOutcome("q2", tuple(f"m{i}" for i in range(1, 10))),
        ),
    ]
    point, (low, high) = paired_user_bootstrap_delta(base, updated, reps=200, seed=1)
    assert point == 0.5
    assert low <= point <= high


def test_fixture_loader_preserves_required_and_forbidden():
    labels, outcomes = load_fixture(Path("tests/fixtures/memory/minimal.json"))
    assert labels[0].required == ("m1",)
    assert labels[0].forbidden == ("m9",)
    assert outcomes["q2"].injected == ()
    assert aggregate([score_query(label, outcomes[label.query_id]) for label in labels]).pooled_recall == 0.1


def test_evaluate_runs_baseline_and_v2_on_the_same_fixture():
    from scripts.memory_eval import evaluate

    report = evaluate(Path("tests/fixtures/memory/seeded.json"))
    assert report["manifest"]["fixture_sha256"] and report["manifest"]["mode"] == "oracle"
    assert report["baseline"]["aggregate"]["n_queries"] == 2
    assert report["v2"]["aggregate"]["n_queries"] == 2
    assert report["v2"]["aggregate"]["query_macro_recall"] >= 0.5
    assert report["delta"]["recall"]["point"] >= 0
    # the scope gate keeps the external value out of an internal-only query
    s1 = next(item for item in report["v2"]["queries"] if item["query_id"] == "s1")
    assert s1["recall"] == 1.0 and s1["strict_fp"] == 0


def test_experience_lane_is_reported_separately_from_v2_metrics():
    from scripts.memory_eval import evaluate

    report = evaluate(Path("tests/fixtures/memory/seeded.json"))
    assert report["experience_lane"]["memory_keys"] == ["exp.deploy.check"]
    assert "exp.deploy.check" not in str(report["v2"]["queries"])
