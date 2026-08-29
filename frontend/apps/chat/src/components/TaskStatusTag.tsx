/** 展示组件：任务/运行状态标签（Tasks/Home 共用）。 */
import { Tag } from "@douyinfe/semi-ui";

import type { WorkspaceTaskStatus } from "../types/chat";

type TaskTagColor = "green" | "blue" | "red" | "grey";

const STATUS_META: Readonly<Record<WorkspaceTaskStatus, { color: TaskTagColor; text: string }>> = {
  cancelled: { color: "grey", text: "已取消" },
  failed: { color: "red", text: "失败" },
  pending: { color: "grey", text: "待开始" },
  running: { color: "blue", text: "进行中" },
  succeeded: { color: "green", text: "已完成" }
};

export function TaskStatusTag({ status }: { readonly status: WorkspaceTaskStatus }) {
  const meta = STATUS_META[status];
  return <Tag color={meta.color}>{meta.text}</Tag>;
}
