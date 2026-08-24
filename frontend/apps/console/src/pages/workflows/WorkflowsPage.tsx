import { useEffect, useState } from "react";

import { Button, Descriptions, Modal, Space, Table, TextArea, Typography } from "@douyinfe/semi-ui";
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
      setNotice(`Workflow Draft ${draft.version} 已创建`);
    });
  }

  async function saveDraft(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      const updated = await api.updateDraft(selected, parseSpec(specText));
      await selectResource(updated);
      setNotice("Workflow Draft 已保存");
    });
  }

  async function validateDraft(): Promise<void> {
    if (!selected) return;
    await runAction(async () => {
      const result = await api.validateDraft(selected);
      if (!result.valid) throw new Error(result.diagnostics.join("；"));
      setValidatedVersion(selected.version);
      setNotice("Workflow 校验通过");
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
      setNotice(`Published ${version}`);
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
      <PageHeader description="管理 WorkflowDefinition DSL、校验与不可变版本。" title="Workflows" />
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
      title: "Workflow"
    },
    { dataIndex: "displayName", title: "Name" },
    { dataIndex: "currentVersion", title: "Current Version" },
    {
      render: (_value: unknown, record: ResourceSummary) => <StatusTag status={record.status} />,
      title: "Status"
    }
  ];
  return <Table columns={columns} dataSource={[...workflows]} pagination={false} rowKey="resourceId" />;
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
    <section aria-label="Workflow Editor" className="panel">
      <Typography.Title heading={4}>Workflow Editor</Typography.Title>
      <Descriptions row>
        <Descriptions.Item itemKey="Workflow">{resource.resourceId}</Descriptions.Item>
        <Descriptions.Item itemKey="Version">{resource.version}</Descriptions.Item>
        <Descriptions.Item itemKey="Status"><StatusTag status={resource.status} /></Descriptions.Item>
      </Descriptions>
      <TextArea
        aria-label="Workflow DSL JSON"
        className="workflow-dsl"
        onChange={props.onSpecChange}
        value={props.specText}
      />
      <Space wrap>
        <Button aria-label="创建 Workflow Draft" icon={<IconPlus />} onClick={props.onCreateDraft}>创建 Workflow Draft</Button>
        <Button aria-label="保存 Workflow" icon={<IconSave />} onClick={props.onSave} type="primary">保存 Workflow</Button>
        <Button aria-label="Validate Workflow" icon={<IconPlay />} onClick={props.onValidate}>Validate Workflow</Button>
        <Button
          aria-label="Publish Workflow"
          disabled={!canPublish}
          icon={<IconPlay />}
          onClick={props.onPublish}
          theme="solid"
          type="warning"
        >
          Publish Workflow
        </Button>
      </Space>
    </section>
  );
}

function VersionsTable({ versions }: { readonly versions: readonly ResourceVersion[] }) {
  const columns = [
    { dataIndex: "version", title: "Version" },
    {
      render: (_value: unknown, record: ResourceVersion) => <StatusTag status={record.status} />,
      title: "Status"
    },
    { dataIndex: "updatedAt", title: "Updated" }
  ];
  return (
    <section aria-label="Workflow Versions" className="panel">
      <Typography.Title heading={4}>Versions</Typography.Title>
      <Table columns={columns} dataSource={[...versions]} pagination={false} rowKey="version" />
    </section>
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
          <Button onClick={props.onConfirm} theme="solid" type="primary">确认发布 Workflow</Button>
        </Space>
      }
      onCancel={props.onCancel}
      title="确认发布 Workflow"
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
