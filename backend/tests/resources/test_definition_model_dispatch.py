"""TASK-003 / BE-S-06：全部 Resource kind 的 typed model 与 schema 分派。"""

from __future__ import annotations

import inspect

from pydantic import BaseModel
from tests.console_helpers import console_stack, tenant_headers

from fluxion.resources import ResourceKind
from fluxion.services.console_resources import (
    ConsoleResourceOps,
    _definition_model,
)

_PRODUCT_KIND_VALUES = {
    "agent_definition",
    "model",
    "tool",
    "skill",
    "mcp",
    "runtime_profile",
    "secret",
    "policy",
}


async def test_be_s_06_each_resource_kind_exposes_typed_schema() -> None:
    """真实 API → Service → model registry 为每个 kind 返回可渲染 schema。"""
    enum_values = {kind.value for kind in ResourceKind}
    assert _PRODUCT_KIND_VALUES <= enum_values

    async with console_stack() as stack:
        for kind in ResourceKind:
            response = await stack.client.get(
                f"/api/v1/resources/{kind.value}/schema",
                headers=tenant_headers(request_id=f"req-be-s-06-{kind.value}"),
            )
            assert response.status_code == 200, kind.value
            payload = response.json()
            schema = payload["data"]["schema"]
            assert payload["code"] == 0
            assert payload["request_id"]
            assert schema["type"] == "object"
            assert isinstance(schema["properties"], dict)
            assert schema["additionalProperties"] is False


def test_rule_backend_quality_001_dispatch_is_frozen_and_typed() -> None:
    for kind in ResourceKind:
        model = _definition_model(kind)
        assert model is not None, kind.value
        assert issubclass(model, BaseModel)
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("frozen") is True

    schema_path_source = "\n".join(
        (
            inspect.getsource(_definition_model),
            inspect.getsource(ConsoleResourceOps.resource_schema),
        )
    )
    assert "spec_json" not in schema_path_source
    assert inspect.signature(_definition_model).return_annotation is not inspect.Signature.empty
