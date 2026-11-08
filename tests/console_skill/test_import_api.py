import hashlib
import json
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings
from muad_console_platform.api import skills as skills_api
from muad_console_platform.infrastructure import skill_validator
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from muad_console_platform.main import app
from sqlalchemy import func, select

from console_skill.conftest import SkillContext, import_skill, tenant_headers
from console_skill.packages import (
    demo_package,
    many_entries_package,
    skill_md,
    symlink_package,
    zip_bytes,
)


@pytest.mark.parametrize(
    ("package", "reason"),
    [
        (zip_bytes({"scripts/run.py": "print('no skill md')\n"}), "SKILL_MD_MISSING"),
        (zip_bytes({"SKILL.md": "# no frontmatter\n"}), "MANIFEST_INVALID"),
        (zip_bytes({"SKILL.md": "---\nname: Only Name\n---\n"}), "MANIFEST_INVALID"),
        (
            zip_bytes({"SKILL.md": skill_md(), "../evil.py": "print('traversal')\n"}),
            "PATH_INVALID",
        ),
        (
            zip_bytes({"SKILL.md": skill_md(), "/etc/evil.py": "print('absolute')\n"}),
            "PATH_INVALID",
        ),
        (symlink_package(), "PATH_INVALID"),
        (zip_bytes({"SKILL.md": skill_md(), "run.sh": "echo nope\n"}), "FILE_TYPE_UNSUPPORTED"),
        (zip_bytes({"SKILL.md": skill_md(), "inner.zip": b"PK\x03\x04"}), "NESTED_ARCHIVE"),
        (zip_bytes({"SKILL.md": skill_md(), "payload.txt": demo_package()}), "NESTED_ARCHIVE"),
        (b"this is not a zip archive", "INVALID_ZIP"),
        (
            demo_package(extra_files={"scripts/leak.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'}),
            "SECRET_DETECTED",
        ),
    ],
)
async def test_invalid_packages_return_400(
    client: AsyncClient,
    skill_env: SkillContext,
    package: bytes,
    reason: str,
) -> None:
    artifact_root = Path(SharedSettings().artifact_root)
    artifacts_before = set(artifact_root.glob("skills/*/*/skill.zip"))
    async with get_session_factory()() as session:
        skills_before = await session.scalar(
            select(func.count()).select_from(Skill).where(Skill.tenant_id == skill_env.tenant_id)
        )
        artifacts_before_count = await session.scalar(
            select(func.count())
            .select_from(SkillArtifact)
            .join(Skill, Skill.id == SkillArtifact.skill_id)
            .where(Skill.tenant_id == skill_env.tenant_id)
        )

    response = await import_skill(client, skill_env, package)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "SKILL_PACKAGE_INVALID"
    assert body["data"]["reason"] == reason

    async with get_session_factory()() as session:
        skills_after = await session.scalar(
            select(func.count()).select_from(Skill).where(Skill.tenant_id == skill_env.tenant_id)
        )
        artifacts_after = await session.scalar(
            select(func.count())
            .select_from(SkillArtifact)
            .join(Skill, Skill.id == SkillArtifact.skill_id)
            .where(Skill.tenant_id == skill_env.tenant_id)
        )
    assert skills_after == skills_before, "非法包不得写入 skill 行"
    assert artifacts_after == artifacts_before_count, "非法包不得写入 artifact 行"
    assert set(artifact_root.glob("skills/*/*/skill.zip")) == artifacts_before, (
        "非法包不得写入 NFS Artifact"
    )


async def test_e04_secret_hit_is_rejected_without_log_leak_or_write(
    client: AsyncClient,
    skill_env: SkillContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    package = demo_package(extra_files={"scripts/leak.py": f'AWS_KEY = "{secret}"\n'})
    artifact_root = Path(SharedSettings().artifact_root)
    artifacts_before = set(artifact_root.glob("skills/*/*/skill.zip"))
    with caplog.at_level("DEBUG"):
        response = await import_skill(client, skill_env, package)
    assert response.status_code == 400
    assert response.json()["code"] == "SKILL_PACKAGE_INVALID"
    assert secret not in caplog.text, "Secret 明文不得进入日志"
    assert secret not in response.text
    assert set(artifact_root.glob("skills/*/*/skill.zip")) == artifacts_before


@pytest.mark.parametrize("platforms", [[], ["mssp"], ["mssp", "mssw"]])
async def test_platform_declarations_persist_and_are_exposed(
    client: AsyncClient, skill_env: SkillContext, platforms: list[str]
) -> None:
    package = zip_bytes({
        "SKILL.md": "---\nname: platform-display\ndescription: example\n"
        f"platforms: {json.dumps(platforms)}\n---\n",
        "scripts/run.py": "print('{}')\n",
    })
    response = await import_skill(client, skill_env, package)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["platforms"] == platforms
    assert data["current_artifact"]["platforms"] == platforms
    async with get_session_factory()() as session:
        stored = await session.get(SkillArtifact, uuid.UUID(data["current_artifact_id"]))
        assert stored is not None
        assert stored.frontmatter_json.get("platforms", []) == platforms
    listing = await client.get("/api/v1/skills", params={"keyword": "platform-display"})
    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["platforms"] == platforms
    artifact = await client.get(f"/api/v1/skills/{data['id']}/artifacts/{data['current_artifact_id']}")
    assert artifact.status_code == 200
    assert artifact.json()["data"]["platforms"] == platforms


async def test_import_derives_version_from_manifest_when_omitted(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """导入不再要求手填版本：优先用包内 muad.skill.json 的 version。"""
    package = demo_package(
        name="manifest-version",
        extra_files={"muad.skill.json": '{"version": "2.3.4"}'},
    )
    response = await import_skill(client, skill_env, package, version=None)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["current_version"] == "2.3.4"


async def test_import_defaults_version_when_manifest_has_none(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """manifest 没写 version 时落到稳定默认值，导入不因缺字段失败。"""
    response = await import_skill(client, skill_env, demo_package(name="no-version"), version=None)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["current_version"] == "1.0.0"


async def test_platform_display_follows_current_version_without_overwriting_history(
    client: AsyncClient, skill_env: SkillContext
) -> None:
    first = await import_skill(client, skill_env, demo_package(name="version-platforms"))
    assert first.status_code == 200
    initial = first.json()["data"]
    skill_id, artifact_id = initial["id"], initial["current_artifact_id"]
    package = zip_bytes({
        "SKILL.md": "---\nname: version-platforms\ndescription: next\nplatforms: [mssp, mssw]\n---\n",
        "scripts/run.py": "print('{}')\n",
    })
    changed = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts", files={"file": ("skill.zip", package, "application/zip")},
        data={"version": "2.0.0"}, headers=tenant_headers(skill_env),
    )
    assert changed.status_code == 200
    current = await client.get(f"/api/v1/skills/{skill_id}")
    assert current.status_code == 200
    assert current.json()["data"]["platforms"] == ["mssp", "mssw"]
    historical = await client.get(f"/api/v1/skills/{skill_id}/artifacts/{artifact_id}")
    assert historical.status_code == 200
    assert historical.json()["data"]["platforms"] == []


async def test_import_creates_skill_and_artifact(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = demo_package(name="Imported Skill", description="Imported description.")
    response = await import_skill(client, skill_env, package, version="1.0.0")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["key"] == "imported-skill"
    assert data["name"] == "Imported Skill"
    assert data["description"] == "Imported description."
    assert data["user_scope"] == "SELECTED"
    assert data["enabled"] is True
    assert data["current_version"] == "1.0.0"
    assert data["execution_mode"] == "SYNC"
    assert data["agent_count"] == 0
    assert data["user_count"] == 0
    assert data["platforms"] == []

    artifact = data["current_artifact"]
    assert artifact["artifact_id"] == data["current_artifact_id"]
    assert artifact["version"] == "1.0.0"
    assert artifact["checksum"] == "sha256:" + hashlib.sha256(package).hexdigest()
    assert artifact["validation_status"] == "READY"
    assert artifact["package_size"] == len(package)
    assert artifact["created_by"] == str(skill_env.admin_id)
    assert artifact["platforms"] == []
    assert artifact["frontmatter"] == {
        "name": "Imported Skill",
        "description": "Imported description.",
        "execution": "SYNC",
        "platform_label": None,
    }
    manifest_paths = {entry["path"] for entry in artifact["manifest"]["files"]}
    assert manifest_paths == {"SKILL.md", "scripts/run.py", "references/notes.md"}

    target = Path(SharedSettings().artifact_root) / artifact["storage_key"]
    assert target.is_file()
    assert target.read_bytes() == package

    async with get_session_factory()() as session:
        skill = await session.get(Skill, uuid.UUID(data["id"]))
        assert skill is not None
        assert skill.tenant_id == skill_env.tenant_id
        assert skill.key == "imported-skill"
        stored = await session.get(SkillArtifact, uuid.UUID(artifact["artifact_id"]))
        assert stored is not None
        assert stored.checksum == artifact["checksum"]
        assert stored.storage_key == artifact["storage_key"]


async def test_import_derives_key_from_inner_directory(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = demo_package(name="Inner Skill", top_dir="inner-dir")
    response = await import_skill(client, skill_env, package, version="1.0.0")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["key"] == "inner-dir"
    manifest_paths = {entry["path"] for entry in data["current_artifact"]["manifest"]["files"]}
    assert manifest_paths == {
        "inner-dir/SKILL.md",
        "inner-dir/scripts/run.py",
        "inner-dir/references/notes.md",
    }


async def test_import_uses_form_key_default_script_and_execution(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = demo_package(name="Async Skill", execution="async", platform_label="WECOM")
    response = await import_skill(
        client,
        skill_env,
        package,
        version="2.1.0",
        key="custom-key",
        default_script="scripts/run.py",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["key"] == "custom-key"
    assert data["platform_label"] == "WECOM"
    assert data["execution_mode"] == "ASYNC"
    assert data["current_artifact"]["default_script"] == "scripts/run.py"
    assert data["current_artifact"]["frontmatter"]["execution"] == "ASYNC"


async def test_import_honors_user_scope_form_field(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    all_scope = await import_skill(
        client,
        skill_env,
        demo_package(name="All Scope Skill"),
        version="1.0.0",
        key="all-scope-key",
        user_scope="ALL",
    )
    assert all_scope.status_code == 200, all_scope.text
    assert all_scope.json()["data"]["user_scope"] == "ALL"

    default_scope = await import_skill(
        client,
        skill_env,
        demo_package(name="Default Scope Skill"),
        version="1.0.0",
        key="default-scope-key",
    )
    assert default_scope.status_code == 200
    assert default_scope.json()["data"]["user_scope"] == "SELECTED"

    async with get_session_factory()() as session:
        skill = await session.scalar(
            select(Skill).where(
                Skill.tenant_id == skill_env.tenant_id, Skill.key == "all-scope-key"
            )
        )
    assert skill is not None and skill.user_scope == "ALL"


async def test_import_zip_over_size_limit_returns_400(
    client: AsyncClient,
    skill_env: SkillContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(skills_api, "ZIP_BYTES_LIMIT", 64)
    response = await import_skill(client, skill_env, demo_package())
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "SKILL_PACKAGE_INVALID"
    assert body["data"] == {"reason": "PACKAGE_TOO_LARGE", "limit": "64 B"}


async def test_import_unpacked_size_over_limit_returns_400(
    client: AsyncClient,
    skill_env: SkillContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(skill_validator, "UNPACKED_BYTES_LIMIT", 32)
    response = await import_skill(client, skill_env, demo_package())
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "SKILL_PACKAGE_INVALID"
    assert body["data"] == {"reason": "UNPACKED_TOO_LARGE", "limit": "32 B"}


async def test_import_too_many_entries_returns_400(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = many_entries_package(skill_validator.ENTRY_LIMIT + 1)
    response = await import_skill(client, skill_env, package)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "SKILL_PACKAGE_INVALID"
    assert body["data"]["reason"] == "TOO_MANY_FILES"
    assert body["data"]["limit"] == str(skill_validator.ENTRY_LIMIT)


async def test_import_invalid_default_script_returns_400(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    response = await import_skill(
        client,
        skill_env,
        demo_package(),
        default_script="scripts/missing.py",
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "SKILL_PACKAGE_INVALID"
    assert body["data"] == {
        "reason": "DEFAULT_SCRIPT_INVALID",
        "path": "scripts/missing.py",
    }


async def test_import_without_a_derivable_key_returns_actionable_reason(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    response = await import_skill(
        client,
        skill_env,
        demo_package(name="中文技能"),
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "SKILL_PACKAGE_INVALID"
    assert body["data"] == {"reason": "SKILL_KEY_MISSING"}


async def test_import_duplicate_key_returns_conflict(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = demo_package()
    first = await import_skill(client, skill_env, package, version="1.0.0", key="dup-key")
    assert first.status_code == 200

    same_version = await import_skill(client, skill_env, package, version="1.0.0", key="dup-key")
    assert same_version.status_code == 409
    assert same_version.json()["code"] == "SKILL_VERSION_EXISTS"

    new_version = await import_skill(
        client,
        skill_env,
        demo_package(extra_files={"references/notes.md": "# Changed\n"}),
        version="2.0.0",
        key="dup-key",
    )
    assert new_version.status_code == 409
    assert new_version.json()["code"] == "SKILL_KEY_EXISTS"
    assert "dup-key" in new_version.json()["msg"], "专用码必须带出冲突的 key"


async def test_import_duplicate_checksum_returns_conflict(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    package = demo_package()
    first = await import_skill(client, skill_env, package, version="1.0.0", key="checksum-key")
    assert first.status_code == 200
    duplicate = await import_skill(client, skill_env, package, version="1.1.0", key="checksum-key")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "SKILL_VERSION_EXISTS"


async def test_import_requires_authentication(skill_env: SkillContext) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        response = await anon.get("/api/v1/skills", headers=tenant_headers(skill_env))
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_system_import_forces_all_user_scope(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """系统 Skill 全局生效：即使用户范围传 SELECTED，服务端也强制 ALL。"""
    response = await import_skill(
        client,
        skill_env,
        demo_package(name="System Global"),
        version=None,
        source="SYSTEM",
        user_scope="SELECTED",
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["user_scope"] == "ALL"
