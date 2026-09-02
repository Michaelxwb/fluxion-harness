import { isRecord } from "@fluxion/shared";

import type {
  AuditRecord,
  BindingRecord,
  ControlPlaneItem,
  CredentialMetadata,
  EvalRunSummary,
  EvalSetSummary,
  IssuedChatAccess,
  JsonRecord,
  JsonSchemaNode,
  JsonValue,
  PageData,
  PlatformUser,
  PublishResult,
  ResourceStatus,
  ResourceSummary,
  ResourceType,
  ResourceVersion,
  ResourceVisibility,
  RunDetail,
  ValidationResult
} from "../types/console";

export function parseResourcePage(value: unknown): PageData<ResourceVersion> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseResource) };
}

export function parseBindingPage(value: unknown): PageData<BindingRecord> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseBinding) };
}

export function parsePlatformUserPage(value: unknown): PageData<PlatformUser> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parsePlatformUser) };
}

export function parseCredentialPage(value: unknown): PageData<CredentialMetadata> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseCredential) };
}

export function parseRunPage(value: unknown): PageData<RunDetail> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseRun) };
}

export function parseAuditPage(value: unknown): PageData<AuditRecord> {
  const page = parsePage(value);
  return { ...page, items: page.items.map(parseAudit) };
}

// ---- Phase 5 TASK-006：Eval 实页解析（与后端 /api/v1/admin/evals* envelope 契约）----

export function parseEvalSets(value: unknown): readonly EvalSetSummary[] {
  const record = requiredRecord(value, "eval_sets");
  if (!Array.isArray(record.items)) throw new Error("eval_sets.items 无效");
  return record.items.map(parseEvalSetItem);
}

function parseEvalSetItem(value: unknown): EvalSetSummary {
  const record = requiredRecord(value, "eval_set");
  return {
    id: requiredString(record.id, "eval_set.id"),
    name: requiredString(record.name, "eval_set.name"),
    version: requiredString(record.version, "eval_set.version"),
    status: requiredString(record.status, "eval_set.status"),
    caseCount: requiredNumber(record.case_count, "eval_set.case_count")
  };
}

export function parseEvalRuns(value: unknown): readonly EvalRunSummary[] {
  const record = requiredRecord(value, "eval_runs");
  if (!Array.isArray(record.items)) throw new Error("eval_runs.items 无效");
  return record.items.map(parseEvalRun);
}

export function parseEvalRun(value: unknown): EvalRunSummary {
  const record = requiredRecord(value, "eval_run");
  return {
    runId: requiredString(record.run_id, "eval_run.run_id"),
    evalSetId: requiredString(record.eval_set_id, "eval_run.eval_set_id"),
    evalSetVersion: requiredString(record.eval_set_version, "eval_run.eval_set_version"),
    score: requiredNumber(record.score, "eval_run.score"),
    passed: requiredBoolean(record.passed, "eval_run.passed"),
    traceId: requiredString(record.trace_id, "eval_run.trace_id"),
    createdAt: requiredString(record.created_at, "eval_run.created_at")
  };
}

export function parsePolicyList(value: unknown): readonly ControlPlaneItem[] {
  const record = requiredRecord(value, "policies");
  if (!Array.isArray(record.items)) throw new Error("policies.items 无效");
  return record.items.map(parsePolicyItem);
}

function parsePolicyItem(value: unknown): ControlPlaneItem {
  const record = requiredRecord(value, "policy");
  const allowed = Array.isArray(record.allowed_tools) ? record.allowed_tools.length : 0;
  return {
    id: requiredString(record.policy_id, "policy.policy_id"),
    name: requiredString(record.name, "policy.name"),
    status: requiredString(record.status, "policy.status"),
    detail: `v${requiredString(record.version, "policy.version")} · allowed_tools=${allowed}`
  };
}

export function parseCapabilityList(value: unknown): readonly ControlPlaneItem[] {
  const record = requiredRecord(value, "capabilities");
  if (!Array.isArray(record.items)) throw new Error("capabilities.items 无效");
  return record.items.map(parseCapabilityItem);
}

function parseCapabilityItem(value: unknown): ControlPlaneItem {
  const record = requiredRecord(value, "capability");
  return {
    id: requiredString(record.capability_id, "capability.capability_id"),
    name: requiredString(record.capability_id, "capability.capability_id"),
    status: requiredString(record.status, "capability.status"),
    detail:
      `kind=${requiredString(record.kind, "capability.kind")}` +
      ` · provider=${requiredString(record.provider_id, "capability.provider_id")}`
  };
}

