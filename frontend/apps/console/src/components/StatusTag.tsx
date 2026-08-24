import { Tag } from "@douyinfe/semi-ui";

import type { ResourceStatus } from "../types/console";

interface StatusTagProps {
  readonly status: ResourceStatus | "active" | "succeeded" | "failed" | "running";
}

type StatusTagColor = "green" | "blue" | "red" | "grey";

export function StatusTag({ status }: StatusTagProps) {
  return <Tag color={tagColor(status)}>{status}</Tag>;
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
