

from __future__ import annotations

import uuid

from httpx import AsyncClient

# ---- B-104（08 TASK-004）：Snapshot 非密钥 + 稳定 hash + 认证隔离 ----

def test_b104_snapshot_hash_stable_across_key_rotation() -> None:
    """[B-104] api_key 不参与 content_hash：仅密钥轮换时 hash 稳定。"""
    from muad_agent_runtime.application.run_service import _snapshot_hash
    from muad_contracts import (
        ResolvedAgent,
        ResolvedModel,
        ResolveDefinitionResponse,
    )

    agent_id, model_id = uuid.uuid4(), uuid.uuid4()

    def resolved(api_key: str) -> ResolveDefinitionResponse:
        return ResolveDefinitionResponse(
            agent=ResolvedAgent(id=agent_id, key="k", revision=1, instructions="i", runtime_config={}),
            model=ResolvedModel(
                id=model_id, revision=1, model_id="gpt-4o-mini",
                base_url="https://api.example.com/v1", api_key=api_key,
            ),
            skills=[],
        )

    assert _snapshot_hash(resolved("key-old"), {}) == _snapshot_hash(resolved("key-new"), {})


def test_b104_snapshot_model_json_excludes_api_key() -> None:
    """[B-104] Snapshot 落库的 model_json 不含 api_key（认证只走 API-09）。"""
    from muad_agent_runtime.application.run_service import _snapshot_model
    from muad_contracts import ResolvedModel

    dump = _snapshot_model(
        ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1", api_key="secret-value",
        )
    )
    assert "api_key" not in dump
    assert "secret-value" not in str(dump)
    assert dump["model_id"] == "gpt-4o-mini"  # 冻结的 model 标识保留
