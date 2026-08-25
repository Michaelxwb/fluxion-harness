import { useEffect, useState } from "react";

import { Button, Card, Descriptions, Empty, Modal, Space, Table, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconPlay, IconPlus, IconSave } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import type {
  ConsoleApi,
  JsonRecord,
  ResourceSummary,
  ResourceVersion
} from "../../types/console";

interface WorkflowsPageProps {
  readonly api: ConsoleApi;
}

export function WorkflowsPage({ api }: WorkflowsPageProps) {
  const [workflows, setWorkflows] = useState<readonly ResourceSummary[]>([]);
  const [selected, setSelected] = useState<ResourceVersion | null>(null);
  const [versions, setVersions] = useState<readonly ResourceVersion[]>([]);
  const [specText, setSpecText] = useState("{}");
  const [validatedVersion, setValidatedVersion] = useState<string | null>(null);
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
    setSelected(resource);
    setSpecText(JSON.stringify(resource.spec, null, 2));
    setVersions(result.items);
    setValidatedVersion(null);
  }

  async function createDraft(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      const draft = await api.createDraftFromLatest("workflow", selected.resourceId);
      await selectResource(draft);
      await loadWorkflows();
      setNotice(`草稿 ${draft.version} 已创建`);
    });
  }

  async function saveDraft(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      const updated = await api.updateDraft(selected, parseSpec(specText));
      await selectResource(updated);
      setNotice("草稿已保存");
    });
  }

  async function validateDraft(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      const result = await api.validateDraft(selected);
      if (!result.valid) throw new Error(result.diagnostics.join("；"));
      setValidatedVersion(selected.version);
      setNotice("校验通过");
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

  async function runAction(action: () => Promise<void>): Promise<void> {
    try {
      await action();
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  return (
    <div className="page-stack">
      <PageHeader description="管理工作流定义（WorkflowDefinition）DSL、校验与不可变版本。" title="流程编排" />
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <WorkflowTable onSelect={(item) => void selectWorkflow(item)} workflows={workflows} />
      {selected ? (
        <div className="workflow-layout">
          <WorkflowEditor
            canPublish={validatedVersion === selected.version}
            onCreateDraft={() => void createDraft()}
            onPublish={() => setConfirmVisible(true)}
            onSave={() => void saveDraft()}
            onSpecChange={(value) => {
              setSpecText(value);
              setValidatedVersion(null);
            }}
            onValidate={() => void validateDraft()}
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

function WorkflowEditor(props: {
  readonly canPublish: boolean;
  readonly onCreateDraft: () => void;
  readonly onPublish: () => void;
  readonly onSave: () => void;
  readonly onSpecChange: (value: string) => void;
  readonly onValidate: () => void;
  readonly resource: ResourceVersion;
  readonly specText: string;
}) {
  const { canPublish, resource } = props;
  return (
    <Card aria-label="Workflow Editor" bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }} title="工作流编辑器">
      <Descriptions row>
        <Descriptions.Item itemKey="工作流">{resource.resourceId}</Descriptions.Item>
        <Descriptions.Item itemKey="版本">{resource.version}</Descriptions.Item>
        <Descriptions.Item itemKey="状态"><StatusTag status={resource.status} /></Descriptions.Item>
      </Descriptions>
      <TextArea
        aria-label="工作流 DSL JSON"
        className="workflow-dsl"
        onChange={props.onSpecChange}
        value={props.specText}
      />
      <Space wrap>
        <Button aria-label="创建草稿" icon={<IconPlus />} onClick={props.onCreateDraft}>创建草稿</Button>
        <Button aria-label="保存草稿" icon={<IconSave />} onClick={props.onSave} type="primary">保存草稿</Button>
        <Button aria-label="校验" icon={<IconPlay />} onClick={props.onValidate}>校验</Button>
        <Button
          aria-label="发布"
          disabled={!canPublish}
          icon={<IconPlay />}
          onClick={props.onPublish}
          theme="solid"
          type="warning"
        >
          发布
        </Button>
      </Space>
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

function parseSpec(value: string): JsonRecord {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
    return parsed as JsonRecord;
  }
  throw new Error("Workflow DSL 必须是 JSON Object");
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
