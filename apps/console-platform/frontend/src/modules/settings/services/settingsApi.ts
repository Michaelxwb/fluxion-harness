/**
 * 平台设置 service 层（前端设计 §3.5 数据获取层）。
 *
 * 全部请求经共享 `api` 实例（自动 X-Locale/X-Request-Id/CSRF）；后端 snake_case 与前端
 * camelCase 的字段映射只在本层发生，组件与 hook 不直接消费原始封套，也不裸用 axios/fetch
 * （RULE-front-001）。错误码与字段级错误的提取也只在这里，供 hook 映射到 UI 状态。
 */

import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import type {
  AppliesTo,
  PlatformSettingsSnapshot,
  RevisionPage,
  SaveSettingsResult,
  SettingsFieldError,
  SettingsFieldMeta,
  SettingsFieldType,
  SettingsGroupMeta,
  SettingsRevision,
  SettingsValues
} from '../types';

/** 后端 API-01 分组/字段出参（snake_case）。 */
interface RawField {
  path: string;
  label_key: string;
  type: string;
  default: unknown;
  value: unknown;
  applies_to: AppliesTo;
  overridden_by_resources: number;
  min?: number;
  max?: number;
  enum?: string[];
  unit_key?: string;
}

interface RawGroup {
  key: string;
  label_key: string;
  applies_to: AppliesTo;
  readonly: boolean;
  fields: RawField[];
}

interface RawReadonlyNote {
  key: string;
  label_key: string;
  note_key: string;
  owner: string;
}

interface RawSnapshot {
  revision: number;
  updated_at: string | null;
  updated_by: string | null;
  groups: RawGroup[];
  readonly_notes: RawReadonlyNote[];
}

interface RawSaveResult {
  revision: number;
  settings: Record<string, unknown>;
}

interface RawRevision {
  revision: number;
  created_at: string | null;
  actor_display: string | null;
  changed_keys: string[];
}

interface RawRevisionPage {
  items: RawRevision[];
  page: number;
  page_size: number;
  total: number;
}

interface RawRestoreResult {
  revision: number;
  restored_from: number;
}

const FIELD_TYPES: readonly SettingsFieldType[] = ['bool', 'int', 'float', 'string', 'unknown'];

function toFieldType(value: string): SettingsFieldType {
  return FIELD_TYPES.includes(value as SettingsFieldType)
    ? (value as SettingsFieldType)
    : 'unknown';
}

function optional<T>(value: T | null | undefined): T | undefined {
  return value === null || value === undefined ? undefined : value;
}

function unwrap<T>(envelope: ApiResponse<T>): T {
  // 成功封套的 data 允许为 null（`ok(catalog)` 无参）；设置读取/保存/历史都不接受 null。
  if (envelope.data === null) {
    throw envelope;
  }
  return envelope.data;
}

function toField(raw: RawField): SettingsFieldMeta {
  return {
    path: raw.path,
    labelKey: raw.label_key,
    type: toFieldType(raw.type),
    defaultValue: raw.default,
    value: raw.value,
    appliesTo: raw.applies_to,
    overriddenByResources: raw.overridden_by_resources,
    min: optional(raw.min),
    max: optional(raw.max),
    enum: optional(raw.enum),
    unitKey: optional(raw.unit_key)
  };
}

function toGroup(raw: RawGroup): SettingsGroupMeta {
  return {
    key: raw.key,
    labelKey: raw.label_key,
    appliesTo: raw.applies_to,
    readonly: raw.readonly,
    fields: raw.fields.map(toField)
  };
}

function toSnapshot(raw: RawSnapshot): PlatformSettingsSnapshot {
  return {
    revision: raw.revision,
    updatedAt: raw.updated_at,
    updatedBy: raw.updated_by,
    groups: raw.groups.map(toGroup),
    readonlyNotes: raw.readonly_notes.map((note) => ({
      key: note.key,
      labelKey: note.label_key,
      noteKey: note.note_key,
      owner: note.owner
    }))
  };
}

function toRevision(raw: RawRevision): SettingsRevision {
  return {
    revision: raw.revision,
    createdAt: raw.created_at,
    actorDisplay: raw.actor_display,
    changedKeys: raw.changed_keys
  };
}

/** 读取当前平台设置（API-01，admin）。 */
export async function getPlatformSettings(): Promise<PlatformSettingsSnapshot> {
  const response = await api.get<ApiResponse<RawSnapshot>>('/platform-settings');
  return toSnapshot(unwrap(response.data));
}

/** 保存整份设置文档（API-02，admin + CSRF）；乐观并发靠 `revision`。 */
export async function savePlatformSettings(payload: {
  revision: number;
  settings: SettingsValues;
}): Promise<SaveSettingsResult> {
  const response = await api.put<ApiResponse<RawSaveResult>>('/platform-settings', {
    revision: payload.revision,
    settings: payload.settings
  });
  const data = unwrap(response.data);
  return { revision: data.revision, settings: data.settings };
}

/** 版本历史（API-03，admin），统一分页封套。 */
export async function listSettingsRevisions(params: {
  page: number;
  pageSize: number;
}): Promise<RevisionPage> {
  const response = await api.get<ApiResponse<RawRevisionPage>>('/platform-settings/revisions', {
    params: { page: params.page, page_size: params.pageSize }
  });
  const data = unwrap(response.data);
  return {
    items: data.items.map(toRevision),
    page: data.page,
    pageSize: data.page_size,
    total: data.total
  };
}

/** 回滚到指定版本（API-04，admin + CSRF）；成功产生新版本，历史不删除。 */
export async function restoreSettingsRevision(revision: number): Promise<RawRestoreResult> {
  const response = await api.post<ApiResponse<RawRestoreResult>>(
    `/platform-settings/revisions/${revision}/restore`
  );
  return unwrap(response.data);
}

/** 错误码（AxiosError 的 `response.data.code` 或业务错误体本身）。 */
export function errorCodeOf(error: unknown): string | null {
  const body = apiErrorBody(error);
  return body?.code ?? null;
}

interface RawDetails {
  details?: { path?: string; message?: string }[];
}

/** 字段级错误（API-02 `VALIDATION_FAILED` 的 `details[].path`）。 */
export function fieldErrorsFrom(error: unknown): SettingsFieldError[] {
  const candidate = error as { response?: { data?: { data?: RawDetails } } } & { data?: RawDetails };
  const details = candidate?.response?.data?.data?.details ?? candidate?.data?.details;
  if (!Array.isArray(details)) {
    return [];
  }
  return details
    .filter((item): item is { path: string; message?: string } => Boolean(item?.path))
    .map((item) => ({ path: item.path, message: item.message ?? '' }));
}
