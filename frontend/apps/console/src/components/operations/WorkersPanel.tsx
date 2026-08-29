/** C407 WorkersPanel（TASK-014 / CMP-11）：运行 Worker 状态（展示组件）。 */
import { Empty, Table, Tag } from "@douyinfe/semi-ui";

import type { WorkflowWorkerSummary } from "../../types/console";

interface WorkersPanelProps {
  readonly workers: readonly WorkflowWorkerSummary[];
}

export function WorkersPanel({ workers }: WorkersPanelProps) {
  const columns = [
    { dataIndex: "workerId", title: "Worker" },
    {
      render: (_value: unknown, record: WorkflowWorkerSummary) => (
        <Tag color={record.status === "running" ? "green" : record.status === "idle" ? "blue" : "grey"}>
          {record.status}
        </Tag>
      ),
      title: "状态"
    },
    {
      render: (_value: unknown, record: WorkflowWorkerSummary) => record.queues.join(", "),
      title: "消费队列"
    },
    { dataIndex: "runningWorkflows", title: "运行中工作流" },
    { dataIndex: "startedAt", title: "启动时间" }
  ];
  return (
    <section aria-label="Workers Panel">
      <Table
        columns={columns}
        dataSource={[...workers]}
        empty={<Empty description="无运行中 Worker" />}
        pagination={false}
        rowKey="workerId"
      />
    </section>
  );
}
