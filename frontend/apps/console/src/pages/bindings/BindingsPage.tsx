import { useEffect, useState } from "react";

import { Button, Card, Empty, Input, Modal, Select, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { ListPager } from "../../components/ListPager";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import type {
  BindingRecord,
  ConsoleApi,
  CredentialMetadata,
  ResourceSummary,
  ResourceType
} from "../../types/console";

interface BindingsPageProps {
  readonly api: ConsoleApi;
}

const BINDING_PAGE_SIZE = 20;

export function BindingsPage({ api }: BindingsPageProps) {
  const [bindings, setBindings] = useState<readonly BindingRecord[]>([]);
  const [bindingTotal, setBindingTotal] = useState(0);
  const [bindingPage, setBindingPage] = useState(1);
  const [resources, setResources] = useState<readonly ResourceSummary[]>([]);
  const [credentials, setCredentials] = useState<readonly CredentialMetadata[]>([]);
  const [resourceType, setResourceType] = useState<ResourceType | "all">("all");
  const [bindOpen, setBindOpen] = useState(false);
  const [bindType, setBindType] = useState<ResourceType>("mcp");
  const [bindResourceId, setBindResourceId] = useState<string>();
  const [bindNonce, setBindNonce] = useState(0);
  const [bindSubjectId, setBindSubjectId] = useState("bind-user-001");
  const [bindCredentialRef, setBindCredentialRef] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 绑定列表按资源类型过滤走后端分页；可绑定资源/凭据与过滤无关，加载一次即可。
  useEffect(() => {
    void loadBindings(1);
  }, [resourceType]);

  useEffect(() => {
    void loadSupportingData();
  }, []);

  async function loadSupportingData(): Promise<void> {
    try {
      const [resourcePage, credentialPage] = await Promise.all([
        api.listResources(),
        api.listCredentials()
      ]);
      setResources(resourcePage.items);
      setCredentials(credentialPage);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  async function loadBindings(page: number): Promise<void> {
    try {
      const pageData = await api.listBindings(
        { page, pageSize: BINDING_PAGE_SIZE },
        resourceType === "all" ? undefined : resourceType
      );
      setBindings(pageData.items);
      setBindingTotal(pageData.total);
      setBindingPage(page);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  function openBind(): void {
    setBindType(resourceType === "all" ? "mcp" : resourceType);
    setBindResourceId(undefined);
    setBindSubjectId("bind-user-001");
    setBindCredentialRef(credentials[0]?.credentialRef ?? null);
    setBindNonce((nonce) => nonce + 1);
    setBindOpen(true);
  }

  async function createBinding(): Promise<void> {
    if (!bindResourceId) return;
    try {
      await api.saveBinding({
        credentialRef: bindCredentialRef,
        resourceId: bindResourceId,
        resourceType: bindType,
        subjectId: bindSubjectId.trim(),
        subjectType: "user",
        versionSelector: "latest-published"
      });
      setBindOpen(false);
      setNotice("绑定已创建");
      await loadBindings(bindingPage);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  const modalResources = resources.filter((resource) => resource.resourceType === bindType);

  return (
    <div className="page-stack">
      <PageHeader description="绑定承载用户级差异，凭据只展示 SecretRef。" title="资源绑定" />
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <Card
        aria-label="绑定列表"
        bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }}
        header={
          <div className="list-card-header list-card-header--spread">
            <Space>
              <Button aria-label="新增绑定" icon={<IconPlus />} onClick={openBind} type="primary">新增绑定</Button>
            </Space>
            <Select
              aria-label="资源类型"
              data-testid="binding-resource-type-select"
              onChange={(value) => {
                if (value === "all" || isBindableResourceType(value)) setResourceType(value);
              }}
              optionList={[
                { label: "全部", value: "all" },
                ...BINDABLE_RESOURCE_TYPES.map((type) => ({ label: BINDABLE_RESOURCE_TYPE_LABELS[type], value: type }))
              ]}
              style={{ width: 160 }}
              value={resourceType}
            />
          </div>
        }
      >
        <Table
          columns={bindingColumns}
          dataSource={[...bindings]}
          empty={<Empty description="暂无绑定" />}
          pagination={false}
          rowKey="bindingId"
        />
        <ListPager onChange={(page) => void loadBindings(page)} page={bindingPage} pageSize={BINDING_PAGE_SIZE} total={bindingTotal} />
      </Card>
      {bindOpen ? (
        <Modal
          footer={
            <Space>
              <Button aria-label="取消" onClick={() => setBindOpen(false)}>取消</Button>
              <Button
                aria-label="创建绑定"
                disabled={!bindResourceId || !bindSubjectId.trim()}
                onClick={() => void createBinding()}
                theme="solid"
                type="primary"
              >
                创建绑定
              </Button>
            </Space>
          }
          onCancel={() => setBindOpen(false)}
          title="新增绑定"
          visible
        >
          <Space vertical align="start" style={{ width: "100%" }}>
            <Select
              aria-label="资源类型"
              onChange={(value) => {
                if (isBindableResourceType(value)) {
                  setBindType(value);
                  setBindResourceId(undefined);
                }
              }}
              optionList={BINDABLE_RESOURCE_TYPES.map((type) => ({ label: BINDABLE_RESOURCE_TYPE_LABELS[type], value: type }))}
              style={{ width: 200 }}
              value={bindType}
            />
            <Select
              key={`${bindType}-${bindNonce}`}
              aria-label="资源"
              onChange={(value) => setBindResourceId(typeof value === "string" ? value : undefined)}
              optionList={modalResources.map((resource) => ({ label: resource.resourceId, value: resource.resourceId }))}
              placeholder="选择要绑定的资源"
              style={{ width: 200 }}
            />
            <Input aria-label="主体 ID" onChange={setBindSubjectId} placeholder="主体 ID" value={bindSubjectId} />
            <Select
              aria-label="凭据引用"
              onChange={(value) => setBindCredentialRef(typeof value === "string" ? value : null)}
              optionList={credentials.map((credential) => ({ label: credential.credentialRef, value: credential.credentialRef }))}
              style={{ width: 200 }}
              value={bindCredentialRef ?? ""}
            />
          </Space>
        </Modal>
      ) : null}
    </div>
  );
}

const bindingColumns = [
  {
    dataIndex: "resourceType",
    render: (_value: unknown, record: BindingRecord) => BINDABLE_RESOURCE_TYPE_LABELS[record.resourceType],
    title: "类型"
  },
  { dataIndex: "subjectId", title: "主体" },
  { dataIndex: "resourceId", title: "资源" },
  { dataIndex: "credentialRef", title: "凭据引用" },
  {
    render: (_value: unknown, record: BindingRecord) => (
      <StatusTag status={record.enabled ? "active" : "deprecated"} />
    ),
    title: "状态"
  }
];

const BINDABLE_RESOURCE_TYPES: readonly ResourceType[] = ["skill", "mcp", "plugin", "policy"];

const BINDABLE_RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  runtime_profile: "运行态",
  skill: "技能",
  mcp: "MCP 工具",
  plugin: "插件",
  policy: "策略",
  workflow: "工作流",
  agent_definition: "智能体",
  model_provider: "模型服务",
  model_definition: "模型定义",
  tool: "工具",
  secret: "凭据",
  eval_set: "评测集"
};

function isBindableResourceType(value: unknown): value is ResourceType {
  return typeof value === "string" && BINDABLE_RESOURCE_TYPES.includes(value as ResourceType);
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
