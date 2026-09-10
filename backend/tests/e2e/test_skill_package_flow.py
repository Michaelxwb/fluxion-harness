"""TASK-009（S-06，E2E）：Skill Package 上传 → 解析 → 发布 → Chat 生效。

真实边界：ZIP 解析器 + ArtifactStore 真落盘 + PG 真库 + wire 模型真跑完整
Agent Loop。断言：trace.snapshot 冻结 skill 版本 + artifact 引用 +
SKILL.md 指令进入系统提示。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from fluxion.plugins.artifact import LocalFileArtifactStore
from fluxion.plugins.model_provider import (
    ModelProviderRegistry,
    OpenAICompatibleHTTPModelProvider,
)
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest
from fluxion.services.skill_package_service import SkillPackageService
from tests.product_wire import openai_final_response, openai_wire_server
from tests.runtime_helpers import (
    TEST_POSTGRES_DSN,
    publish_resource,
    seed_model_definition,
)


def _package_zip() -> bytes:
    manifest = yaml.safe_dump(
        {
            "name": "helper",
            "version": "1",
            "description": "d",
            "required_capabilities": [],
            "knowledge": ["knowledge/faq.md"],
        }
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.yaml", manifest)
        archive.writestr("SKILL.md", "# Helper\nAlways greet with HELLO-PKG.")
        archive.writestr("knowledge/faq.md", "# FAQ\nA: B")
    return buf.getvalue()


def _model_registry(base_url: str) -> ModelProviderRegistry:
    registry = ModelProviderRegistry()
    registry.register(
        "wire",
        OpenAICompatibleHTTPModelProvider(
            provider_id="wire",
            api_base_url=base_url,
            model="fixture-model",
            timeout_seconds=3,
            max_retries=0,
        ),
    )
    return registry


@pytest.mark.asyncio
async def test_S06_package_to_chat(
    pg_store: RegistryStore, tmp_path: Path
) -> None:
    """S-06：Package 发布 → Agent 绑定 → Chat，指令与 artifact 双冻结生效。"""
    engine = create_async_engine(TEST_POSTGRES_DSN)
    artifacts = LocalFileArtifactStore(root=tmp_path / "artifacts", engine=engine)
    try:
        publication = await SkillPackageService(pg_store, artifacts).publish_package(
            tenant_id="tenant-a", data=_package_zip()
        )
        assert publication.skill_id == "helper"
        assert publication.version == "1"
        assert publication.artifact_uri.startswith("artifact://tenant-a/skills/")
        # artifact 真落盘可读回。
        stored = await artifacts.get(
            "tenant-a", "skills", "helper-1.zip"
        )
        assert len(stored) > 0

        await publish_resource(
            pg_store,
            tenant_id="tenant-a",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            spec={"max_rounds": 8, "default": True},
        )
        await seed_model_definition(pg_store, tenant_id="tenant-a", provider_id="wire")
        await publish_resource(
            pg_store,
            tenant_id="tenant-a",
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="1",
            spec={
                "name": "assistant",
                "system_prompt": "p",
                "owner": "builder",
                "model_policy": {
                    "primary_model_ref": {"id": "model.wire", "version": "1"}
                },
                "capabilities": [
                    {"capability_ref": "helper", "version_pin": "1", "type": "skill"}
                ],
            },
        )
        async with openai_wire_server(
            [openai_final_response("HELLO-PKG done")]
        ) as wire:
            runtime = RuntimeApplicationService(
                pg_store, model_providers=_model_registry(wire.base_url)
            )
            result = await runtime.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    agent_definition_id="assistant",
                    runtime_profile_id="assistant",
                    session_id="session-pkg",
                    input_message="hi",
                )
            )
        trace = await runtime.trace_store.get(result.trace_id)
        assert trace is not None
        assert trace.snapshot.skill_versions == {"helper": "1"}
        assert trace.snapshot.artifact_refs.get("helper") == publication.artifact_uri
        assert "HELLO-PKG" in trace.snapshot.skill_instructions.get("helper", "")
        assert result.output == "HELLO-PKG done"
    finally:
        await engine.dispose()
