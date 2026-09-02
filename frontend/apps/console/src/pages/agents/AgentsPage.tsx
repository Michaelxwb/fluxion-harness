import { useEffect, useState } from "react";

import { useNavigate } from "react-router-dom";
import { Button, Card, Empty, Spin, Table, Typography } from "@douyinfe/semi-ui";
import { IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import { AgentDetailSideSheet } from "./AgentDetailSideSheet";
import { CreateAgentModal } from "./CreateAgentModal";
import type { ConsoleApi, ResourceStatus, ResourceSummary } from "../../types/console";

interface AgentsPageProps {
  readonly api: ConsoleApi;
}

/** TASK-011/012：领域独立列表页——「智能体」只展示 AgentDefinition；CreateAgentModal 最小建档。 */
export function AgentsPage({ api }: AgentsPageProps) {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<readonly ResourceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    setAgents(null);
    void api
      .listResources("agent_definition")
      .then((page) => {
        if (active) setAgents(page.items);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <div className="page-stack">
      <PageHeader
        description="以智能体为中心构建、预览与发布；每个智能体绑定自己的能力与运行配置。"
        extra={
          <Button
            aria-label="新建智能体"
            icon={<IconPlus />}
            onClick={() => setCreateOpen(true)}
            type="primary"
          >
            新建智能体
          </Button>
        }
        title="智能体"
      />
      <ErrorBanner message={error} onRetry={() => setReloadKey((key) => key + 1)} />
      <CreateAgentModal
        api={api}
        onClose={() => setCreateOpen(false)}
        onCreated={(resourceId) => {
          setCreateOpen(false);
          // console-creation-flow-fix（CF-S-02）：创建即编辑——新建 draft 携带
          // resourceId 直达编辑器（编辑器 getResource 任意状态 + :working-draft 复用，
          // draft 立即可达，不再依赖列表刷新）。
          navigate(`/build/agents/${resourceId}/edit`);
        }}
        visible={createOpen}
      />
      <AgentDetailSideSheet
        api={api}
        onClose={() => setSelectedAgentId(null)}
        resourceId={selectedAgentId}
      />
      <Card aria-label="智能体列表">
        {agents === null ? (
          <div aria-label="智能体加载中">
            <Spin />
          </div>
        ) : agents.length === 0 ? (
          <Empty description="暂无智能体" />
        ) : (
          <Table
            columns={[
              {
                title: "名称",
                dataIndex: "displayName",
                render: (value: string, record: ResourceSummary) => (
                  <Typography.Text link onClick={() => setSelectedAgentId(record.resourceId)}>
                    {value}
                  </Typography.Text>
                )
              },
              { title: "资源 ID", dataIndex: "resourceId" },
              {
                title: "状态",
                dataIndex: "status",
                render: (value: string) => <StatusTag status={value as ResourceStatus} />
              },
              { title: "版本", dataIndex: "currentVersion" },
              { title: "更新时间", dataIndex: "updatedAt" },
              {
                title: "操作",
                render: (_value: unknown, record: ResourceSummary) => (
                  <Button
                    aria-label={`编辑 ${record.resourceId}`}
                    onClick={() => navigate(`/build/agents/${record.resourceId}/edit`)}
                    size="small"
                  >
                    编辑
                  </Button>
                )
              }
            ]}
            dataSource={agents.map((agent) => ({
              key: `${agent.resourceType}:${agent.resourceId}`,
              ...agent
            }))}
            empty={<Empty description="暂无智能体" />}
            pagination={false}
            size="middle"
          />
        )}
        {agents !== null && agents.length > 0 ? (
          <Typography.Text type="tertiary">共 {agents.length} 个智能体</Typography.Text>
        ) : null}
      </Card>
    </div>
  );
}
