import { Tag } from "@douyinfe/semi-ui";

import type { ResourceStatus } from "../types/console";

interface StatusTagProps {
  readonly status:
    | ResourceStatus
    | "active"
    | "succeeded"
    | "failed"
    | "running"
    | "completed"
    | "cancelled"
    | "timed_out";
}

type StatusTagColor = "green" | "blue" | "red" | "grey" | "orange";

// 105 P2-02（TASK-012/FEAT-F3）：执行终态四值徽标（completed 绿/failed 红/
// cancelled 灰/timed_out 橙）；未知值回落灰色"未知"（防御未来值）。
const STATUS_LABELS: Record<StatusTagProps["status"], string> = {
  published: "已发布",
  draft: "草稿",
  active: "已启用",
  succeeded: "成功",
  failed: "失败",
  running: "运行中",
  deprecated: "已弃用",
  completed: "完成",
  cancelled: "已取消",
  timed_out: "超时"
};

export function StatusTag({ status }: StatusTagProps) {
  return <Tag color={tagColor(status)}>{STATUS_LABELS[status] ?? "未知"}</Tag>;
}

function tagColor(status: StatusTagProps["status"]): StatusTagColor {
  if (
    status === "published" ||
    status === "active" ||
    status === "succeeded" ||
    status === "completed"
  ) {
    return "green";
  }
  if (status === "draft" || status === "running") {
    return "blue";
  }
  if (status === "failed") {
    return "red";
  }
  if (status === "timed_out") {
    return "orange";
  }
  return "grey";
}