function parsePage(value: unknown): { items: unknown[]; page: number; pageSize: number; total: number } {
  const record = requiredRecord(value, "page");
  if (!Array.isArray(record.items)) throw new Error("API page items 无效");
  return {
    items: record.items,
    page: requiredNumber(record.page, "page"),
    pageSize: requiredNumber(record.page_size, "page_size"),
    total: requiredNumber(record.total, "total")
  };
}

export function parseResource(value: unknown): ResourceVersion {
  const record = requiredRecord(value, "resource");
  return {
    resourceId: requiredString(record.resource_id, "resource_id"),
    resourceType: requiredResourceType(record.resource_type),
    spec: requiredJsonRecord(record.spec, "spec"),
    status: requiredStatus(record.status),
    tenantId: requiredString(record.tenant_id, "tenant_id"),
    updatedAt: optionalString(record.updated_at) ?? "",
    version: requiredString(record.version, "version"),
    visibility: requiredVisibility(record.visibility)
  };
}

export function toResourceSummary(resource: ResourceVersion): ResourceSummary {
  const displayName = optionalString(resource.spec.display_name) ?? optionalString(resource.spec.name);
  return {
    currentVersion: resource.version,
    displayName: displayName ?? resource.resourceId,
    resourceId: resource.resourceId,
    resourceType: resource.resourceType,
    status: resource.status,
    updatedAt: resource.updatedAt,
    visibility: resource.visibility
  };
}

export function parseBinding(value: unknown): BindingRecord {
  const record = requiredRecord(value, "binding");
  return {
    bindingId: requiredString(record.binding_id, "binding_id"),
    credentialRef: optionalString(record.credential_ref),
    enabled: requiredBoolean(record.enabled, "enabled"),
    resourceId: requiredString(record.resource_id, "resource_id"),
    resourceType: requiredResourceType(record.resource_type),
    subjectId: requiredString(record.subject_id, "subject_id"),
    subjectType: requiredSubjectType(record.subject_type),
    tenantId: requiredString(record.tenant_id, "tenant_id"),
    versionSelector: requiredString(record.version_selector, "version_selector")
  };
}

export function parsePlatformUser(value: unknown): PlatformUser {
  const record = requiredRecord(value, "platform_user");
  return {
    createdAt: requiredString(record.created_at, "created_at"),
    displayName: requiredString(record.display_name, "display_name"),
    platformUserId: requiredString(record.platform_user_id, "platform_user_id")
  };
}

export function parseIssuedChatAccess(value: unknown): IssuedChatAccess {
  const record = requiredRecord(value, "chat_access");
  return {
    accessId: requiredString(record.access_id, "access_id"),
    chatPath: requiredString(record.chat_path, "chat_path"),
    createdAt: requiredString(record.created_at, "created_at"),
    platformUserId: requiredString(record.platform_user_id, "platform_user_id"),
    agentId: requiredString(record.agent_id, "agent_id"),
    token: requiredString(record.token, "token")
  };
}

function parseCredential(value: unknown): CredentialMetadata {
  const record = requiredRecord(value, "credential");
  const status = requiredString(record.status, "status");
  if (status !== "active" && status !== "rotating" && status !== "disabled") {
    throw new Error("credential status 无效");
  }
  return {
    credentialRef: requiredString(record.credential_ref, "credential_ref"),
    lastRotatedAt: requiredString(record.last_rotated_at, "last_rotated_at"),
    provider: requiredString(record.provider, "provider"),
    status
  };
}

function parseRun(value: unknown): RunDetail {
  const record = requiredRecord(value, "run");
  const snapshot = requiredRecord(record.snapshot, "snapshot");
  const status = requiredString(record.status, "status");
  if (status !== "running" && status !== "succeeded" && status !== "failed") {
    throw new Error("run status 无效");
  }
  if (!Array.isArray(record.trace_events)) throw new Error("trace_events 无效");
  return {
    executionId: requiredString(record.execution_id, "execution_id"),
    snapshot: {
      mcps: parseVersionRefs(snapshot.mcps, "mcps"),
      plugins: parseVersionRefs(snapshot.plugins, "plugins"),
      policies: parseVersionRefs(snapshot.policies, "policies"),
      runtimeProfile: parseVersionRef(snapshot.runtime_profile, "runtime_profile"),
      skills: parseVersionRefs(snapshot.skills, "skills")
    },
    startedAt: requiredString(record.started_at, "started_at"),
    status,
    traceEvents: record.trace_events.map(parseTraceEvent)
  };
}

