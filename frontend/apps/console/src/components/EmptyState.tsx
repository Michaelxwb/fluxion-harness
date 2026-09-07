import type { ReactNode } from "react";

import { Empty } from "@douyinfe/semi-ui";

interface EmptyStateProps {
  readonly description: string;
  readonly title?: string;
  readonly action?: ReactNode;
}

/** 统一空态：插画 + 一句话说明 + 主 CTA。 */
export function EmptyState({ description, title, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <Empty description={description} title={title}>
        {action}
      </Empty>
    </div>
  );
}
