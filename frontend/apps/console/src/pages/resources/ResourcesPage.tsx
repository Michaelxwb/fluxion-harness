import { useEffect, useState, type ReactNode } from "react";

import { Button, Card, Empty, Input, Modal, Select, SideSheet, Space, Table, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { ListPager } from "../../components/ListPager";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import { parseSpec } from "../../utils/json";
import type { ConsoleApi, ResourceSummary, ResourceType } from "../../types/console";
import { ResourceDetailPanel } from "./ResourceDetailPanel";

interface ResourcesPageProps {
  readonly api: ConsoleApi;
}

const RESOURCE_PAGE_SIZE = 20;

export function ResourcesPage({ api }: ResourcesPageProps) {
  const [resources, setResources] = useState<readonly ResourceSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<ResourceType | "all">("all");
  const [resourcePage, setResourcePage] = useState(1);
  const [drawerTarget, setDrawerTarget] = useState<{
    readonly resourceType: ResourceType;
    readonly resourceId: string;
  } | null>(null);
  const [createType, setCreateType] = useState<ResourceType>("runtime_profile");
  const [createOpen, setCreateOpen] = useState(false);
  const [createId, setCreateId] = useState("");
  const [createVersion, setCreateVersion] = useState("v1");
  const [createSpec, setCreateSpec] = useState("{}");

  useEffect(() => {
    void loadResources();
  }, []);

  async function loadResources(): Promise<void> {
    // 后端单表 resource_definitions，GET /api/v1/resources 一次返回全部类型。
    const result = await api.listResources();
    setResources(result.items);
  }

  async function createResource(): Promise<void> {
    try {
      const created = await api.createResource({
        resourceId: createId.trim(),
        resourceType: createType,
        spec: parseSpec(createSpec),
        version: createVersion.trim(),
        visibility: "private"
      });
      setCreateOpen(false);
      setCreateId("");
      setCreateVersion("v1");
      setCreateSpec("{}");
      await loadResources();
      setDrawerTarget({ resourceType: created.resourceType, resourceId: created.resourceId });
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  function openCreate(): void {
    setCreateType(typeFilter === "all" ? "runtime_profile" : typeFilter);
    setCreateOpen(true);
  }

  const visibleResources =
    typeFilter === "all"
      ? resources
      : resources.filter((resource) => resource.resourceType === typeFilter);
  const totalPages = Math.max(1, Math.ceil(visibleResources.length / RESOURCE_PAGE_SIZE));
  const currentPage = Math.min(resourcePage, totalPages);
  const pageResources = visibleResources.slice(
    (currentPage - 1) * RESOURCE_PAGE_SIZE,
    currentPage * RESOURCE_PAGE_SIZE
  );

  return (
    <div className="page-stack">
      <PageHeader description="管理所有类型的资源定义（运行态 / 技能 / MCP 工具 / 插件 / 策略），不创建运行实例（Pod）。" title="运行资产" />
      <ErrorBanner message={error} />
      <Card
        aria-label="资源列表"
        bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }}
        header={
          <div className="list-card-header list-card-header--spread">
            <Space>
              <Button aria-label="新增" icon={<IconPlus />} onClick={() => openCreate()} type="primary">新增</Button>
            </Space>
            <Select
              aria-label="类型筛选"
              onChange={(value) => {
                if (value === "all" || isResourceType(value)) {
                  setTypeFilter(value);
                  setResourcePage(1);
                }
              }}
              optionList={[
                { label: "全部", value: "all" },
                ...RESOURCE_TYPES.map((type) => ({ label: RESOURCE_TYPE_LABELS[type], value: type }))
              ]}
              style={{ width: 160 }}
              value={typeFilter}
            />
          </div>
        }
      >
        {typeFilter !== "all" ? (
          <Typography.Text type="tertiary">{RESOURCE_TYPE_HINTS[typeFilter]}</Typography.Text>
        ) : null}
        <ResourceTable
          empty={
            <Empty
              description={typeFilter === "all" ? "暂无资源" : `暂无「${RESOURCE_TYPE_LABELS[typeFilter]}」资源`}
            />
          }
          onSelect={(resourceType, resourceId) => setDrawerTarget({ resourceType, resourceId })}
          resources={pageResources}
        />
        <ListPager
          onChange={setResourcePage}
          page={currentPage}
          pageSize={RESOURCE_PAGE_SIZE}
          total={visibleResources.length}
        />
      </Card>
      {createOpen ? (
        <Modal
          footer={
            <Space>
              <Button aria-label="取消" onClick={() => setCreateOpen(false)}>取消</Button>
              <Button
                aria-label="创建草稿"
                disabled={!createId.trim() || !createVersion.trim()}
                onClick={() => void createResource()}
                theme="solid"
                type="primary"
              >
                创建草稿
              </Button>
            </Space>
          }
          onCancel={() => setCreateOpen(false)}
          title={`新建资源（${RESOURCE_TYPE_LABELS[createType]}）`}
          visible
        >
          <Space vertical align="start" style={{ width: "100%" }}>
            <Select
              aria-label="类型"
              onChange={(value) => {
                if (isResourceType(value)) setCreateType(value);
              }}
              optionList={RESOURCE_TYPES.map((type) => ({ label: RESOURCE_TYPE_LABELS[type], value: type }))}
              style={{ width: 200 }}
              value={createType}
            />
            <Typography.Text type="tertiary">{RESOURCE_TYPE_HINTS[createType]}</Typography.Text>
            <Input aria-label="资源 ID" onChange={setCreateId} placeholder="资源 ID" value={createId} />
            <Input aria-label="版本" onChange={setCreateVersion} value={createVersion} />
            <TextArea aria-label="新资源规格 JSON" autosize={{ minRows: 8, maxRows: 16 }} onChange={setCreateSpec} value={createSpec} />
          </Space>
        </Modal>
      ) : null}
      <SideSheet
        onCancel={() => setDrawerTarget(null)}
        title="资源详情"
        visible={drawerTarget !== null}
        width={980}
      >
        {drawerTarget ? (
          <ResourceDetailPanel
            api={api}
            resourceId={drawerTarget.resourceId}
            resourceType={drawerTarget.resourceType}
          />
        ) : null}
      </SideSheet>
    </div>
  );
}

const RESOURCE_TYPES: readonly ResourceType[] = [
  "runtime_profile",
  "skill",
  "mcp",
  "plugin",
  "policy",
  "workflow"
];

const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  runtime_profile: "运行态",
  skill: "技能",
  mcp: "MCP 工具",
  plugin: "插件",
  policy: "策略",
  workflow: "工作流"
};

