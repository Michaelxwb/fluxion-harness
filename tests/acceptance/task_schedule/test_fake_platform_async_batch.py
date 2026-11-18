"""[E2E] ASYNC fake-platform batch Skill through real Worker fan-out.

The Skill package under ``skills/fake-platform-async-batch`` is packed as a real
artifact. The parent receives an explicit target list + ``offline`` (the Worker
acceptance stack has no Console platform session), returns a BATCH plan, and the
Worker creates one child Task per item. Every child writes JSON+CSV files into
``SKILL_OUTPUT_DIR``; the parent fan-in aggregates all children.
"""

from __future__ import annotations

import hashlib
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
from muad_agent_runtime.application.task_client import WorkerTaskClient
from muad_contracts import SkillExecutionMode
from sqlalchemy import text

from .environment import INTERNAL_TOKEN, LiveStack, run_async, run_db
from .helpers import submission_context

ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = ROOT / "skills/fake-platform-async-batch"
TARGETS = ["alpha", "beta", "gamma"]


def _pack(live_stack: LiveStack) -> tuple[str, str, uuid.UUID]:
    storage_key = f"skills/{uuid.uuid4()}/v1/skill.zip"
    target = live_stack.artifact_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        for path in sorted(SKILL_DIR.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(SKILL_DIR)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            archive.write(path, relative.as_posix())
    checksum = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    return storage_key, checksum, uuid.uuid4()


def _submit(live_stack: LiveStack) -> str:
    storage_key, checksum, artifact_id = _pack(live_stack)
    context, resolved = submission_context(live_stack)
    original = resolved["skill"]
    skill = original.model_copy(
        update={
            "skill_id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "key": "fake-platform-async-batch",
            "name": "Fake platform async batch",
            "description": "worker acceptance for async batch + files",
            "version": "0.1.0",
            "checksum": checksum,
            "storage_key": storage_key,
            "execution_mode": SkillExecutionMode.ASYNC,
            "source": "ADMIN",
        }
    )
    context = replace(context, skills=(skill,))

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context,
                call_id=f"fake-platform-batch-{uuid.uuid4()}",
                skill=skill,
                input_data={
                    "targets": TARGETS,
                    "max_concurrency": 2,
                    "aggregate_mode": "BEST_EFFORT",
                    "offline": True,
                },
            )
        finally:
            await client.aclose()

    return str(run_async(submit)["task_id"])


def _rows(live_stack: LiveStack, task_id: str) -> tuple[Any, ...]:
    async def query(factory: Any) -> Any:
        async with factory() as session:
            parent = (
                await session.execute(
                    text(
                        "SELECT status, task_type, result_json, error_code, error_message "
                        "FROM task.task_execution WHERE id = :id"
                    ),
                    {"id": uuid.UUID(task_id)},
                )
            ).one()
            children = list(
                (
                    await session.execute(
                        text(
                            "SELECT id, status, item_key, input_json, result_json, "
                            "error_code, error_message "
                            "FROM task.task_execution WHERE parent_id = :id ORDER BY item_key"
                        ),
                        {"id": uuid.UUID(task_id)},
                    )
                ).all()
            )
            return parent, children

    return cast(tuple[Any, ...], run_db(query))


def _file_entries(result_json: Any) -> list[dict[str, Any]]:
    if not isinstance(result_json, dict):
        return []
    files = result_json.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def test_async_fake_platform_batch_fans_out_and_writes_files(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    task_id = _submit(live_stack)

    def parent_terminal() -> bool:
        parent, _ = _rows(live_stack, task_id)
        return parent[0] in {"COMPLETED", "FAILED", "CANCELLED"}

    deadline = time.monotonic() + 120
    while not parent_terminal() and time.monotonic() < deadline:
        time.sleep(1.0)
    parent, children = _rows(live_stack, task_id)
    assert parent[0] == "COMPLETED", (parent, children)
    assert parent[1] == "BATCH"
    assert parent[2]["total"] == 3 and parent[2]["succeeded"] == 3, parent[2]

    assert len(children) == 3
    by_target = {}
    for child in children:
        assert child[1] == "COMPLETED", child
        assert child[2], "child item_key is deterministic"
        assert child[3]["target"] in TARGETS, child[3]
        by_target[child[3]["target"]] = child

    for target in TARGETS:
        child = by_target[target]
        files = _file_entries(child[4])
        names = {str(item.get("relative_path")) for item in files}
        assert names == {f"report-{target}.json", f"report-{target}.csv"}, child[4]
        for item in files:
            assert item["size"] > 0
            assert item["checksum"].startswith("sha256:")
