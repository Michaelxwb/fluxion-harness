import type { ResourceSummary } from "../../types/console";

/** TASK-009：凭据列表行投影（列表页与元数据/轮换/禁用 Modal 共享）。 */
export interface CredentialRow {
  readonly key: string;
  readonly displayName: string;
  readonly resourceId: string;
  readonly secretRef: string;
  readonly purpose: string;
  readonly revoked: boolean;
  readonly updatedAt: string;
  /** 使用方：引用该 SecretRef 的 Provider 展示名（投影服务端关联）。 */
  readonly consumers: readonly string[];
  readonly status: ResourceSummary["status"];
  /** 当前版本号（发布操作定位版本用）。 */
  readonly version: string;
}
