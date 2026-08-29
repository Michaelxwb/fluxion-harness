/** C407 RunsTable（TASK-014 / CMP-11）：workflow_run 投影表（trace 关联；展示组件）。 */
import { Table, Tag } from "@douyinfe/semi-ui";

import type { WorkflowRunProjection } from "../../types/console";

interface RunsTableProps {
  readonly runs: readonly WorkflowRunProjection[];
}

export function RunsTable({ runs }: RunsTableProps) {
  const columns = [
    { dataIndex: "runId", title: "运行 ID" },
    {
      render: (_value: unknown, record: WorkflowRunProjection) =>
        `${record.workflowId} @ ${record.workflowVersion}`,
      title: "工作流"
    },
    {
      render: (_value: unknown, record: WorkflowRunProjection) => (
        <Tag color={record.status === "succeeded" ? "green" : record.status === "running" ? "blue" : "red"}>
          {record.status}
        </Tag>
      ),
      title: "状态"
    },
    {
      render: (_value: unknown, record: WorkflowRunProjection) =>
        Object.keys(record.nodeStates).length,
      title: "节点数"
    },
    {
      // trace 关联：trace_id 直接可查（Console/Workflow Studio Phase 4 数据源）
      render: (_value: unknown, record: WorkflowRunProjection) => record.traceId,
      title: "Trace"
    },
    { dataIndex: "updatedAt", title: "更新时间" }
  ];
  return (
    <section aria-label="Workflow Runs">
      <Table
        columns={columns}
        dataSource={[...runs]}
        pagination={false}
        rowKey="runId"
      />
    </section>
  );
}
