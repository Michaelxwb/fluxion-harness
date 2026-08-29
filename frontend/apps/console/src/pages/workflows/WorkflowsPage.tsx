import { useEffect, useState } from "react";

import { Button, Card, Descriptions, Empty, Modal, Space, Table, Tabs, Typography } from "@douyinfe/semi-ui";
import { IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import { JsonEditorTab } from "../../components/studio/JsonEditorTab";
import { NodeConfigForm } from "../../components/studio/NodeConfigForm";
import { StudioToolbar } from "../../components/studio/StudioToolbar";
import { WorkflowNodeList } from "../../components/studio/WorkflowNodeList";
import type {
  ConsoleApi,
  JsonRecord,
  ResourceSummary,
  ResourceVersion,
  WorkflowDraftV2,
  WorkflowV2Diagnostic,
  WorkflowV2Node
} from "../../types/console";

interface WorkflowsPageProps {
  readonly api: ConsoleApi;
}

/** C403 Workflow Studio（TASK-012）：表单模式（V2 判别联合节点编辑）+ JSON 高级模式。 */
export function WorkflowsPage({ api }: WorkflowsPageProps) {
  const [workflows, setWorkflows] = useState<readonly ResourceSummary[]>([]);
  const [selected, setSelected] = useState<ResourceVersion | null>(null);
  const [versions, setVersions] = useState<readonly ResourceVersion[]>([]);
  const [specText, setSpecText] = useState("{}");
  const [draft, setDraft] = useState<WorkflowDraftV2 | null>(null);
  const [selectedNodeIndex, setSelectedNodeIndex] = useState<number | null>(null);
  const [validatedVersion, setValidatedVersion] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<readonly WorkflowV2Diagnostic[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmVisible, setConfirmVisible] = useState(false);

  useEffect(() => {
    void loadWorkflows();
  }, []);

  async function loadWorkflows(): Promise<void> {
    const result = await api.listResources("workflow");
    setWorkflows(result.items);
  }

  async function selectWorkflow(summary: ResourceSummary): Promise<void> {
    await runAction(async () => {
      const resource = await api.getResource("workflow", summary.resourceId);
      await selectResource(resource);
    });
  }

  async function selectResource(resource: ResourceVersion): Promise<void> {
    const result = await api.listVersions("workflow", resource.resourceId, {
      page: 1,
      pageSize: 20
    });
    const nextDraft = parseDraft(resource.spec);
    setSelected(resource);
    setSpecText(JSON.stringify(resource.spec, null, 2));
    setDraft(nextDraft);
    setSelectedNodeIndex(nextDraft.steps.length > 0 ? 0 : null);
    setVersions(result.items);
    setValidatedVersion(null);
    setDiagnostics([]);
    // P2（review）：engine_ref 是 V1 legacy 顶层字段，V2 契约（extra=forbid）拒绝——
    // 保存时会移除。加载时显式提示，避免静默字段丢失。
    if (resource.spec && typeof resource.spec.engine_ref === "string") {
      setNotice("该 spec 含已弃用的 engine_ref（V2 不进入 Product DSL），保存后将被移除");
    }
  }

  async function createDraft(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      const draftResource = await api.createDraftFromLatest("workflow", selected.resourceId);
      await selectResource(draftResource);
      await loadWorkflows();
      setNotice(`草稿 ${draftResource.version} 已创建`);
    });
  }

  async function saveDraft(): Promise<void> {
    if (!selected || !draft) return;
    await runAction(async () => {
      const updated = await api.updateDraft(selected, draftToSpec(draft));
      await selectResourceKeepSelection(updated);
      // 纯保存（P2 review 分工）：校验=保存并解锁发布（走 validateDraft）；保存草稿
      // 只持久化，提示"草稿已保存"。
      setNotice("草稿已保存");
    });
  }

  async function validateDraft(): Promise<void> {
    if (!selected || !draft) return;
    await runAction(async () => {
      // C403（E-02）：先做 V2 判别联合草稿校验（诊断逐字段定位），再走资源级校验。
      // P2（review）：S-10 验收流是"校验→发布"（无独立保存步），发布以持久化版本为对象，
      // 故校验需落盘才能解锁发布——但副作用必须显式告知，而非静默"保存+校验"。
      const v2 = await api.validateWorkflow(draft);
      if (!v2.valid) {
        setDiagnostics(v2.diagnostics);
        setValidatedVersion(null);
        setNotice(null);
        return;
      }
      setDiagnostics([]);
      const updated = await api.updateDraft(selected, draftToSpec(draft));
      const result = await api.validateDraft(updated);
      if (!result.valid) throw new Error(result.diagnostics.join("；"));
      await selectResourceKeepSelection(updated);
      setValidatedVersion(updated.version);
      setNotice("已保存并校验通过，可发布");
    });
  }

  async function publish(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      await api.publishVersion(selected);
      const version = selected.version;
      const latest = await api.getResource("workflow", selected.resourceId);
      await selectResource(latest);
      await loadWorkflows();
      setConfirmVisible(false);
      setNotice(`已发布 ${version}`);
    });
  }

  async function selectResourceKeepSelection(resource: ResourceVersion): Promise<void> {
    const result = await api.listVersions("workflow", resource.resourceId, {
      page: 1,
      pageSize: 20
    });
    setSelected(resource);
    setVersions(result.items);
  }

  async function runAction(action: () => Promise<void>): Promise<void> {
    try {
      await action();
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  // ---- 草稿编辑（表单模式 ↔ JSON 模式共享状态） ----

  function updateDraft(next: WorkflowDraftV2): void {
    setDraft(next);
    setSpecText(JSON.stringify(draftToSpec(next), null, 2));
    setValidatedVersion(null);
    setDiagnostics([]);
  }

  function updateNode(next: WorkflowV2Node): void {
    if (!draft || selectedNodeIndex === null) return;
    updateDraft({
      ...draft,
      steps: draft.steps.map((node, index) => (index === selectedNodeIndex ? next : node))
    });
  }

  function addNode(): void {
    if (!draft) return;
    const id = uniqueNodeId(draft);
    updateDraft({
      ...draft,
      steps: [
        ...draft.steps,
        { capability_ref: "", depends_on: [], id, input: {}, type: "capability" }
      ]
    });
    setSelectedNodeIndex(draft.steps.length);
  }

  function removeNode(index: number): void {
    if (!draft) return;
    updateDraft({ ...draft, steps: draft.steps.filter((_, i) => i !== index) });
    setSelectedNodeIndex((current) => {
      if (current === null) return null;
      if (current === index) return null;
      return current > index ? current - 1 : current;
    });
  }

  return (
    <div className="page-stack">
      <PageHeader description="管理工作流定义（WorkflowDefinition）DSL、校验与不可变版本。" title="流程编排" />
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <WorkflowTable onSelect={(item) => void selectWorkflow(item)} workflows={workflows} />
      {selected ? (
        <div className="workflow-layout">
          <WorkflowStudioEditor
            canPublish={validatedVersion === selected.version}
            diagnostics={diagnostics}
            draft={draft}
            nodeListProps={{
              nodes: draft?.steps ?? [],
              onAdd: addNode,
              onRemove: removeNode,
              onSelect: setSelectedNodeIndex,
              selectedIndex: selectedNodeIndex
            }}
            onCreateDraft={() => void createDraft()}
            onPublish={() => setConfirmVisible(true)}
            onSave={() => void saveDraft()}
            onSpecChange={(value) => {
              setSpecText(value);
              setValidatedVersion(null);
              try {
                setDraft(parseDraftText(value));
              } catch {
                // JSON 编辑中间态：语法未完整时保留上一份草稿
              }
            }}
            onValidate={() => void validateDraft()}
            onNodeChange={updateNode}
            resource={selected}
            specText={specText}
          />
          <VersionsTable versions={versions} />
        </div>
      ) : null}
      <PublishWorkflowModal
        onCancel={() => setConfirmVisible(false)}
        onConfirm={() => void publish()}
        resource={selected}
        visible={confirmVisible}
      />
    </div>
  );
}

function WorkflowTable({
  onSelect,
  workflows
}: {
  readonly onSelect: (workflow: ResourceSummary) => void;
  readonly workflows: readonly ResourceSummary[];
}) {
  const columns = [
    {
      dataIndex: "resourceId",
      render: (_value: unknown, record: ResourceSummary) => (
        <Button onClick={() => onSelect(record)} type="tertiary">{record.resourceId}</Button>
      ),
      title: "工作流"
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
      dataSource={[...workflows]}
      empty={<Empty description="暂无工作流" />}
      pagination={false}
      rowKey="resourceId"
    />
  );
}

interface WorkflowStudioEditorProps {
  readonly canPublish: boolean;
  readonly diagnostics: readonly WorkflowV2Diagnostic[];
  readonly draft: WorkflowDraftV2 | null;
  readonly nodeListProps: {
    readonly nodes: readonly WorkflowV2Node[];
    readonly selectedIndex: number | null;
    readonly onSelect: (index: number) => void;
    readonly onAdd: () => void;
    readonly onRemove: (index: number) => void;
  };
  readonly onCreateDraft: () => void;
  readonly onPublish: () => void;
  readonly onSave: () => void;
  readonly onSpecChange: (value: string) => void;
  readonly onValidate: () => void;
  readonly onNodeChange: (node: WorkflowV2Node) => void;
  readonly resource: ResourceVersion;
  readonly specText: string;
}

function WorkflowStudioEditor(props: WorkflowStudioEditorProps) {
  const { canPublish, draft, nodeListProps, resource } = props;
  const selectedNode =
    nodeListProps.selectedIndex === null
      ? null
      : draft?.steps[nodeListProps.selectedIndex] ?? null;
  return (
    <Card
      aria-label="Workflow Editor"
      bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }}
      title="工作流编辑器"
    >
      <Descriptions row>
        <Descriptions.Item itemKey="工作流">{resource.resourceId}</Descriptions.Item>
        <Descriptions.Item itemKey="版本">{resource.version}</Descriptions.Item>
        <Descriptions.Item itemKey="状态"><StatusTag status={resource.status} /></Descriptions.Item>
      </Descriptions>
      <Space wrap>
        <Button aria-label="创建草稿" icon={<IconPlus />} onClick={props.onCreateDraft}>创建草稿</Button>
      </Space>
      <StudioToolbar
        canPublish={canPublish}
        diagnostics={props.diagnostics}
        onPublish={props.onPublish}
        onSave={props.onSave}
        onValidate={props.onValidate}
      />
      <Tabs type="line" defaultActiveKey="form">
        <Tabs.TabPane tab="表单模式" itemKey="form">
          <div className="studio-form-mode">
            <WorkflowNodeList {...nodeListProps} />
            {selectedNode ? (
              <NodeConfigForm node={selectedNode} onChange={props.onNodeChange} />
            ) : (
              <Empty description="选择左侧节点进行配置" />
            )}
          </div>
        </Tabs.TabPane>
        <Tabs.TabPane tab="JSON 高级模式" itemKey="json">
          <JsonEditorTab onChange={props.onSpecChange} specText={props.specText} />
        </Tabs.TabPane>
      </Tabs>
    </Card>
  );
}

