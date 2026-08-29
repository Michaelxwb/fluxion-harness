/**
 * C401 Eval 占位空态页（TASK-004）：导航入口置灰、点击进入本占位页。
 * 实际评测页面归 Phase 5（design 对齐项 B）。
 */
import { Empty, Typography } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";

export function EvalPlaceholderPage() {
  return (
    <section aria-label="评测">
      <PageHeader description="能力评测（Phase 5 提供）" title="评测" />
      <Empty description="评测能力建设中" title={<Typography.Text type="tertiary">暂无内容</Typography.Text>} />
    </section>
  );
}
