import { useCallback, useEffect, useRef, useState } from "react";

import { IconPlus } from "@douyinfe/semi-icons";
import { Button, Modal, Select, Table, Tabs, Toast } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "../../components/PageHeader";
import { EmptyState } from "../../components/EmptyState";
import {
  DEFAULT_PAGE_SIZE,
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type {
  ConsoleApi,
  ResourceSummary,
  ResourceStatus,
  ResourceType
} from "../../types/console";
import { CreateSkillModal } from "./CreateSkillModal";
import { CreateMcpServerModal } from "./CreateMcpServerModal";
import { CreateToolModal } from "./CreateToolModal";

interface CapabilitiesPageProps {
  readonly api: ConsoleApi;
  readonly initialKind?: CapabilityKind;
  readonly onKindChange?: (kind: CapabilityKind) => void;
}

type CapabilityKind = "skill" | "tool" | "mcp";

const KIND_TABS: readonly { readonly key: CapabilityKind; readonly text: string }[] = [
  { key: "skill", text: "技能" },
  { key: "tool", text: "工具" },
  { key: "mcp", text: "MCP" }
];

interface ListRow {
  readonly key: string;
  readonly name: string;
  readonly resourceId: string;
  readonly version: string;
  readonly status: string;
  readonly refCount: number;
}

const KIND_EMPTY_TEXT: Record<CapabilityKind, { readonly title: string; readonly description: string }> = {
  skill: {
    description: "技能是可复用的 Prompt 与指令包，可被智能体挂载。先新建技能，再到智能体编辑器中挂载使用。",
    title: "暂无技能"
  },
  tool: {
    description: "工具是可被模型函数调用的外部操作。发布后即可在授权白名单与工作流节点中引用。",
    title: "暂无工具"
  },
  mcp: {
    description: "MCP 接入外部服务的能力网关。添加 Server 后，其下工具自动注册为可用能力。",
    title: "暂无 MCP Server"
  }
};

/** TASK-016（§8.3）：Capabilities 管理页。skill tab 已产品化（CreateSkillModal +
 * 独立 Editor + StandardListShell）；tool/mcp tab 维持 SchemaForm 内联新建，
 * 由 TASK-017/018 逐一收敛（收敛后 skill/tool/mcp 均不再 import SchemaForm）。 */
export function CapabilitiesPage({
  api,
  initialKind = "skill",
  onKindChange
}: CapabilitiesPageProps) {
  const navigate = useNavigate();
  const [kind, setKind] = useState<CapabilityKind>(initialKind);
  const [rows, setRows] = useState<readonly ListRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [createSkillOpen, setCreateSkillOpen] = useState(false);
  const [createToolOpen, setCreateToolOpen] = useState(false);
  const [createMcpOpen, setCreateMcpOpen] = useState(false);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const refresh = useCallback(async () => {
    requestSeq.current += 1;
    const requestId = requestSeq.current;
    setError(null);
    setRows(null);
    try {
      // FEAT-03：分页/搜索/状态全部服务端化（GET /api/v1/resources），防乱序覆盖。
      const result = await api.listResources(kind as ResourceType, {
        page,
        pageSize,
        keyword: debouncedSearch.trim() || undefined,
        status: (statusFilter || undefined) as ResourceStatus | undefined
      });
      // 被引用数：绑定页内 join（有界 100 条），失败不阻断列表。
      let counts = new Map<string, number>();
      try {
        const bindings = await api.listBindings({ page: 1, pageSize: 100 }, kind as ResourceType);
        counts = new Map<string, number>();
        for (const binding of bindings.items) {
          counts.set(binding.resourceId, (counts.get(binding.resourceId) ?? 0) + 1);
        }
      } catch {
        counts = new Map<string, number>();
      }
      if (requestId !== requestSeq.current) return;
      setRows(
        result.items.map((item: ResourceSummary) => ({
          key: `${item.resourceId}@${item.currentVersion}`,
          name: item.displayName || item.resourceId,
          refCount: counts.get(item.resourceId) ?? 0,
          resourceId: item.resourceId,
          version: item.currentVersion,
          status: item.status
        }))
      );
      setTotal(result.total);
    } catch (cause) {
      if (requestId !== requestSeq.current) return;
      setError(cause instanceof Error ? cause.message : "加载失败");
    }
  }, [api, debouncedSearch, kind, page, pageSize, statusFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh, reloadKey]);

  // ---- skill 行操作（TASK-016） ----

  function confirmDeleteSkill(row: ListRow): void {
    Modal.confirm({
      title: `删除技能「${row.name}」？`,
      content:
        "将把当前发布版本标记为已弃用；引用此技能的 Workflow/Agent 需重新校验。运行中的 ExecutionSnapshot 不受影响。",
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          const resource = await api.getResource("skill", row.resourceId, row.version);
          if (resource.status !== "published") {
            Toast.warning("草稿不可删除；请先发布，或在版本治理中处理未发布版本");
            return;
          }
          await api.deprecateVersion(resource, "Console 列表删除操作");
          Toast.success("技能版本已弃用");
          reload();
        } catch (cause) {
          Toast.error(cause instanceof Error ? cause.message : "删除失败");
        }
      }
    });
  }

  async function publishSkill(row: ListRow): Promise<void> {
    try {
      const resource = await api.getResource("skill", row.resourceId, row.version);
      await api.publishVersion(resource);
      Toast.success("技能已发布");
      reload();
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "发布失败");
    }
  }

  function confirmDeleteTool(row: ListRow): void {
    Modal.confirm({
      title: `删除工具「${row.name}」？`,
      content:
        "将把当前发布版本标记为已弃用；引用此工具的 Agent 需重新校验授权链。运行中的 ExecutionSnapshot 不受影响。",
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          const resource = await api.getResource("tool", row.resourceId, row.version);
          if (resource.status !== "published") {
            Toast.warning("草稿不可删除；请先发布，或在版本治理中处理未发布版本");
            return;
          }
          await api.deprecateVersion(resource, "Console 列表删除操作");
          Toast.success("工具版本已弃用");
          reload();
        } catch (cause) {
          Toast.error(cause instanceof Error ? cause.message : "删除失败");
        }
      }
    });
  }

  async function publishTool(row: ListRow): Promise<void> {
    try {
      const resource = await api.getResource("tool", row.resourceId, row.version);
      await api.publishVersion(resource);
      Toast.success("工具已发布");
      reload();
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "发布失败");
    }
  }

  function confirmDeleteMcp(row: ListRow): void {
    Modal.confirm({
      title: `删除 MCP Server「${row.name}」？`,
      content:
        "将把当前发布版本标记为已弃用；引用此 MCP 的 Agent 用户授权链需重新校验。运行中的 ExecutionSnapshot 不受影响。",
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          const resource = await api.getResource("mcp", row.resourceId, row.version);
          if (resource.status !== "published") {
            Toast.warning("草稿不可删除；请先发布，或在版本治理中处理未发布版本");
            return;
          }
          await api.deprecateVersion(resource, "Console 列表删除操作");
          Toast.success("MCP 版本已弃用");
          reload();
        } catch (cause) {
          Toast.error(cause instanceof Error ? cause.message : "删除失败");
        }
      }
    });
  }

  async function publishMcp(row: ListRow): Promise<void> {
    try {
      const resource = await api.getResource("mcp", row.resourceId, row.version);
      await api.publishVersion(resource);
      Toast.success("MCP 已发布");
      reload();
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "发布失败");
    }
  }

  function reload(): void {
    setReloadKey((key) => key + 1);
  }

  return (
    <div>
      <PageHeader title="能力" description="技能 / 工具 / MCP 三类能力资源的统一管理入口" />
      <Tabs
        activeKey={kind}
        onChange={(key) => {
          if (!isCapabilityKind(key)) return;
          setKind(key);
          setSearch("");
          setStatusFilter("");
          setPage(1);
          onKindChange?.(key);
        }}
      >
        {KIND_TABS.map((tab) => (
          <Tabs.TabPane itemKey={tab.key} key={tab.key} tab={tab.text} />
        ))}
      </Tabs>

      {kind === "tool" ? (
        <div aria-label="工具列表">
          <StandardListCard
            empty={rows !== null && total === 0}
            emptyDescription="暂无工具"
            error={error}
            footer={
              rows !== null && total > 0 ? (
                <StandardListFooter
                  onPageChange={setPage}
                  onPageSizeChange={(next) => {
                    setPageSize(next);
                    setPage(1);
                  }}
                  page={page}
                  pageSize={pageSize}
                  total={total}
                />
              ) : undefined
            }
            loading={rows === null && !error}
            onRetry={reload}
            toolbar={
              <StandardListToolbar
                filters={
                  <>
                    <span className="sr-only" id="tool-status-filter-label">
                      工具状态过滤
                    </span>
                    <Select
                    aria-labelledby="tool-status-filter-label"
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
                  </>
                }
                primary={
                  <Button
                    aria-label="新建 Tool"
                    icon={<IconPlus />}
                    onClick={() => setCreateToolOpen(true)}
                    theme="solid"
                    type="primary"
                  >
                    新建 Tool
                  </Button>
                }
                search={
                  <StandardListSearch
                    onChange={(value) => {
                      setSearch(value);
                    }}
                    placeholder="搜索工具"
                    value={search}
                  />
                }
              />
            }
          >
            <Table<ListRow>
              aria-label="工具列表表格"
              empty={
                <EmptyState
                  description={KIND_EMPTY_TEXT.tool.description}
                  title={KIND_EMPTY_TEXT.tool.title}
                />
              }
              columns={[
                {
                  dataIndex: "name",
                  render: (_value, record) => (
                    <Button
                      onClick={() => navigate(`/build/tools/${record.resourceId}/edit`)}
                      theme="borderless"
                      type="primary"
                    >
                      {record.name}
                    </Button>
                  ),
                  title: "名称"
                },
                { dataIndex: "resourceId", title: "ID" },
                { dataIndex: "version", title: "版本" },
                {
                  dataIndex: "status",
                  render: (status: string) => <StatusTag status={status as ResourceStatus} />,
                  title: "状态"
                },
                {
                  dataIndex: "refCount",
                  render: (value: number) => (value > 0 ? `${value} 处引用` : "—"),
                  title: "引用"
                },
                {
                  dataIndex: "resourceId",
                  render: (_value, record) => (
                    <RowActions
                      immediate={[
                        {
                          key: "edit",
                          content: "编辑",
                          onClick: () => navigate(`/build/tools/${record.resourceId}/edit`)
                        }
                      ]}
                      more={[
                        { key: "publish", content: "发布", onClick: () => void publishTool(record) },
                        { key: "delete", content: "删除", onClick: () => confirmDeleteTool(record) }
                      ]}
                    />
                  ),
                  title: "操作"
                }
              ]}
              dataSource={[...(rows ?? [])]}
              pagination={false}
              rowKey="key"
            />
          </StandardListCard>
        </div>
      ) : kind === "skill" ? (
        <div aria-label="技能列表">
          <StandardListCard
            empty={rows !== null && total === 0}
            emptyDescription="暂无技能"
            error={error}
            footer={
              rows !== null && total > 0 ? (
                <StandardListFooter
                  onPageChange={setPage}
                  onPageSizeChange={(next) => {
                    setPageSize(next);
                    setPage(1);
                  }}
                  page={page}
                  pageSize={pageSize}
                  total={total}
                />
              ) : undefined
            }
            loading={rows === null && !error}
            onRetry={reload}
            toolbar={
              <StandardListToolbar
                filters={
                  <>
                    <span className="sr-only" id="skill-status-filter-label">
                      技能状态过滤
                    </span>
                    <Select
                    aria-labelledby="skill-status-filter-label"
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
                  </>
                }
                primary={
                  <Button
                    aria-label="新建 Skill"
                    icon={<IconPlus />}
                    onClick={() => setCreateSkillOpen(true)}
                    theme="solid"
                    type="primary"
                  >
                    新建 Skill
                  </Button>
                }
                search={
                  <StandardListSearch
                    onChange={(value) => {
                      setSearch(value);
                    }}
                    placeholder="搜索技能"
                    value={search}
                  />
                }
              />
            }
          >
            <Table<ListRow>
              aria-label="技能列表表格"
              empty={
                <EmptyState
                  description={KIND_EMPTY_TEXT.skill.description}
                  title={KIND_EMPTY_TEXT.skill.title}
                />
              }
              columns={[
                {
                  dataIndex: "name",
                  render: (_value, record) => (
                    <Button
                      onClick={() => navigate(`/build/skills/${record.resourceId}/edit`)}
                      theme="borderless"
                      type="primary"
                    >
                      {record.name}
                    </Button>
                  ),
                  title: "名称"
                },
                { dataIndex: "resourceId", title: "ID" },
                { dataIndex: "version", title: "版本" },
                {
                  dataIndex: "status",
                  render: (status: string) => <StatusTag status={status as ResourceStatus} />,
                  title: "状态"
                },
                {
                  dataIndex: "refCount",
                  render: (value: number) => (value > 0 ? `${value} 处引用` : "—"),
                  title: "引用"
                },
                {
                  dataIndex: "resourceId",
                  render: (_value, record) => (
                    <RowActions
                      immediate={[
                        {
                          key: "edit",
                          content: "编辑",
                          onClick: () => navigate(`/build/skills/${record.resourceId}/edit`)
                        }
                      ]}
                      more={[
                        { key: "publish", content: "发布", onClick: () => void publishSkill(record) },
                        { key: "delete", content: "删除", onClick: () => confirmDeleteSkill(record) }
                      ]}
                    />
                  ),
                  title: "操作"
                }
              ]}
              dataSource={[...(rows ?? [])]}
              pagination={false}
              rowKey="key"
            />
          </StandardListCard>
        </div>
      ) : (
        <div aria-label="MCP 列表">
          <StandardListCard
            empty={rows !== null && total === 0}
            emptyDescription="暂无 MCP Server"
            error={error}
            footer={
              rows !== null && total > 0 ? (
                <StandardListFooter
                  onPageChange={setPage}
                  onPageSizeChange={(next) => {
                    setPageSize(next);
                    setPage(1);
                  }}
                  page={page}
                  pageSize={pageSize}
                  total={total}
                />
              ) : undefined
            }
            loading={rows === null && !error}
            onRetry={reload}
            toolbar={
              <StandardListToolbar
                filters={
                  <>
                    <span className="sr-only" id="mcp-status-filter-label">
                      MCP 状态过滤
                    </span>
                    <Select
                    aria-labelledby="mcp-status-filter-label"
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
                  </>
                }
                primary={
                  <Button
                    aria-label="添加 MCP Server"
                    icon={<IconPlus />}
                    onClick={() => setCreateMcpOpen(true)}
                    theme="solid"
                    type="primary"
                  >
                    添加 MCP Server
                  </Button>
                }
                search={
                  <StandardListSearch
                    onChange={(value) => {
                      setSearch(value);
                    }}
                    placeholder="搜索 MCP"
                    value={search}
                  />
                }
              />
            }
          >
            <Table<ListRow>
              aria-label="MCP 列表表格"
              empty={
                <EmptyState
                  description={KIND_EMPTY_TEXT.mcp.description}
                  title={KIND_EMPTY_TEXT.mcp.title}
                />
              }
              columns={[
                {
                  dataIndex: "name",
                  render: (_value, record) => (
                    <Button
                      onClick={() => navigate(`/build/mcp/${record.resourceId}/edit`)}
                      theme="borderless"
                      type="primary"
                    >
                      {record.name}
                    </Button>
                  ),
                  title: "名称"
                },
                { dataIndex: "resourceId", title: "ID" },
                { dataIndex: "version", title: "版本" },
                {
                  dataIndex: "status",
                  render: (status: string) => <StatusTag status={status as ResourceStatus} />,
                  title: "状态"
                },
                {
                  dataIndex: "refCount",
                  render: (value: number) => (value > 0 ? `${value} 处引用` : "—"),
                  title: "引用"
                },
                {
                  dataIndex: "resourceId",
                  render: (_value, record) => (
                    <RowActions
                      immediate={[
                        {
                          key: "edit",
                          content: "编辑",
                          onClick: () => navigate(`/build/mcp/${record.resourceId}/edit`)
                        }
                      ]}
                      more={[
                        { key: "publish", content: "发布", onClick: () => void publishMcp(record) },
                        { key: "delete", content: "删除", onClick: () => confirmDeleteMcp(record) }
                      ]}
                    />
                  ),
                  title: "操作"
                }
              ]}
              dataSource={[...(rows ?? [])]}
              pagination={false}
              rowKey="key"
            />
          </StandardListCard>
        </div>
      )}

      <CreateMcpServerModal
        api={api}
        onClose={() => setCreateMcpOpen(false)}
        onCreated={(created) => {
          setCreateMcpOpen(false);
          reload();
          navigate(`/build/mcp/${created.resourceId}/edit`);
        }}
        visible={createMcpOpen}
      />
      <CreateToolModal
        api={api}
        onClose={() => setCreateToolOpen(false)}
        onCreated={(created) => {
          setCreateToolOpen(false);
          reload();
          navigate(`/build/tools/${created.resourceId}/edit`);
        }}
        visible={createToolOpen}
      />
      <CreateSkillModal
        api={api}
        onClose={() => setCreateSkillOpen(false)}
        onCreated={(created) => {
          setCreateSkillOpen(false);
          reload();
          navigate(`/build/skills/${created.resourceId}/edit`);
        }}
        visible={createSkillOpen}
      />
    </div>
  );
}

function isCapabilityKind(value: unknown): value is CapabilityKind {
  return value === "skill" || value === "tool" || value === "mcp";
}
