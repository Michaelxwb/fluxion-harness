"""CI validation-cache export: helper behavior and workflow/config alignment."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_cache = _load_script("ci_validation_cache")
merge_cache = _load_script("merge_validation_cache")


def test_select_validator_rejects_unknown_and_duplicate() -> None:
    validators = [{"name": "A"}, {"name": "B"}, {"name": "B"}]
    assert ci_cache.select_validator(validators, "A") == {"name": "A"}
    with pytest.raises(ValueError, match="unknown validator"):
        ci_cache.select_validator(validators, "C")
    with pytest.raises(ValueError, match="duplicate validator"):
        ci_cache.select_validator(validators, "B")


def test_merge_caches_prefers_newest_entry(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"k1": {"at": "2026-01-01", "fingerprint": "old"},
                                 "k2": {"at": "2026-01-01", "fingerprint": "keep"}}))
    second.write_text(json.dumps({"k1": {"at": "2026-01-02", "fingerprint": "new"}}))
    merged = merge_cache.merge_caches([first, second])
    assert merged["k1"]["fingerprint"] == "new"
    assert merged["k2"]["fingerprint"] == "keep"


def test_merge_cli_keeps_existing_output_as_base(tmp_path: Path) -> None:
    fragment = tmp_path / "fragment.json"
    output = tmp_path / ".validation-cache.json"
    fragment.write_text(json.dumps({"new": {"at": "2026-01-02"}}))
    output.write_text(json.dumps({"local": {"at": "2026-01-01"}}))
    assert merge_cache.main([str(fragment), "--output", str(output)]) == 0
    merged = json.loads(output.read_text())
    assert set(merged) == {"new", "local"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True, text=True)


def test_run_validator_writes_reusable_cache(tmp_path: Path) -> None:
    (tmp_path / ".code-flow").mkdir()
    (tmp_path / ".code-flow" / "validation.yml").write_text(
        "validators:\n"
        "  - name: cache-smoke\n"
        "    trigger: '**/*.py'\n"
        "    command: \"python3 -c \\\"print('ok')\\\"\"\n"
        "    heavy: true\n",
        encoding="utf-8",
    )
    (tmp_path / "sample.py").write_text("", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "sample.py")
    _git(tmp_path, "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "-q", "-m", "init")

    first = ci_cache.run_validator(str(tmp_path), "cache-smoke")
    assert first["decision"] == "pass"
    cache_file = tmp_path / ".code-flow" / ".validation-cache.json"
    assert len(json.loads(cache_file.read_text())) == 1

    second = ci_cache.run_validator(str(tmp_path), "cache-smoke")
    assert second["decision"] == "pass"
    assert second["reused"] == 1


def test_workflow_cache_jobs_reference_heavy_validators() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8"))
    validation = yaml.safe_load((ROOT / ".code-flow" / "validation.yml").read_text(encoding="utf-8"))
    heavy = {str(item["name"]) for item in validation["validators"] if item.get("heavy") is True}

    shards = workflow["jobs"]["backend-tests"]["strategy"]["matrix"]["shard"]
    assert {str(item["name"]) for item in shards} == {"fast", "runtime", "console", "worker"}
    assert {str(item["validator"]) for item in shards} <= heavy

    acceptance_runs = [
        str(step.get("run", ""))
        for step in workflow["jobs"]["acceptance-cache"]["steps"]
    ]
    assert any("ci_validation_cache.py" in run and "Pytest (acceptance)" in run
               for run in acceptance_runs), acceptance_runs
    assert "Pytest (acceptance)" in heavy

    merge_runs = [
        str(step.get("run", ""))
        for step in workflow["jobs"]["validation-cache-merge"]["steps"]
    ]
    assert any("merge_validation_cache.py" in run for run in merge_runs), merge_runs
    assert any("validation-cache-provenance.json" in run for run in merge_runs), merge_runs


def test_rebind_local_makes_ci_cache_reusable(tmp_path: Path) -> None:
    (tmp_path / ".code-flow").mkdir()
    (tmp_path / ".code-flow" / "validation.yml").write_text(
        "validators:\n"
        "  - name: cache-rebind\n"
        "    trigger: '**/*.py'\n"
        "    command: \"python3 -c \\\"print('ok')\\\"\"\n"
        "    heavy: true\n",
        encoding="utf-8",
    )
    (tmp_path / "sample.py").write_text("", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "sample.py")
    _git(tmp_path, "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "-q", "-m", "init")
    commit = subprocess.run(("git", "rev-parse", "HEAD"), cwd=tmp_path, check=True,
                            capture_output=True, text=True).stdout.strip()

    first = ci_cache.run_validator(str(tmp_path), "cache-rebind")
    assert first["decision"] == "pass"
    cache_file = tmp_path / ".code-flow" / ".validation-cache.json"
    cache = json.loads(cache_file.read_text())
    key = next(iter(cache))
    cache[key]["execution_identity"] = "ci-identity"
    cache_file.write_text(json.dumps(cache), encoding="utf-8")

    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({
        "run_url": "https://github.com/example/repo/actions/runs/1",
        "commit": commit,
    }), encoding="utf-8")
    assert merge_cache.main([
        str(cache_file), "--output", str(cache_file),
        "--rebind-local", "--provenance", str(provenance), "--root", str(tmp_path),
    ]) == 0

    rebound = json.loads(cache_file.read_text())[key]
    assert rebound["source"] == "ci"
    assert rebound["ci_execution_identity"] == "ci-identity"
    assert rebound["ci_run_url"].endswith("/actions/runs/1")
    assert rebound["ci_commit"] == commit

    second = ci_cache.run_validator(str(tmp_path), "cache-rebind")
    assert second["decision"] == "pass"
    assert second["reused"] == 1
