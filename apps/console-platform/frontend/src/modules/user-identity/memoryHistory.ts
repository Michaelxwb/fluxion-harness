/**
 * 记忆版本历史的**删除态**判定（评审条目 5：删除后恢复）。
 *
 * 契约：
 * - 历史按 `version DESC` 返回，首行即最新版本；`after` 是当时的完整行快照（含 `is_deleted`），
 *   所以"当前是不是 tombstone"只由**最新版本**决定，不能看历史里任意一条删除记录；
 * - 恢复动作必须按 tombstone 的**保留版本**做版本检查（删除不重置 version），因此这里返回保留
 *   版本本身；最新版本不是删除快照时返回 `null`（恢复入口随之消失）；
 * - 只认布尔 `true`：缺字段、字符串或未知形态都不算删除态（不做真值转换）。
 */

import type { MemoryHistoryItem } from './memoryTypes';

/** 最新版本是删除快照时返回它的保留版本，否则 null。 */
export function memoryTombstoneVersion(items: readonly MemoryHistoryItem[]): number | null {
  const latest = items[0];
  if (latest === undefined || latest.after.is_deleted !== true) {
    return null;
  }
  return latest.version;
}
