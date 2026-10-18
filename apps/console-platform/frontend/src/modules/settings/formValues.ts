/**
 * 设置表单的纯逻辑：扁平值 ↔ 后端整份文档、脏判断与字段错误定位（前端设计 §3.5）。
 *
 * 后端 API-01 的字段路径有两套形态：压缩组用**组内相对路径**（`snip.max_groups`），其余分组
 * 带分组前缀（`task.max_attempts`）。API-02 提交的是**整份分组文档**，所以保存前要把扁平值
 * 还原成嵌套结构——本模块是这层转换的唯一落点，组件与 hook 不重复实现。
 */

import type { SettingsFieldMeta, SettingsGroupMeta, SettingsValues } from './types';

/** 该分组在扁平路径里使用的前缀（压缩组不带前缀）。 */
function groupPrefix(groupKey: string): string {
  return groupKey === 'compaction' ? '' : `${groupKey}.`;
}

/** 取字段路径在分组内的相对路径。 */
export function relativePath(groupKey: string, path: string): string {
  const prefix = groupPrefix(groupKey);
  return prefix && path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

/** 由元数据构建初始扁平值（每个字段的当前生效值）。 */
export function initialValues(groups: SettingsGroupMeta[]): SettingsValues {
  const values: SettingsValues = {};
  for (const group of groups) {
    for (const field of group.fields) {
      values[field.path] = field.value;
    }
  }
  return values;
}

function setNested(target: Record<string, unknown>, path: string, value: unknown): void {
  const segments = path.split('.');
  let node = target;
  for (const segment of segments.slice(0, -1)) {
    const child = node[segment];
    if (typeof child !== 'object' || child === null || Array.isArray(child)) {
      node[segment] = {};
    }
    node = node[segment] as Record<string, unknown>;
  }
  node[segments[segments.length - 1]] = value;
}

/**
 * 把扁平值还原成 API-02 需要的整份分组文档。
 *
 * 只输出已知字段（元数据里出现过的路径），未知键不提交——避免把客户端的脏数据带进保存。
 */
export function buildDocument(
  groups: SettingsGroupMeta[],
  values: SettingsValues
): Record<string, unknown> {
  const document: Record<string, unknown> = {};
  for (const group of groups) {
    const groupDocument: Record<string, unknown> = {};
    for (const field of group.fields) {
      if (!(field.path in values)) {
        continue;
      }
      setNested(groupDocument, relativePath(group.key, field.path), values[field.path]);
    }
    document[group.key] = groupDocument;
  }
  return document;
}

/** 脏判断：逐字段比对当前值与基准值（浅比较即可，叶子都是原始类型）。 */
export function isDirty(base: SettingsValues, current: SettingsValues): boolean {
  const keys = new Set([...Object.keys(base), ...Object.keys(current)]);
  for (const key of keys) {
    if (base[key] !== current[key]) {
      return true;
    }
  }
  return false;
}

/** 字段错误定位结果：错误路径命中的分组与字段（控件）。 */
export interface FieldLocation {
  group: SettingsGroupMeta;
  field: SettingsFieldMeta;
}

/**
 * 把后端 `details[].path` 解析到具体控件（E-11：错误落到字段行，不整页报错）。
 *
 * 两种形态都要认：字段路径（`webchat.upload_max_bytes`）直接命中；压缩组（compaction）的字段
 * 路径是组内相对路径（`summary.model_ref`），而跨字段校验错误可能带分组前缀
 * （`compaction.summary.model_ref`，见 `muad_contracts.platform_settings._validate_summary_model_ref`），
 * 此时按分组前缀归一化后再匹配。解析不到返回 null——不猜、不把错误挂到别的字段上。
 */
export function locateField(groups: SettingsGroupMeta[], path: string): FieldLocation | null {
  for (const group of groups) {
    const field = group.fields.find((candidate) => candidate.path === path);
    if (field) {
      return { group, field };
    }
  }
  for (const group of groups) {
    const prefix = `${group.key}.`;
    if (!path.startsWith(prefix)) {
      continue;
    }
    const relative = path.slice(prefix.length);
    const field = group.fields.find((candidate) => candidate.path === relative);
    if (field) {
      return { group, field };
    }
  }
  return null;
}
