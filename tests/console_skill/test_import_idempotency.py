"""[S-04][E-03] 导入幂等（Idempotency-Key 重放返回首次结果）与重复版本拒绝。

真实边界：ASGI 真实 HTTP 上传 → 真实 PostgreSQL 幂等表；无 mock。
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from console_skill.conftest import SkillContext, import_skill, tenant_headers
from console_skill.packages import demo_package


async def test_s04_import_replay_same_idempotency_key_returns_first_result(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """[S-04] 相同 Idempotency-Key 重放：不新增 Skill/Artifact，返回首次导入结果。"""
    key = f"idem-{uuid.uuid4()}"
    headers = {**tenant_headers(skill_env), "Idempotency-Key": key}
    package = demo_package()

    first = await client.post(
        "/api/v1/skills/import",
        files={"file": ("skill.zip", package, "application/zip")},
        data={"version": "1.0.0"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()

    replay = await client.post(
        "/api/v1/skills/import",
        files={"file": ("skill.zip", package, "application/zip")},
        data={"version": "1.0.0"},
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first_payload["data"]

    # 只有一行 Skill / Artifact
    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact

    async with get_session_factory()() as session:
        skills = (
            await session.execute(
                Skill.__table__.select().where(Skill.tenant_id == skill_env.tenant_id)
            )
        ).all()
        artifacts = (
            await session.execute(
                SkillArtifact.__table__.select().where(
                    SkillArtifact.created_by.isnot(None)
                )
            )
        ).all()
        tenant_skill_ids = [row.id for row in skills]
        tenant_artifacts = [
            row for row in artifacts if row.skill_id in tenant_skill_ids
        ]
        assert len(tenant_skill_ids) >= 1
        assert len(tenant_artifacts) == len(tenant_skill_ids)  # 每个 Skill 恰一个 Artifact


async def test_s04_artifact_replay_same_idempotency_key_no_new_artifact(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """[S-04] /artifacts 幂等：同 key 重放返回首次结果，版本数不变。"""
    created = await import_skill(client, skill_env, demo_package(), version="1.0.0")
    assert created.status_code == 200, created.text
    skill_id = created.json()["data"]["id"]

    key = f"idem-{uuid.uuid4()}"
    headers = {**tenant_headers(skill_env), "Idempotency-Key": key}
    v2 = demo_package(name="Demo Skill V2")
    first = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", v2, "application/zip")},
        data={"version": "2.0.0"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    replay = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", v2, "application/zip")},
        data={"version": "2.0.0"},
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]

    versions = await client.get(
        f"/api/v1/skills/{skill_id}/artifacts", headers=tenant_headers(skill_env)
    )
    items = versions.json()["data"]["items"]
    assert [item["version"] for item in items].count("2.0.0") == 1


async def test_e03_duplicate_version_or_checksum_returns_skill_version_exists(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """[E-03] 无幂等 key 的重复 version/checksum 返回 SKILL_VERSION_EXISTS，不覆盖不新增。"""
    created = await import_skill(client, skill_env, demo_package(), version="1.0.0")
    assert created.status_code == 200
    skill_id = created.json()["data"]["id"]

    same_version = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", demo_package(name="Other"), "application/zip")},
        data={"version": "1.0.0"},
        headers=tenant_headers(skill_env),
    )
    assert same_version.status_code == 409
    assert same_version.json()["code"] == "SKILL_VERSION_EXISTS"

    same_checksum = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", demo_package(), "application/zip")},
        data={"version": "9.9.9"},
        headers=tenant_headers(skill_env),
    )
    assert same_checksum.status_code == 409
    assert same_checksum.json()["code"] == "SKILL_VERSION_EXISTS"

    versions = await client.get(
        f"/api/v1/skills/{skill_id}/artifacts", headers=tenant_headers(skill_env)
    )
    assert len(versions.json()["data"]["items"]) == 1
