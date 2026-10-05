/**
 * 平台设置页类型（前端设计 §3.4/§3.5）。
 *
 * 字段的类型/默认值/范围/枚举一律来自 API-01 返回的元数据（`default`/`type`/`min`/`max`/`enum`）；
 * 前端不持有任何默认值或范围副本，避免成为第二套默认源（RISK-FE-01）。
 */

/** 生效方式枚举（API-01 `applies_to`）。 */
export type AppliesTo = 'new_run' | 'new_task' | 'next_operation' | 'restart_required' | 'code';

/** 字段控件类型（API-01 `type`；`unknown` 覆盖 `summary.model_ref` 这样的可空引用）。 */
export type SettingsFieldType = 'bool' | 'int' | 'float' | 'string' | 'unknown';

/** 单个设置项元数据（API-01 groups[].fields[]）。 */
export interface SettingsFieldMeta {
  path: string;
  labelKey: string;
  type: SettingsFieldType;
  /** 平台默认值（API 返回，前端只读展示）。 */
  defaultValue: unknown;
  /** 当前生效值（revision=0 时等于 defaultValue）。 */
  value: unknown;
  appliesTo: AppliesTo;
  overriddenByResources: number;
  min?: number;
  max?: number;
  enum?: string[];
  unitKey?: string;
}

/** 分组元数据（API-01 groups[]）。 */
export interface SettingsGroupMeta {
  key: string;
  labelKey: string;
  appliesTo: AppliesTo;
  readonly: boolean;
  fields: SettingsFieldMeta[];
}

/** 非本页管理的项及其归属说明（API-01 readonly_notes[]）。 */
export interface ReadonlyNote {
  key: string;
  labelKey: string;
  noteKey: string;
  owner: string;
}

/** API-01 读取结果。 */
export interface PlatformSettingsSnapshot {
  revision: number;
  updatedAt: string | null;
  updatedBy: string | null;
  groups: SettingsGroupMeta[];
  readonlyNotes: ReadonlyNote[];
}

/** API-02 保存结果。 */
export interface SaveSettingsResult {
  revision: number;
  settings: Record<string, unknown>;
}

/** API-03 单条历史版本。 */
export interface SettingsRevision {
  revision: number;
  createdAt: string | null;
  actorDisplay: string | null;
  changedKeys: string[];
}

/** API-03 分页封套（统一 `{items,page,page_size,total}`）。 */
export interface RevisionPage {
  items: SettingsRevision[];
  page: number;
  pageSize: number;
  total: number;
}

/** 保存失败时按字段定位的错误（API-02 `details[].path`）。 */
export interface SettingsFieldError {
  path: string;
  message: string;
}

/** 扁平化的表单值：字段路径 → 值（压缩组用组内相对路径）。 */
export type SettingsValues = Record<string, unknown>;
