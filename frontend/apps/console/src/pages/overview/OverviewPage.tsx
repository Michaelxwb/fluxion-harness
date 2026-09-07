import { useEffect, useState } from "react";

import { Button, Card, Table, Typography } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";

import type { AuditRecord, ConsoleApi, RunDetail } from "../../types/console";
import { ActionTag, isRiskyAction } from "../../components/ActionTag";
import { PageHeader } from "../../components/PageHeader";
import { RelativeTime } from "../../components/RelativeTime";
import { ResourceId } from "../../components/ResourceId";
import { StatusTag } from "../../components/StatusTag";

interface OverviewProps {
  readonly api: ConsoleApi;
}

interface CountCard {
  readonly label: string;
  readonly path: string;
  readonly value: number | null;
}

/** TASK-023（§8.12）：管理员异常工作台——异常卡片组（数据源对应各域 API，
 * 不新建旁路查询）+ 点击跳转目标页带过滤参数；计数卡片降为次要信息层级；
 * 最近活动保留。 */
export function OverviewPage({ api }: OverviewProps) {
  const navigate = useNavigate();
  const [counts, setCounts] = useState<CountCard[]>(() => [
    { label: "智能体", path: "/build/agents", value: null },
    { label: "工作流", path: "/build/workflows", value: null },
    { label: "用户", path: "/users", value: null },
    { label: "执行记录", path: "/operations/runs", value: null }
  ]);
  const [activity, setActivity] = useState<readonly AuditRecord[] | null>(null);
  const [failedRuns, setFailedRuns] = useState<readonly RunDetail[] | null>(null);
  const [revokedCredentials, setRevokedCredentials] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // FEAT-03：计数取服务端 total（不再依赖首屏 100 条数组长度）；
      // 异常工作台取最近 5 条失败记录（有意 top-N）。
      const [agents, workflows, users, runs, failed, auditPage, credentials] = await Promise.all([
        api.listResources("agent_definition", { page: 1, pageSize: 1 }),
        api.listResources("workflow", { page: 1, pageSize: 1 }),
        api.listPlatformUsers({ page: 1, pageSize: 1 }),
        api.listRuns({ page: 1, pageSize: 1 }),
        api.listRuns({ page: 1, pageSize: 5, status: "failed" }),
        api.listAudit({ page: 1, pageSize: 5 }),
        api.listCredentials().catch(() => [])
      ]);
      if (cancelled) {
        return;
      }
      setCounts([
        { label: "智能体", path: "/build/agents", value: agents.total },
        { label: "工作流", path: "/build/workflows", value: workflows.total },
        { label: "用户", path: "/users", value: users.total },
        { label: "执行记录", path: "/operations/runs", value: runs.total }
      ]);
      setActivity(auditPage.items);
      setFailedRuns(failed.items);
      setRevokedCredentials(credentials.filter((item) => item.status === "disabled").length);
    })();
    return () => {
      cancelled = true;
    };
  }, [api]);

  const abnormal = failedRuns !== null && failedRuns.length > 0;
  const credentialsAbnormal = (revokedCredentials ?? 0) > 0;
  const allQuiet = !abnormal && !credentialsAbnormal && failedRuns !== null && revokedCredentials !== null;

  return (
    <div>
      <PageHeader title="概览" description="平台对象计数、异常工作台与最近操作轨迹" />

      <div aria-label="异常工作台" className="page-stack" style={{ gap: 12 }}>
        <Typography.Title heading={5}>异常工作台</Typography.Title>
        {allQuiet ? (
          <Typography.Text type="tertiary">一切正常：暂无失败执行，凭据状态正常。</Typography.Text>
        ) : (
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
          <Card aria-label="异常卡片-最近异常运行">
            <Typography.Text strong>最近异常运行</Typography.Text>
            <Typography.Title
              heading={3}
              type={abnormal ? "danger" : undefined}
              style={{ margin: "8px 0 4px" }}
            >
              {failedRuns === null ? "…" : failedRuns.length}
            </Typography.Title>
            <Typography.Text type="tertiary">
              {failedRuns === null
                ? "加载中"
                : abnormal
                  ? (
                    <span>
                      最近失败：
                      <ResourceId id={failedRuns[0]?.executionId ?? ""} />
                    </span>
                  )
                  : "暂无失败执行"}
            </Typography.Text>
            <div style={{ paddingTop: 12 }}>
              <Button
                aria-label="查看异常运行"
                disabled={!abnormal}
                onClick={() => navigate("/operations/runs?statusFilter=failed")}
                size="small"
              >
                查看异常运行
              </Button>
            </div>
          </Card>
          <Card aria-label="异常卡片-凭据异常">
            <Typography.Text strong>已撤销凭据</Typography.Text>
            <Typography.Title
              heading={3}
              type={credentialsAbnormal ? "danger" : undefined}
              style={{ margin: "8px 0 4px" }}
            >
              {revokedCredentials === null ? "…" : revokedCredentials}
            </Typography.Title>
            <Typography.Text type="tertiary">
              {credentialsAbnormal ? "存在已撤销凭据，绑定方将 fail-closed" : "凭据状态正常"}
            </Typography.Text>
            <div style={{ paddingTop: 12 }}>
              <Button
                aria-label="查看凭据"
                disabled={!credentialsAbnormal}
                onClick={() => navigate("/platform/credentials")}
                size="small"
              >
                查看凭据
              </Button>
            </div>
          </Card>
          <Card aria-label="异常卡片-最近治理活动">
            <Typography.Text strong>最近治理活动</Typography.Text>
            <div style={{ display: "grid", gap: 4, paddingTop: 8 }}>
              {(activity ?? []).slice(0, 3).map((record) => (
                <Typography.Text key={record.id} type={isRiskyAction(record.action) ? "danger" : undefined} style={{ fontSize: 12 }}>
                  <ActionTag action={record.action} /> <ResourceId id={record.resourceId} />
                </Typography.Text>
              ))}
              {activity !== null && activity.length === 0 ? (
                <Typography.Text type="tertiary">暂无活动</Typography.Text>
              ) : null}
            </div>
            <div style={{ paddingTop: 12 }}>
              <Button
                aria-label="查看审计"
                onClick={() => navigate("/governance/audit")}
                size="small"
              >
                查看审计
              </Button>
            </div>
          </Card>
        </div>
        )}
      </div>

      <div aria-label="异常运行列表" style={{ marginTop: 16 }}>
        {abnormal ? (
          <Table<RunDetail>
            columns={[
              {
                dataIndex: "executionId",
                render: (value: string) => <ResourceId id={value} />,
                title: "异常执行"
              },
              {
                dataIndex: "status",
                render: (status: RunDetail["status"]) => <StatusTag status={status} />,
                title: "状态"
              },
              {
                dataIndex: "startedAt",
                render: (value: string) => <RelativeTime value={value} />,
                title: "开始时间"
              }
            ]}
            dataSource={[...(failedRuns ?? [])].slice(0, 5)}
            pagination={false}
            rowKey="executionId"
            size="small"
          />
        ) : null}
      </div>

      <Typography.Title heading={5} style={{ marginTop: 16 }} type="tertiary">
        平台对象
      </Typography.Title>
      <div className="overview-cards">
        {counts.map((card) => (
          <div
            key={card.label}
            aria-label={`count-${card.label}`}
            className="overview-card--link"
            onClick={() => navigate(card.path)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                navigate(card.path);
              }
            }}
            role="button"
            tabIndex={0}
          >
            <Card className="overview-card">
              <Typography.Title heading={4}>
                {card.value === null ? "…" : card.value}
              </Typography.Title>
              <Typography.Text type="tertiary">{card.label}</Typography.Text>
            </Card>
          </div>
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
          {
            dataIndex: "action",
            render: (value: string) => <ActionTag action={value} />,
            title: "操作"
          },
          {
            dataIndex: "resourceId",
            render: (value: string) => <ResourceId id={value} />,
            title: "对象"
          },
          { dataIndex: "actorId", title: "执行者" },
          {
            dataIndex: "at",
            render: (value: string) => <RelativeTime value={value} />,
            title: "时间"
          }
        ]}
      />
    </div>
  );
}
