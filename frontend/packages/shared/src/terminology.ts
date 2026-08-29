/**
 * 术语隐藏 denylist（TASK-015 / FEAT-P4-13 / NFR-ACC-01）——单一事实源，chat/console 双端引用。
 *
 * 固定清单（design §2.2 FEAT-P4-13 + 附录）：普通用户核心页禁止出现的底层术语。
 * 覆盖范围固定（RISK-P4-05）：仅普通用户可见面（chat 全部页面）；Admin/Builder
 * 视图不受限（他们需要底层术语）。
 */
export const TERMINOLOGY_DENYLIST = [
  "RuntimeProfile",
  "runtime_profile",
  "ExecutionSnapshot",
  "Registry",
  "Resource",
  "Binding",
  "Plugin",
  // Workflow 底层态（引擎/执行内部，产品语言「工作流运行」不受限）
  "DBOS",
  "engine_ref"
] as const;

export type TerminologyDenyTerm = (typeof TERMINOLOGY_DENYLIST)[number];

/** 统计一段渲染输出中 denylist 术语的出现次数（B-02：普通用户核心页 = 0）。 */
export function countDenylistHits(html: string): Record<string, number> {
  const hits: Record<string, number> = {};
  for (const term of TERMINOLOGY_DENYLIST) {
    const count = html.split(term).length - 1;
    if (count > 0) hits[term] = count;
  }
  return hits;
}
