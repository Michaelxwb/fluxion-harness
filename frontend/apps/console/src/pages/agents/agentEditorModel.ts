import type {
  CapabilitySelection,
  JsonRecord,
  ResourceSummary,
  ResourceVersion
} from "../../types/console";

export interface AgentEditorValue {
  readonly name: string;
  readonly systemPrompt: string;
  readonly instructions: string;
  readonly primaryModel: string;
  readonly modelTimeoutMs: number;
  readonly modelDeadlineMs: number;
  readonly runtimeProfile: string;
  readonly workflow: string;
  readonly memoryPolicy: string;
  readonly personalizationPolicy: string;
  readonly capabilities: readonly CapabilitySelection[];
}

export const EMPTY_AGENT_EDITOR_VALUE: AgentEditorValue = {
  name: "",
  systemPrompt: "",
  instructions: "",
  primaryModel: "",
  modelTimeoutMs: 60_000,
  modelDeadlineMs: 120_000,
  runtimeProfile: "",
  workflow: "",
  memoryPolicy: "",
  personalizationPolicy: "",
  capabilities: []
};

export function editorValueFrom(resource: ResourceVersion): AgentEditorValue {
  const modelPolicy = asRecord(resource.spec?.model_policy);
  return {
    name: String(resource.spec?.name ?? resource.resourceId),
    systemPrompt: String(resource.spec?.system_prompt ?? ""),
    instructions: String(resource.spec?.instructions ?? ""),
    primaryModel: formatExactRef(asRecord(modelPolicy?.primary_model_ref)),
    modelTimeoutMs: Number(modelPolicy?.model_timeout_ms ?? 60_000),
    modelDeadlineMs: Number(modelPolicy?.model_deadline_ms ?? 120_000),
    runtimeProfile: formatExactRef(asRecord(resource.spec?.runtime_profile_ref)),
    workflow: formatExactRef(asRecord(resource.spec?.workflow_ref)),
    memoryPolicy: formatExactRef(asRecord(resource.spec?.memory_policy_ref)),
    personalizationPolicy: formatExactRef(asRecord(resource.spec?.personalization_policy_ref)),
    capabilities: capabilitySelections(resource.spec?.capabilities)
  };
}

export function editorSpec(resource: ResourceVersion, value: AgentEditorValue): JsonRecord {
  const existingModelPolicy = asRecord(resource.spec?.model_policy) ?? {};
  return {
    ...resource.spec,
    name: value.name.trim(),
    system_prompt: value.systemPrompt,
    instructions: value.instructions,
    model_policy: {
      ...existingModelPolicy,
      primary_model_ref: parseExactRef(value.primaryModel),
      model_timeout_ms: value.modelTimeoutMs,
      model_deadline_ms: value.modelDeadlineMs
    },
    runtime_profile_ref: parseOptionalExactRef(value.runtimeProfile),
    workflow_ref: parseOptionalExactRef(value.workflow),
    memory_policy_ref: parseOptionalExactRef(value.memoryPolicy),
    personalization_policy_ref: parseOptionalExactRef(value.personalizationPolicy),
    capabilities: value.capabilities.map((capability) => ({
      type: capability.type,
      capability_ref: capability.capabilityRef,
      version_pin: capability.versionPin
    }))
  };
}

export function referenceOptions(resources: readonly ResourceSummary[]) {
  return resources.map((resource) => ({
    label: `${resource.displayName || resource.resourceId} (${resource.currentVersion})`,
    value: `${resource.resourceId}@${resource.currentVersion}`
  }));
}

function capabilitySelections(value: unknown): readonly CapabilitySelection[] {
  if (!Array.isArray(value)) return [];
  return value.map((capability) => {
    const item = capability as JsonRecord;
    return {
      type: item.type as CapabilitySelection["type"],
      capabilityRef: String(item.capability_ref),
      versionPin: String(item.version_pin)
    };
  });
}

function asRecord(value: unknown): JsonRecord | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : undefined;
}

function formatExactRef(value: JsonRecord | undefined): string {
  return value?.id && value.version ? `${String(value.id)}@${String(value.version)}` : "";
}

function parseExactRef(value: string): JsonRecord {
  const index = value.lastIndexOf("@");
  return index > 0
    ? { id: value.slice(0, index), version: value.slice(index + 1) }
    : { id: value, version: "latest-published" };
}

function parseOptionalExactRef(value: string): JsonRecord | null {
  return value.trim() ? parseExactRef(value.trim()) : null;
}
