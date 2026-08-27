import { useEffect, useState } from "react";

import { Card, Table, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, AuditRecord } from "../../types/console";
import { PageHeader } from "../../components/PageHeader";

interface OverviewProps {
  readonly api: ConsoleApi;
}

interface CountCard {
  readonly label: string;
  readonly value: number | null;
}

/** TASK-011 / FE-S-15：概览——关键对象计数 + 最近活动骨架。 */
export function OverviewPage({ api }: OverviewProps) {
  const [counts, setCounts] = useState<CountCard[]>(() => [
    { label: "智能体", value: null },
    { label: "工作流", value: null },
    { label: "用户", value: null },
    { label: "执行记录", value: null }
  ]);
  const [activity, setActivity] = useState<readonly AuditRecord[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [agents, workflows, users, runs, auditPage] = await Promise.all([
        api.listVisibleResources("agent_definition"),
        api.listResources("workflow").then((page) => page.items),
        api.listPlatformUsers({ page: 1, pageSize: 1 }),
        api.listRuns(),
        api.listAudit({ page: 1, pageSize: 5 })
      ]);
      if (cancelled) {
        return;
      }
      setCounts([
        { label: "智能体", value: agents.length },
        { label: "工作流", value: workflows.length },
        { label: "用户", value: users.total },
        { label: "执行记录", value: runs.length }
      ]);
      setActivity(auditPage.items);
    })();
    return () => {
      cancelled = true;
    };
  }, [api]);

  return (
    <div>
      <PageHeader title="概览" description="平台对象计数与最近操作轨迹" />
      <div className="overview-cards">
        {counts.map((card) => (
          <Card key={card.label} className="overview-card" aria-label={`count-${card.label}`}>
            <Typography.Title heading={3}>
              {card.value === null ? "…" : card.value}
            </Typography.Title>
            <Typography.Text type="tertiary">{card.label}</Typography.Text>
          </Card>
        ))}
      </div>
      <Typography.Title heading={5} style={{ marginTop: 16 }}>
        最近活动
      </Typography.Title>
      <Table
        size="small"
        loading={activity === null}
        dataSource={activity ? [...activity] : []}
        pagination={false}
        rowKey={(row?: AuditRecord) => row?.id ?? ""}
        columns={[
          { title: "操作", dataIndex: "action" },
          { title: "对象", dataIndex: "resourceId" },
          { title: "执行者", dataIndex: "actorId" }
        ]}
      />
    </div>
  );
}
