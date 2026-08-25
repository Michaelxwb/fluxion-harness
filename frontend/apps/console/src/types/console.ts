import type { P1View } from "./navigation";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | JsonRecord;

export interface JsonRecord {
  readonly [key: string]: JsonValue;
}

export type ResourceType = "runtime_profile" | "skill" | "mcp" | "plugin" | "policy" | "workflow";
export type ResourceStatus = "draft" | "published" | "deprecated";
export type ResourceVisibility = "system" | "public" | "tenant" | "private";

export interface PageRequest {
  readonly page: number;
  readonly pageSize: number;
}

export interface PageData<T> extends PageRequest {
  readonly items: readonly T[];
  readonly total: number;
}

export interface ResourceVersion {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly tenantId: string;
  readonly version: string;
  readonly status: ResourceStatus;
  readonly visibility: ResourceVisibility;
  readonly spec: JsonRecord;
  readonly updatedAt: string;
}

export interface ResourceSummary {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly displayName: string;
  readonly currentVersion: string;
  readonly status: ResourceStatus;
  readonly visibility: ResourceVisibility;
  readonly updatedAt: string;
}

export interface ResourceCreateInput {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly version: string;
  readonly visibility: ResourceVisibility;
  readonly spec: JsonRecord;
}

export interface ValidationResult {
  readonly valid: boolean;
  readonly diagnostics: readonly string[];
}

export interface PublishResult {
  readonly resourceId: string;
  readonly version: string;
  readonly status: ResourceStatus;
  readonly eventStatus: "pending" | "published";
  readonly kubernetesWorkloadCreated: false;
}

export interface RollbackResult {
  readonly resourceId: string;
  readonly targetVersion: string;
  readonly newVersion: string;
  readonly status: ResourceStatus;
}

export interface BindingRecord {
  readonly bindingId: string;
  readonly tenantId: string;
  readonly subjectType: "user" | "tenant";
  readonly subjectId: string;
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly versionSelector: string;
  readonly credentialRef: string | null;
  readonly policyId: string;
  readonly enabled: boolean;
}

export interface CredentialMetadata {
  readonly credentialRef: string;
  readonly provider: string;
  readonly status: "active" | "rotating" | "disabled";
  readonly lastRotatedAt: string;
}

export interface VersionRef {
  readonly id: string;
  readonly version: string;
}

export interface TraceEvent {
  readonly id: string;
  readonly event: string;
  readonly at: string;
}

export interface RunDetail {
  readonly executionId: string;
  readonly status: "running" | "succeeded" | "failed";
  readonly startedAt: string;
  readonly snapshot: {
    readonly runtimeProfile: VersionRef;
    readonly skills: readonly VersionRef[];
    readonly mcps: readonly VersionRef[];
    readonly plugins: readonly VersionRef[];
    readonly policies: readonly VersionRef[];
  };
  readonly traceEvents: readonly TraceEvent[];
}

export interface AuditRecord {
  readonly id: string;
  readonly action: string;
  readonly actorId: string;
  readonly resourceId: string;
  readonly resourceVersion: string;
  readonly at: string;
}

export interface ControlPlaneItem {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly detail: string;
}

export interface PlatformUser {
  readonly platformUserId: string;
  readonly displayName: string;
  readonly createdAt: string;
}

export interface IssuedChatAccess {
  readonly accessId: string;
  readonly platformUserId: string;
  readonly runtimeProfileId: string;
  readonly token: string;
  readonly chatPath: string;
  readonly createdAt: string;
}

export interface BindingInput {
  readonly resourceType: ResourceType;
  readonly resourceId: string;
  readonly subjectType: "user" | "tenant";
  readonly subjectId: string;
  readonly versionSelector: string;
  readonly credentialRef: string | null;
  readonly policyId: string;
}

export interface ConsoleApi {
  listResources(resourceType?: ResourceType): Promise<PageData<ResourceSummary>>;
  getResource(resourceType: ResourceType, resourceId: string, version?: string): Promise<ResourceVersion>;
  createResource(input: ResourceCreateInput): Promise<ResourceVersion>;
  createDraftFromLatest(resourceType: ResourceType, resourceId: string): Promise<ResourceVersion>;
  updateDraft(resource: ResourceVersion, spec: JsonRecord): Promise<ResourceVersion>;
  validateDraft(resource: ResourceVersion): Promise<ValidationResult>;
  publishVersion(resource: ResourceVersion): Promise<PublishResult>;
  rollbackVersion(resource: ResourceVersion, targetVersion: string): Promise<RollbackResult>;
  listVersions(resourceType: ResourceType, resourceId: string, page: PageRequest): Promise<PageData<ResourceVersion>>;
  listVisibleResources(resourceType: ResourceType): Promise<readonly ResourceSummary[]>;
  listBindings(request: PageRequest, resourceType?: ResourceType): Promise<PageData<BindingRecord>>;
  saveBinding(input: BindingInput): Promise<BindingRecord>;
  listCredentials(): Promise<readonly CredentialMetadata[]>;
  listRuns(): Promise<readonly RunDetail[]>;
  listAudit(page: PageRequest): Promise<PageData<AuditRecord>>;
  listP1View(view: P1View): Promise<readonly ControlPlaneItem[]>;
  listPlatformUsers(request: PageRequest): Promise<PageData<PlatformUser>>;
  createPlatformUser(platformUserId: string, displayName: string): Promise<PlatformUser>;
  issueChatAccess(platformUserId: string, runtimeProfileId: string): Promise<IssuedChatAccess>;
  revokeChatAccess(accessId: string): Promise<void>;
}
