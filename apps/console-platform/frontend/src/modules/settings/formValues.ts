/**
 * 设置表单的纯逻辑：扁平值 ↔ 后端整份文档、脏判断（前端设计 §3.5）。
 *
 * 后端 API-01 的字段路径有两套形态：压缩组用**组内相对路径**（`snip.max_groups`），其余分组
 * 带分组前缀（`task.max_attempts`）。API-02 提交的是**整份分组文档**，所以保存前要把扁平值
 * 还原成嵌套结构——本模块是这层转换的唯一落点，组件与 hook 不重复实现。
 */

import type { SettingsGroupMeta, SettingsValues } from './types';

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
