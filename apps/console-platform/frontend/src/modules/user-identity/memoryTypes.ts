/**
 * 用户记忆（`runtime.user_memory`）的严格前端契约，与 Console/Runtime 后端 DTO 逐字段对齐：
 * - `MemoryItem`：`GET /users/{user_id}/memory` 读投影（Console `MemoryItem`）；
 * - `MemoryHistoryItem`：`GET /users/{user_id}/memory/{memory_key}/history` 版本历史（author 与 actor 分列）；
 * - `MemoryCreateInput` / `MemoryEnabledUpdateInput`：管理员写操作入参（author 不可选）；
 * - `MemoryCommandReceipt`：写路由返回的 Runtime 命令回执（`muad_contracts.memory_admin.MemoryCommandResponse`）。
 *
 * 字段名保持后端 snake_case（与 `users.ts` 其余 DTO 一致）；不放任 `any`，也不保留旧 `source_type`。
 */

/** `enabled` + `expires_at` 派生的记忆状态。 */
export type MemoryStatus = 'ACTIVE' | 'DISABLED' | 'EXPIRED';

/** 记忆作者：USER=用户确认、ADMIN=管理员人工、AGENT=推断。 */
export type MemoryAuthor = 'USER' | 'ADMIN' | 'AGENT';

/** 分类枚举；`TASK_STATE` 是 Agent 任务线索占用的保留命名空间。 */
export type MemoryCategory = 'PREFERENCE' | 'WORK_STYLE' | 'EXPLICIT' | 'TASK_STATE';

/** 管理员人工写入允许的分类（后端 `MemoryCreateRequest` 拒绝 TASK_STATE）。 */
export type MemoryWritableCategory = 'PREFERENCE' | 'WORK_STYLE' | 'EXPLICIT';

/** 普通事实内容（Runtime `FactContent`）。 */
export interface MemoryFactContent {
  value: string;
}

/** 任务线索内容（Runtime `TaskStateContent`，key 固定在 `task.<root_conversation_hex>`）。 */
export interface MemoryTaskStateContent {
  title: string;
  goal: string;
  constraints: string[];
  open_items: string[];
  next_action: string;
  root_conversation_id: string;
  source_conversation_id: string;
}

/** 记忆内容投影：普通事实 `{ value }` 或结构化任务线索。 */
export type MemoryContent = MemoryFactContent | MemoryTaskStateContent;

/** `GET /users/{user_id}/memory` 列表项。 */
export interface MemoryItem {
  id: string;
  memory_key: string;
  category: MemoryCategory;
  content: MemoryContent;
  author: MemoryAuthor;
  version: number;
  enabled: boolean;
  explicit_request: boolean;
  confirmed_count: number;
  status: MemoryStatus;
  source_ref: string | null;
  source_quote: string | null;
  expires_at: string | null;
  last_confirmed_at: string | null;
  create_time: string;
  update_time: string;
}

/** 版本历史项：`after` 是当时的完整快照；未知/历史 actor 与原因原样透出。 */
export interface MemoryHistoryItem {
  id: string;
  origin_id: string;
  memory_key: string;
  version: number;
  change_reason: string;
  actor_type: string;
  actor_id: string | null;
  command_id: string;
  source_run_id: string | null;
  after: Record<string, unknown>;
  create_time: string;
}

/** 列表过滤（与后端 query 参数同名）；分页由 `PageParams` 承载。 */
export interface MemoryListQuery {
  category?: MemoryCategory;
  author?: MemoryAuthor;
  status?: MemoryStatus;
}

/**
 * 管理员新增/显式覆盖入参（`POST /users/{user_id}/memory`）：
 * `author` 不可选；`expected_version` 是覆盖既有 key 的前提，新 key 不传。
 */
export interface MemoryCreateInput {
  memory_key: string;
  category: MemoryWritableCategory;
  value: string;
  expected_version?: number;
  expires_at?: string;
}

/** 启禁用入参（`PATCH /users/{user_id}/memory/{memory_key}`）：版本冲突不覆盖。 */
export interface MemoryEnabledUpdateInput {
  enabled: boolean;
  expected_version: number;
  expires_at?: string;
}

/** 单条变更结果状态（Runtime `MemoryResultStatus`）。 */
export type MemoryResultStatus =
  | 'CREATED'
  | 'UPDATED'
  | 'DELETED'
  | 'ENABLED'
  | 'DISABLED'
  | 'RESTORED'
  | 'RETIRED'
  | 'UNCHANGED'
  | 'SKIPPED';

/** 单条变更结果（Runtime `MemoryMutationResult`）。 */
export interface MemoryMutationResult {
  memory_key: string;
  id: string | null;
  version: number | null;
  status: MemoryResultStatus;
  reason: string | null;
}

/** 命令整体结果。 */
export type MemoryCommandStatus = 'APPLIED' | 'UNCHANGED' | 'STALE';

/** 写路由回执（Runtime `MemoryCommandResponse`）：`command_id` 即调用方发来的 Idempotency-Key。 */
export interface MemoryCommandReceipt {
  command_id: string;
  scope_revision: number;
  forget_epoch: number;
  changed_count: number;
  status: MemoryCommandStatus;
  items: MemoryMutationResult[];
}

/**
 * 记忆命令错误码（与 `config/api-messages.yaml` 同名，未知码不进入语义分支）。
 * HTTP 语义：404 `MEMORY_NOT_FOUND`（条目不存在）/`COMMON_NOT_FOUND`（用户不在本租户）；
 * 409 `MEMORY_VERSION_CONFLICT`（版本冲突，刷新后按新版本重试）/`IDEMPOTENCY_MISMATCH`
 * （同幂等键换了输入）；403 `FORBIDDEN`（无管理权限）；400 `MEMORY_VALUE_INVALID`；
 * 422 `COMMON_VALIDATION_ERROR`；500 `COMMON_INTERNAL_ERROR`（Runtime 暂时不可用，可重试）。
 */
export const MEMORY_ERROR_CODES = [
  'MEMORY_VALUE_INVALID',
  'MEMORY_NOT_FOUND',
  'COMMON_NOT_FOUND',
  'MEMORY_VERSION_CONFLICT',
  'IDEMPOTENCY_MISMATCH',
  'FORBIDDEN',
  'COMMON_VALIDATION_ERROR',
  'COMMON_INTERNAL_ERROR'
] as const;

export type MemoryErrorCode = (typeof MEMORY_ERROR_CODES)[number];

/** 码值收窄：未知码返回 false，调用方按通用失败处理。 */
export function isMemoryErrorCode(code: string): code is MemoryErrorCode {
  return MEMORY_ERROR_CODES.some((candidate) => candidate === code);
}
