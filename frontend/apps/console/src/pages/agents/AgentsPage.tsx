import { useEffect, useMemo, useState } from "react";

import { IconPlus } from "@douyinfe/semi-icons";
import { Button, Empty, Modal, Select, Table, Toast } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "../../components/PageHeader";
import {
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type {
  BindingRecord,
  ConsoleApi,
  JsonRecord,
  ResourceStatus,
  ResourceSummary
} from "../../types/console";
import { AgentDetailSideSheet } from "./AgentDetailSideSheet";
import { CreateAgentModal } from "./CreateAgentModal";

interface AgentsPageProps {
  readonly api: ConsoleApi;
}

interface AgentRow extends ResourceSummary {
  readonly key: string;
  readonly modelId: string;
}

const PAGE_SIZE = 10;

function countBindings(bindings: readonly BindingRecord[]): ReadonlyMap<string, number> {
  const counts = new Map<string, number>();
  for (const binding of bindings) {
    counts.set(binding.resourceId, (counts.get(binding.resourceId) ?? 0) + 1);
  }
  return counts;
}

function primaryModelId(spec: JsonRecord): string {
  const policy = spec.model_policy as JsonRecord | undefined;
  const reference = policy?.primary_model_ref as JsonRecord | undefined;
  return String(reference?.id ?? "-");
}

/** TASK-011：智能体标准列表——领域筛选、单套分页、只读详情与受治理行操作。 */
export function AgentsPage({ api }: AgentsPageProps) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<readonly AgentRow[] | null>(null);
  const [bindingCounts, setBindingCounts] = useState<ReadonlyMap<string, number>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    setRows(null);
    void (async () => {
      try {
        const [agentsPage, bindingsPage] = await Promise.all([
          api.listResources("agent_definition"),
          api.listBindings({ page: 1, pageSize: 100 }, "agent_definition")
        ]);
        const details = await Promise.all(
          agentsPage.items.map((agent) =>
            api.getResource("agent_definition", agent.resourceId, agent.currentVersion)
          )
        );
        if (!active) return;
        setRows(
          agentsPage.items
            .map((agent, index) => ({
              ...agent,
              key: agent.resourceId,
              modelId: primaryModelId(details[index].spec)
            }))
            .sort((left, right) =>
              left.displayName.localeCompare(right.displayName, "zh-CN", { numeric: true })
            )
        );
        setBindingCounts(countBindings(bindingsPage.items));
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  const modelOptions = useMemo(
    () => [...new Set((rows ?? []).map((row) => row.modelId).filter((id) => id !== "-"))].sort(),
    [rows]
  );
  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (rows ?? []).filter((row) => {
      if (statusFilter && row.status !== statusFilter) return false;
      if (modelFilter && row.modelId !== modelFilter) return false;
      return (
        !keyword ||
        row.displayName.toLowerCase().includes(keyword) ||
        row.resourceId.toLowerCase().includes(keyword)
      );
    });
  }, [modelFilter, rows, search, statusFilter]);
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function reload(): void {
    setReloadKey((key) => key + 1);
  }

  async function publishAgent(row: AgentRow): Promise<void> {
    try {
      const resource = await api.getResource("agent_definition", row.resourceId, row.currentVersion);
      await api.publishVersion(resource);
      Toast.success("智能体已发布");
      reload();
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "发布失败");
    }
  }

  async function copyAgent(row: AgentRow): Promise<void> {
    try {
      const source = await api.getResource("agent_definition", row.resourceId, row.currentVersion);
      const created = await api.createAgent({ ...source.spec, name: `${source.spec.name} 副本` });
      Toast.success("智能体副本已创建");
      navigate(`/build/agents/${created.resourceId}/edit`);
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "复制失败");
    }
  }

  function confirmDelete(row: AgentRow): void {
    const affectedBindings = bindingCounts.get(row.resourceId) ?? 0;
    Modal.confirm({
      title: `删除智能体「${row.displayName}」？`,
      content: `将把当前发布版本标记为已弃用；影响 1 个发布版本和 ${affectedBindings} 个绑定。运行中的 ExecutionSnapshot 不受影响。`,
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          const resource = await api.getResource("agent_definition", row.resourceId, row.currentVersion);
          if (resource.status !== "published") {
            Toast.warning("草稿不可删除；请先发布，或在版本治理中处理未发布版本");
            return;
          }
          await api.deprecateVersion(resource, "Console 列表删除操作");
          Toast.success("智能体版本已弃用");
          reload();
        } catch (cause) {
          Toast.error(cause instanceof Error ? cause.message : "删除失败");
        }
      }
    });
  }

  return (
    <div className="page-stack">
      <PageHeader
        description="以智能体为中心构建、预览与发布；每个智能体绑定自己的能力与运行配置。"
        title="智能体"
      />
      <div aria-label="智能体列表">
        <StandardListCard
          empty={rows !== null && rows.length === 0}
          emptyDescription="暂无智能体"
          error={error}
          footer={
            rows !== null && rows.length > 0 ? (
              <StandardListFooter
                onPageChange={setPage}
                page={page}
                pageSize={PAGE_SIZE}
                total={filtered.length}
              />
            ) : undefined
          }
          loading={rows === null && !error}
          onRetry={reload}
          toolbar={
            <StandardListToolbar
              filters={
                <>
                  <span className="sr-only" id="agent-status-filter-label">
                    状态过滤
                  </span>
                  <Select
                    aria-labelledby="agent-status-filter-label"
                    onChange={(value) => {
                      setStatusFilter(String(value ?? ""));
                      setPage(1);
                    }}
                    optionList={[
                      { label: "全部状态", value: "" },
                      { label: "草稿", value: "draft" },
                      { label: "已发布", value: "published" },
                      { label: "已弃用", value: "deprecated" }
                    ]}
                    placeholder="状态"
                    style={{ width: 130 }}
                    value={statusFilter}
                  />
                  <span className="sr-only" id="agent-model-filter-label">
                    主模型过滤
                  </span>
                  <Select
                    aria-labelledby="agent-model-filter-label"
                    onChange={(value) => {
                      setModelFilter(String(value ?? ""));
                      setPage(1);
                    }}
                    optionList={[
                      { label: "全部模型", value: "" },
                      ...modelOptions.map((modelId) => ({ label: modelId, value: modelId }))
                    ]}
                    placeholder="主模型"
                    style={{ width: 180 }}
                    value={modelFilter}
                  />
                </>
              }
              primary={
                <Button
                  aria-label="新建智能体"
                  icon={<IconPlus />}
                  onClick={() => setCreateOpen(true)}
                  theme="solid"
                  type="primary"
                >
                  新建智能体
                </Button>
              }
              search={
                <StandardListSearch
                  onChange={(value) => {
                    setSearch(value);
                    setPage(1);
                  }}
                  placeholder="搜索名称 / 资源 ID"
                  value={search}
                />
              }
            />
          }
        >
          <Table
            columns={[
              {
                title: "名称",
                dataIndex: "displayName",
                render: (value: string, record: AgentRow) => (
                  <Button
                    aria-label={`查看智能体 ${value}`}
                    onClick={() => setSelectedAgentId(record.resourceId)}
                    theme="borderless"
                    type="tertiary"
                  >
                    {value}
                  </Button>
                )
              },
              { title: "资源 ID", dataIndex: "resourceId" },
              { title: "主模型", dataIndex: "modelId" },
              {
                title: "状态",
                dataIndex: "status",
                render: (value: string) => <StatusTag status={value as ResourceStatus} />
              },
              { title: "版本", dataIndex: "currentVersion" },
              { title: "更新时间", dataIndex: "updatedAt" },
              {
                title: "操作",
                render: (_value: unknown, record: AgentRow) => (
                  <RowActions
                    immediate={[
                      {
                        key: "edit",
                        content: (
                          <>
                            编辑<span className="sr-only"> {record.resourceId}</span>
                          </>
                        ),
                        onClick: () => navigate(`/build/agents/${record.resourceId}/edit`)
                      }
                    ]}
                    more={[
                      {
                        key: "history",
                        content: "查看版本历史",
                        onClick: () => setSelectedAgentId(record.resourceId)
                      },
                      {
                        key: "publish",
                        content: "发布",
                        onClick: () => void publishAgent(record)
                      },
                      {
                        key: "copy",
                        content: "复制",
                        onClick: () => void copyAgent(record)
                      },
                      { key: "delete", content: "删除", onClick: () => confirmDelete(record) }
                    ]}
                  />
                )
              }
            ]}
            dataSource={paged}
            empty={<Empty description="暂无智能体" />}
            pagination={false}
            rowKey="key"
          />
        </StandardListCard>
      </div>
      <CreateAgentModal
        api={api}
        onClose={() => setCreateOpen(false)}
        onCreated={(resourceId) => {
          setCreateOpen(false);
          navigate(`/build/agents/${resourceId}/edit`);
        }}
        visible={createOpen}
      />
      <AgentDetailSideSheet
        api={api}
        onClose={() => setSelectedAgentId(null)}
        resourceId={selectedAgentId}
      />
    </div>
  );
}