function parseVersionRefs(value: unknown, field: string) {
  if (!Array.isArray(value)) throw new Error(`${field} 无效`);
  return value.map((item) => parseVersionRef(item, field));
}

function parseVersionRef(value: unknown, field: string) {
  const record = requiredRecord(value, field);
  return {
    id: requiredString(record.id, `${field}.id`),
    version: requiredString(record.version, `${field}.version`)
  };
}

function parseTraceEvent(value: unknown) {
  const record = requiredRecord(value, "trace_event");
  return {
    at: requiredString(record.at, "trace_event.at"),
    event: requiredString(record.event, "trace_event.event"),
    id: requiredString(record.id, "trace_event.id")
  };
}

function parseAudit(value: unknown): AuditRecord {
  const record = requiredRecord(value, "audit");
  return {
    action: requiredString(record.action, "action"),
    actorId: requiredString(record.actor_id, "actor_id"),
    at: requiredString(record.at, "at"),
    id: requiredString(record.id, "id"),
    resourceId: requiredString(record.resource_id, "resource_id"),
    resourceVersion: requiredString(record.resource_version, "resource_version")
  };
}

export function parseResourceSchema(value: unknown): JsonSchemaNode {
  const record = requiredRecord(value, "resource schema");
  if (!isRecord(record.schema)) throw new Error("schema 无效");
  return record.schema as unknown as JsonSchemaNode;
}

export function parseValidation(value: unknown): ValidationResult {
  const record = requiredRecord(value, "validation");
  if (!Array.isArray(record.diagnostics) || !record.diagnostics.every((item) => typeof item === "string")) {
    throw new Error("diagnostics 无效");
  }
  return { diagnostics: record.diagnostics, valid: requiredBoolean(record.valid, "valid") };
}

/** TASK-009：`:validate-publish` 返回 `{ valid, issues }`（可操作问题清单）。 */
export function parsePublishValidation(value: unknown): ValidationResult {
  const record = requiredRecord(value, "validate-publish");
  const issues = record.issues;
  if (!Array.isArray(issues) || !issues.every((item) => typeof item === "string")) {
    throw new Error("issues 无效");
  }
  return { diagnostics: issues, valid: requiredBoolean(record.valid, "valid") };
}

export function parsePublish(value: unknown): PublishResult {
  const record = requiredRecord(value, "publish");
  const eventStatus = requiredString(record.event_status, "event_status");
  if (eventStatus !== "pending" && eventStatus !== "published") throw new Error("event_status 无效");
  return {
    eventStatus,
    kubernetesWorkloadCreated: false,
    resourceId: requiredString(record.resource_id, "resource_id"),
    status: requiredStatus(record.status),
    version: requiredString(record.version, "version")
  };
}

export function requiredRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${field} 无效`);
  return value;
}

export function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`${field} 无效`);
  return value;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function requiredNumber(value: unknown, field: string): number {
  if (typeof value !== "number") throw new Error(`${field} 无效`);
  return value;
}

function requiredBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} 无效`);
  return value;
}

function requiredResourceType(value: unknown): ResourceType {
  if (
    [
      "runtime_profile",
      "agent_definition",
      "model_provider",
      "model_definition",
      "tool",
      "secret",
      "skill",
      "mcp",
      "plugin",
      "policy",
      "workflow",
      "eval_set"
    ].includes(String(value))
  ) {
    return value as ResourceType;
  }
  throw new Error("resource_type 无效");
}

function requiredStatus(value: unknown): ResourceStatus {
  if (value === "draft" || value === "published" || value === "deprecated") return value;
  throw new Error("status 无效");
}

function requiredVisibility(value: unknown): ResourceVisibility {
  if (value === "system" || value === "public" || value === "tenant" || value === "private") return value;
  throw new Error("visibility 无效");
}

function requiredSubjectType(value: unknown): "user" | "tenant" {
  if (value === "user" || value === "tenant") return value;
  throw new Error("subject_type 无效");
}

function requiredJsonRecord(value: unknown, field: string): JsonRecord {
  if (!isJsonRecord(value)) throw new Error(`${field} 无效`);
  return value;
}

function isJsonRecord(value: unknown): value is JsonRecord {
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isJsonRecord(value);
}

