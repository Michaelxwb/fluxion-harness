import { Tag } from "@douyinfe/semi-ui";

import type { ResourceStatus } from "../types/console";

interface StatusTagProps {
  readonly status: ResourceStatus | "active" | "succeeded" | "failed" | "running";
}

type StatusTagColor = "green" | "blue" | "red" | "grey";

const STATUS_LABELS: Record<StatusTagProps["status"], string> = {
  published: "已发布",
  draft: "草稿",
  active: "已启用",
  succeeded: "成功",
  failed: "失败",
  running: "运行中",
  deprecated: "已弃用"
};

export function StatusTag({ status }: StatusTagProps) {
  return <Tag color={tagColor(status)}>{STATUS_LABELS[status] ?? status}</Tag>;
}

function tagColor(status: StatusTagProps["status"]): StatusTagColor {
  if (status === "published" || status === "active" || status === "succeeded") {
    return "green";
  }
  if (status === "draft" || status === "running") {
    return "blue";
  }
  if (status === "failed") {
    return "red";
  }
  return "grey";
}
