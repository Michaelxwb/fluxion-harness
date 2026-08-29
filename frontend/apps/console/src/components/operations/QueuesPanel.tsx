/** C407 QueuesPanel（TASK-014 / CMP-11）：workflow 队列状态（展示组件）。 */
import { Empty, Table, Tag } from "@douyinfe/semi-ui";

import type { WorkflowQueueSummary } from "../../types/console";

interface QueuesPanelProps {
  readonly queues: readonly WorkflowQueueSummary[];
}

export function QueuesPanel({ queues }: QueuesPanelProps) {
  const columns = [
    { dataIndex: "queueId", title: "队列" },
    { dataIndex: "name", title: "名称" },
    {
      render: (_value: unknown, record: WorkflowQueueSummary) => (
        <Tag color={record.depth > 0 ? "orange" : "green"}>{record.depth > 0 ? "积压" : "空闲"}</Tag>
      ),
      title: "状态"
    },
    { dataIndex: "depth", title: "排队数" },
    { dataIndex: "workers", title: "Worker 数" }
  ];
  return (
    <section aria-label="Queues Panel">
      <Table
        columns={columns}
        dataSource={[...queues]}
        empty={<Empty description="无运行中队列" />}
        pagination={false}
        rowKey="queueId"
      />
    </section>
  );
}