const RESOURCE_TYPE_HINTS: Record<ResourceType, string> = {
  runtime_profile: "运行态是助手的运行档案：选用模型 / 系统提示词，以及可用技能、MCP 与工具。",
  skill: "技能是给助手的指令包，描述某项任务该怎么做。",
  mcp: "MCP 工具是通过 MCP 协议接入的外部工具服务器。",
  plugin: "插件是扩展运行时能力的扩展包（如模型供应商）。",
  policy: "策略是访问控制与护栏规则。",
  workflow: "工作流是多步骤编排流程。"
};

function isResourceType(value: unknown): value is ResourceType {
  return typeof value === "string" && RESOURCE_TYPES.includes(value as ResourceType);
}

function ResourceTable({
  empty,
  onSelect,
  resources
}: {
  readonly empty: ReactNode;
  readonly onSelect: (resourceType: ResourceType, resourceId: string) => void;
  readonly resources: readonly ResourceSummary[];
}) {
  const columns = [
    {
      dataIndex: "resourceType",
      render: (_value: unknown, record: ResourceSummary) => RESOURCE_TYPE_LABELS[record.resourceType],
      title: "类型"
    },
    {
      dataIndex: "resourceId",
      render: (_value: unknown, record: ResourceSummary) => (
        <Typography.Text link onClick={() => onSelect(record.resourceType, record.resourceId)}>
          {record.resourceId}
        </Typography.Text>
      ),
      title: "资源"
    },
    { dataIndex: "displayName", title: "名称" },
    { dataIndex: "currentVersion", title: "当前版本" },
    {
      render: (_value: unknown, record: ResourceSummary) => <StatusTag status={record.status} />,
      title: "状态"
    }
  ];
  return (
    <Table
      columns={columns}
      dataSource={[...resources]}
      empty={empty}
      pagination={false}
      rowKey={(record) => (record ? `${record.resourceType}/${record.resourceId}` : "")}
    />
  );
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