function VersionsTable({ versions }: { readonly versions: readonly ResourceVersion[] }) {
  const columns = [
    { dataIndex: "version", title: "版本" },
    {
      render: (_value: unknown, record: ResourceVersion) => <StatusTag status={record.status} />,
      title: "状态"
    },
    { dataIndex: "updatedAt", title: "更新时间" }
  ];
  return (
    <Card aria-label="Workflow Versions" bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }} title="版本">
      <Table
        columns={columns}
        dataSource={[...versions]}
        empty={<Empty description="暂无版本" />}
        pagination={false}
        rowKey="version"
      />
    </Card>
  );
}

function PublishWorkflowModal(props: {
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
  readonly resource: ResourceVersion | null;
  readonly visible: boolean;
}) {
  return (
    <Modal
      footer={
        <Space>
          <Button onClick={props.onCancel}>取消</Button>
          <Button onClick={props.onConfirm} theme="solid" type="primary">确认发布</Button>
        </Space>
      }
      onCancel={props.onCancel}
      title="确认发布工作流"
      visible={props.visible}
    >
      {props.resource ? (
        <Space align="start" vertical>
          <Typography.Text>{`workflow/${props.resource.resourceId}`}</Typography.Text>
          <Typography.Text>{props.resource.version}</Typography.Text>
        </Space>
      ) : null}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// draft ↔ spec 转换（V1 兼容：无 type 的 step 注入 capability）

function parseDraft(spec: JsonRecord): WorkflowDraftV2 {
  return parseDraftText(JSON.stringify(spec));
}

function parseDraftText(value: string): WorkflowDraftV2 {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Workflow DSL 必须是 JSON Object");
  }
  const record = parsed as Record<string, unknown>;
  const steps = Array.isArray(record.steps) ? record.steps : [];
  return {
    description: typeof record.description === "string" ? record.description : undefined,
    display_name: typeof record.display_name === "string" ? record.display_name : undefined,
    name: typeof record.name === "string" ? record.name : "",
    steps: steps.map(normalizeNode)
  };
}

function normalizeNode(step: unknown): WorkflowV2Node {
  const node = (typeof step === "object" && step !== null ? step : {}) as Record<string, unknown>;
  const type = typeof node.type === "string" ? node.type : "capability";
  return { ...node, type } as unknown as WorkflowV2Node;
}

function draftToSpec(draft: WorkflowDraftV2): JsonRecord {
  const spec: Record<string, unknown> = {
    name: draft.name,
    steps: draft.steps
  };
  if (draft.display_name !== undefined) spec.display_name = draft.display_name;
  if (draft.description !== undefined) spec.description = draft.description;
  return spec as JsonRecord;
}

function uniqueNodeId(draft: WorkflowDraftV2): string {
  const existing = new Set(draft.steps.map((node) => node.id));
  let index = draft.steps.length + 1;
  while (existing.has(`node-${index}`)) index += 1;
  return `node-${index}`;
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
