from __future__ import annotations

from httpx import AsyncClient, Response
from tests.console_helpers import console_stack, create_resource, publish_resource, tenant_headers
from tests.runtime_helpers import publish_resource as publish_registry_resource

from fluxion.resources import ResourceKind, ResourceStatus


async def test_E_C104_invalid_dsl_and_capability_ref_block_publish() -> None:
    async with console_stack() as stack:
        invalid_spec: dict[str, object] = {
            "name": "invalid-workflow",
            "engine_ref": "workflow-engine://primary",
            "steps": "not-a-list",
        }
        unknown_ref_spec = _workflow_spec("skill:missing-capability@1")
        await create_resource(
            stack.client,
            kind=ResourceKind.WORKFLOW,
            resource_id="invalid-workflow",
            spec=invalid_spec,
        )
        await create_resource(
            stack.client,
            kind=ResourceKind.WORKFLOW,
            resource_id="unknown-ref-workflow",
            spec=unknown_ref_spec,
        )

        invalid_validation = await _validate(stack.client, "invalid-workflow")
        invalid_publish = await publish_resource(
            stack.client,
            kind=ResourceKind.WORKFLOW,
            resource_id="invalid-workflow",
        )
        unknown_validation = await _validate(stack.client, "unknown-ref-workflow")
        unknown_publish = await publish_resource(
            stack.client,
            kind=ResourceKind.WORKFLOW,
            resource_id="unknown-ref-workflow",
        )
        invalid = await stack.store.get(
            ResourceKind.WORKFLOW,
            "invalid-workflow",
            tenant_id="tenant-a",
            version="1",
        )
        unknown = await stack.store.get(
            ResourceKind.WORKFLOW,
            "unknown-ref-workflow",
            tenant_id="tenant-a",
            version="1",
        )

    assert invalid_validation.status_code == 400
    assert "steps" in invalid_validation.json()["message"]
    assert invalid_publish.status_code == 400
    assert unknown_validation.status_code == 400
    assert "skill:missing-capability@1" in unknown_validation.json()["message"]
    assert unknown_publish.status_code == 400
    assert invalid is not None and invalid.status is ResourceStatus.DRAFT
    assert unknown is not None and unknown.status is ResourceStatus.DRAFT


async def test_valid_workflow_validates_and_publishes_to_registry() -> None:
    async with console_stack() as stack:
        await publish_registry_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="report-source",
            version="1",
            spec={
                "name": "report-source",
                "description": "报表数据源",
                "capability_id": "cap.report.source",
                "parameters": {},
            },
        )
        await create_resource(
            stack.client,
            kind=ResourceKind.WORKFLOW,
            resource_id="weekly-report",
            spec=_workflow_spec("skill:report-source@1"),
        )

        validation = await _validate(stack.client, "weekly-report")
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.WORKFLOW,
            resource_id="weekly-report",
            request_id="req-workflow-publish",
        )
        versions = await stack.client.get(
            "/api/v1/resources/workflow/weekly-report/versions?page=1&page_size=20",
            headers=tenant_headers(request_id="req-workflow-versions"),
        )
        resource = await stack.store.get(
            ResourceKind.WORKFLOW,
            "weekly-report",
            tenant_id="tenant-a",
            version="1",
        )

    assert validation.status_code == 200
    assert validation.json()["data"] == {"diagnostics": ["校验通过"], "valid": True}
    assert published.status_code == 200
    assert versions.status_code == 200
    assert versions.json()["data"]["items"][0]["version"] == "1"
    assert versions.json()["data"]["total"] == 1
    assert resource is not None and resource.status is ResourceStatus.PUBLISHED


async def _validate(client: AsyncClient, resource_id: str) -> Response:
    return await client.post(
        f"/api/v1/resources/workflow/{resource_id}/versions/1:validate",
        json={},
        headers=tenant_headers(request_id=f"req-validate-{resource_id}"),
    )


def _workflow_spec(capability_ref: str) -> dict[str, object]:
    return {
        "name": "weekly-report",
        "description": "每周报表",
        "engine_ref": "workflow-engine://primary",
        "steps": [
            {
                "id": "collect",
                "capability_ref": capability_ref,
                "depends_on": [],
                "input": {"period": "last-week"},
            }
        ],
    }
