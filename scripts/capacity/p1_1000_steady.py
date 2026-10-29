"""1000 用户稳态诊断插件（本地 15 核 + 同机 PG/Redis，非 staging 验收）。

用法（在仓库根目录）：

    PYTHONPATH=scripts/capacity MUAD_PGBOUNCER=1 MUAD_PROD_CADENCE=1 \
    MUAD_MODEL_SLOTS=2000 MUAD_MODEL_KEYS=4 MUAD_LOOP_LAG=1 MUAD_GC_PROBE=1 \
    MUAD_PGSS_RESET=1 MUAD_SMOKE_METRICS_OUT=/tmp/1000.json \
    CAPACITY_LOAD_USERS=1000 MUAD_CAPACITY_PROFILE=/tmp/1000.yml \
    uv run pytest -q -s -p smoke_report -p p1_1000_steady \
      tests/acceptance/capacity/test_capacity.py::test_s01_thousand_users_admitted_visible_and_bounded

开关：

- `MUAD_PGBOUNCER=1`：四单元 `DATABASE_URL` 走 6432（PgBouncer）。
- `MUAD_PROD_CADENCE=1`：lease/heartbeat/reaper 用生产节拍。
- `MUAD_MODEL_SLOTS=N`：把模型槽抬到 N，并同步改测试的 `MODEL_MAX_INFLIGHT`
  （**诊断口径**；正式验收保持 seed 的 16）。
- `MUAD_MODEL_KEYS=K`：把总槽拆到 K 个凭据（本机甜点 4）。
- `MUAD_LOOP_LAG/MUAD_GC_PROBE/MUAD_DB_PROBE`：运行时诊断探针。
- `MUAD_PGSS_RESET=1`：每模块前重置本机 `pg_stat_statements`（需要 `local_postgres` 容器）。

**不要把本插件的数字写成生产容量结论**；staging E-09 仍使用正式 profile 与 seed 的
16 槽，不加载本插件。
"""
from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    from tests.acceptance.capacity.stack import CapacityStack

    pgbouncer = os.environ.get("MUAD_PGBOUNCER") == "1"
    prod_cadence = os.environ.get("MUAD_PROD_CADENCE") == "1"
    if pgbouncer or prod_cadence:
        original = CapacityStack._service_env_for

        def patched(
            self: CapacityStack, unit: str, index: int, port: int
        ) -> dict[str, str]:
            env = original(self, unit, index, port)
            if pgbouncer:
                env["DATABASE_URL"] = env["DATABASE_URL"].replace(":5432/", ":6432/")
            if prod_cadence and unit in ("agent-runtime", "agent-worker"):
                env["RUN_LEASE_SEC"] = "60"
                env["RUN_HEARTBEAT_SEC"] = "20"
                env["RUN_REAPER_INTERVAL_SEC"] = "30"
            if os.environ.get("MUAD_DB_PROBE") == "1" and unit == "agent-runtime":
                env["DB_PROBE_INTERVAL_MS"] = os.environ.get(
                    "MUAD_DB_PROBE_INTERVAL_MS", "50"
                )
            if os.environ.get("MUAD_LOOP_LAG") == "1" and unit == "agent-runtime":
                env["LOOP_LAG_INTERVAL_MS"] = os.environ.get(
                    "MUAD_LOOP_LAG_INTERVAL_MS", "10"
                )
            if os.environ.get("MUAD_GC_PROBE") == "1" and unit == "agent-runtime":
                env["GC_PROBE"] = "1"
            return env

        CapacityStack._service_env_for = patched  # type: ignore[method-assign]
    slots = os.environ.get("MUAD_MODEL_SLOTS")
    if slots:
        from tests.acceptance.capacity import test_capacity

        test_capacity.MODEL_MAX_INFLIGHT = int(slots)


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(
    fixturedef: Any, request: Any
) -> Generator[None, None, None]:
    outcome: Any = yield
    slots = os.environ.get("MUAD_MODEL_SLOTS")
    if not slots or fixturedef.argname != "capacity_module":
        return
    module = outcome.get_result()
    if module is None:
        return
    if os.environ.get("MUAD_DEBUG_ENV") == "1":
        from tests.acceptance.capacity import test_capacity as _tc
        from tests.acceptance.capacity.stack import read_process_environ

        runtime_env = _tc.SERVICE_ENV["agent-runtime"]
        print(
            "SERVICE-ENV",
            runtime_env.get("RUN_LEASE_SEC"),
            runtime_env.get("RUN_HEARTBEAT_SEC"),
        )
        inst = module.stack._instances["agent-runtime"][0]
        env = read_process_environ(inst.process.pid)
        print(
            "RUNTIME-ENV",
            "RUN_LEASE_SEC=", env.get("RUN_LEASE_SEC"),
            "RUN_HEARTBEAT_SEC=", env.get("RUN_HEARTBEAT_SEC"),
            "users=", os.environ.get("CAPACITY_LOAD_USERS"),
        )

    if os.environ.get("MUAD_PGSS_RESET") == "1":
        import subprocess

        subprocess.run(
            [
                "docker", "exec", "local_postgres", "psql", "-U", "mmuser",
                "-d", "muad", "-Atc", "select pg_stat_statements_reset();",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    _ensure_model_keys(module, int(slots), int(os.environ.get("MUAD_MODEL_KEYS", "1")))


def _ensure_model_keys(module: Any, total_slots: int, keys: int) -> None:
    """给同一 model 追加 credential+deployment，每个 key 一个容量行；总槽数不变。"""
    if keys < 1:
        raise ValueError("MUAD_MODEL_KEYS must be >= 1")
    import hashlib
    import uuid

    from muad_console_platform.infrastructure.models.control import (
        ModelCredential,
        ModelDeployment,
    )
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.acceptance.capacity.delivery_harness import PROBE_API_KEY
    from tests.acceptance.capacity.stack import run_async

    database_url = module.stack.database_url
    tenant_id = module.stack.tenant_id
    model_id = module.seeded.model_id
    probe_url = f"{module.stack.model_probe.url}/v1"
    per_key = total_slots // keys

    async def ensure() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                deployments = (
                    await session.scalars(
                        select(ModelDeployment).where(
                            ModelDeployment.tenant_id == tenant_id,
                            ModelDeployment.model_id == model_id,
                            ModelDeployment.is_deleted.is_(False),
                        )
                    )
                ).all()
                credential_ids = [row.credential_id for row in deployments]
                credentials = list(
                    (
                        await session.scalars(
                            select(ModelCredential).where(
                                ModelCredential.tenant_id == tenant_id,
                                ModelCredential.id.in_(credential_ids),
                            )
                        )
                    ).all()
                )
                while len(credentials) < keys:
                    api_key = f"{PROBE_API_KEY}-{uuid.uuid4().hex[:8]}"
                    credential = ModelCredential(
                        tenant_id=tenant_id,
                        key=f"delivery-cred-{uuid.uuid4().hex[:8]}",
                        provider_key="openai",
                        base_url=probe_url,
                        api_key=api_key,
                        api_key_digest=hashlib.sha256(api_key.encode()).hexdigest(),
                        max_inflight=per_key,
                    )
                    session.add(credential)
                    await session.flush()
                    session.add(
                        ModelDeployment(
                            tenant_id=tenant_id,
                            key=f"delivery-member-{uuid.uuid4().hex[:8]}",
                            model_id=model_id,
                            credential_id=credential.id,
                            weight=1,
                        )
                    )
                    credentials.append(credential)
                for credential in credentials:
                    credential.base_url = probe_url
                    credential.max_inflight = per_key
                await session.commit()
        finally:
            await engine.dispose()

    run_async(ensure)
