from __future__ import annotations

from dataclasses import dataclass

from fluxion.registry import AuditRecord, ChatAccessRecord, PlatformUserRecord
from fluxion.resources import ResourceBinding, ResourceDefinition
from fluxion.runtime.secrets import SecretMetadata
from fluxion.runtime.tracing import TraceRecord
from fluxion.services.console_contracts import (
    ApprovalRecordView,
    PublishResourceResult,
)
from fluxion.services.runtime_contracts import PluginSummary


@dataclass(frozen=True, slots=True)
class IssuedChatAccess:
    record: ChatAccessRecord
    token: str


def resource_payload(resource: ResourceDefinition) -> dict[str, object]:
    return {
        "resource_type": resource.kind.value,
        "resource_id": resource.id,
        "tenant_id": resource.tenant_id,
        "version": resource.version,
        "status": resource.status.value,
        "visibility": resource.visibility.value,
        "spec": resource.spec_json,
        "updated_at": (resource.published_at or resource.created_at).isoformat(),
    }


def binding_payload(binding: ResourceBinding) -> dict[str, object]:
    return {
        "binding_id": binding.binding_id,
        "tenant_id": binding.tenant_id,
        "subject_type": str(binding.subject_type),
        "subject_id": binding.subject_id,
        "resource_type": binding.resource_type.value,
        "resource_id": binding.resource_id,
        "version_selector": binding.resource_version_selector,
        "credential_ref": binding.credential_ref,
        "config": binding.config_json or {},
        "enabled": binding.enabled,
    }


def platform_user_payload(user: PlatformUserRecord) -> dict[str, object]:
    return {
        "tenant_id": user.tenant_id,
        "platform_user_id": user.platform_user_id,
        "display_name": user.display_name,
        "created_at": user.created_at.isoformat(),
    }


def policy_payload(resource: ResourceDefinition) -> dict[str, object]:
    spec = resource.spec_json or {}
    return {
        "policy_id": resource.id,
        "name": spec.get("name", resource.id),
        "version": resource.version,
        "status": resource.status.value,
        "visibility": resource.visibility.value,
        "allowed_tools": spec.get("allowed_tools", []),
        "denied_tools": spec.get("denied_tools", []),
    }


def _capability_payload(summary: PluginSummary) -> dict[str, object]:
    return {
        "capability_id": f"model.{summary.plugin_id}",
        "kind": summary.plugin_type,
        "version": "1",
        "provider_id": summary.plugin_id,
        "status": "loaded",
    }


def issued_chat_access_payload(issued: IssuedChatAccess) -> dict[str, object]:
    record = issued.record
    return {
        "access_id": record.access_id,
        "tenant_id": record.tenant_id,
        "platform_user_id": record.platform_user_id,
        "agent_id": record.agent_id,
        "token": issued.token,
        "chat_path": f"/chat/#/{issued.token}",
        "created_at": record.created_at.isoformat(),
    }


def trace_payload(trace: TraceRecord) -> dict[str, object]:
    snapshot = trace.snapshot
    return {
        "trace_id": trace.trace_id,
        "execution_id": trace.execution_id,
        "tenant_id": trace.tenant_id,
        "user_id": snapshot.user_id,
        "runtime_profile": {
            "id": snapshot.runtime_profile_id,
            "version": snapshot.runtime_profile_version,
        },
        "agent_definition": (
            {
                "id": snapshot.agent_definition_id,
                "version": snapshot.agent_definition_version,
            }
            if snapshot.agent_definition_id
            else None
        ),
        "skills": snapshot.skill_versions,
        "mcps": snapshot.mcp_versions,
        "plugins": snapshot.plugin_versions,
        "policy_version": snapshot.policy_version,
        "tools": list(trace.tools),
        "error": trace.error,
        "latency_ms": trace.latency_ms,
    }


def credential_payload(metadata: SecretMetadata) -> dict[str, object]:
    return {
        "credential_ref": metadata.ref,
        "provider": metadata.provider,
        "status": "disabled" if metadata.revoked else "active",
        "version": metadata.version,
        "last_rotated_at": metadata.created_at.isoformat(),
    }


def run_payload(trace: TraceRecord) -> dict[str, object]:
    snapshot = trace.snapshot
    started_at = snapshot.created_at.isoformat()
    policies = []
    if snapshot.policy_version is not None:
        policies.append({"id": "tenant-policy", "version": snapshot.policy_version})
    return {
        "execution_id": trace.execution_id,
        "trace_id": trace.trace_id,
        "status": "failed" if trace.error is not None else "succeeded",
        "started_at": started_at,
        "snapshot": {
            "runtime_profile": {
                # TASK-A105：机械替换误伤点还原——该键语义为执行 mechanics 版本。
                "id": snapshot.runtime_profile_id,
                "version": snapshot.runtime_profile_version,
            },
            "skills": _version_refs(snapshot.skill_versions),
            "mcps": _version_refs(snapshot.mcp_versions),
            "plugins": _version_refs(snapshot.plugin_versions),
            "policies": policies,
        },
        "trace_events": [
            {
                "id": f"{trace.trace_id}:{index}",
                "event": event.name,
                "at": started_at,
            }
            for index, event in enumerate(trace.events)
        ],
    }


def audit_payload(record: AuditRecord) -> dict[str, object]:
    return {
        "id": record.audit_id,
        "action": record.action,
        "actor_id": record.actor_id,
        "resource_id": record.target_id,
        "resource_version": _audit_version(record),
        "at": record.created_at.isoformat() if record.created_at is not None else "",
    }


def publish_payload(result: PublishResourceResult) -> dict[str, object]:
    return {
        "resource_id": result.resource_id,
        "version": result.version,
        "status": result.status,
        "publish_id": result.publish_id,
        "event_status": result.event_status,
        "kubernetes_workload_created": result.kubernetes_workload_created,
    }


def approval_payload(record: ApprovalRecordView) -> dict[str, object]:
    return {
        "approval_id": record.approval_id,
        "tenant_id": record.tenant_id,
        "kind": record.kind,
        "resource_id": record.resource_id,
        "target_version": record.target_version,
        "operation": record.operation,
        "status": record.status,
        "requester_actor_id": record.requester_actor_id,
        "approver_actor_id": record.approver_actor_id,
        "reason": record.reason,
        "expires_at": record.expires_at.isoformat(),
        "created_at": record.created_at.isoformat(),
        "decided_at": record.decided_at.isoformat() if record.decided_at else None,
    }


def _version_refs(versions: dict[str, str]) -> list[dict[str, str]]:
    return [{"id": resource_id, "version": version} for resource_id, version in versions.items()]


def _audit_version(record: AuditRecord) -> str:
    for payload in (record.after, record.before):
        if payload is not None and isinstance(payload.get("version"), str):
            return str(payload["version"])
    return record.publish_id or ""
