import { useEffect, useState } from "react";

import { Button, Descriptions, Input, Modal, Select, Space, Table, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconPlay, IconPlus, IconSave } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord, PageData, ResourceSummary, ResourceType, ResourceVersion } from "../../types/console";
import { type ConfirmAction, ResourceActionModal } from "./ResourceActionModal";
import { ResourceVersionsPanel, RetentionPanel, VERSION_PAGE_SIZE } from "./ResourceVersionsPanel";

interface ResourcesPageProps {
  readonly api: ConsoleApi;
}

export function ResourcesPage({ api }: ResourcesPageProps) {
  const [resources, setResources] = useState<readonly ResourceSummary[]>([]);
  const [selected, setSelected] = useState<ResourceVersion | null>(null);
  const [specText, setSpecText] = useState("{}");
  const [versions, setVersions] = useState<PageData<ResourceVersion> | null>(null);
  const [versionPage, setVersionPage] = useState(1);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [resourceType, setResourceType] = useState<ResourceType>("runtime_profile");
  const [createOpen, setCreateOpen] = useState(false);
  const [createId, setCreateId] = useState("");
  const [createVersion, setCreateVersion] = useState("v1");
  const [createSpec, setCreateSpec] = useState("{}");

  async function loadResources(type = resourceType): Promise<void> {
    const page = await api.listResources(type);
    setResources(page.items);
  }

  async function selectResource(summary: ResourceSummary): Promise<void> {
    try {
      const resource = await api.getResource(summary.resourceType, summary.resourceId);
      setSelectedResource(resource);
      await loadVersions(resource, 1);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  useEffect(() => {
    setSelected(null);
    void loadResources(resourceType);
  }, [resourceType]);

  async function createResource(): Promise<void> {
    await runAction(async () => {
      const created = await api.createResource({
        resourceId: createId.trim(),
        resourceType,
        spec: parseSpec(createSpec),
        version: createVersion.trim(),
        visibility: "private"
      });
      setCreateOpen(false);
      setCreateId("");
      setCreateVersion("v1");
      setCreateSpec("{}");
      setSelectedResource(created);
      await loadResources();
      await loadVersions(created, 1);
      setNotice(`${created.resourceId}@${created.version} 已创建`);
    });
  }

  async function createDraft(): Promise<void> {
    if (!selected) {
      return;
    }
    await runAction(async () => {
      const draft = await api.createDraftFromLatest(selected.resourceType, selected.resourceId);
      setSelectedResource(draft);
      await loadResources();
      await loadVersions(draft, 1);
      setNotice(`Draft ${draft.version} 已创建`);
    });
  }

  async function saveDraft(): Promise<void> {
    if (!selected) {
      return;
    }
    await runAction(async () => {
      const updated = await api.updateDraft(selected, parseSpec(specText));
      setSelectedResource(updated);
      await loadResources();
      await loadVersions(updated, versionPage);
      setNotice("Draft 已保存");
    });
  }

  async function validateDraft(): Promise<void> {
    if (!selected) {
      return;
    }
    await runAction(async () => {
      const result = await api.validateDraft(selected);
      setDiagnostic(result.diagnostics.join("；"));
      setNotice(result.valid ? "Validate 完成" : "校验失败");
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

  async function loadVersions(resource: ResourceVersion, page: number): Promise<void> {
    setVersionPage(page);
    setVersions(
      await api.listVersions(resource.resourceType, resource.resourceId, {
        page,
        pageSize: VERSION_PAGE_SIZE
      })
    );
  }

  function setSelectedResource(resource: ResourceVersion): void {
    setSelected(resource);
    setSpecText(JSON.stringify(resource.spec, null, 2));
    setDiagnostic(null);
  }

  return (
    <div className="page-stack">
      <PageHeader description="Console 只管理 RuntimeProfile，不创建 Runtime Pod。" title="Runtime Profiles" />
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <Space wrap>
        <Select
          aria-label="Resource 类型"
          data-testid="resource-type-select"
          onChange={(value) => {
            if (isResourceType(value)) setResourceType(value);
          }}
          optionList={RESOURCE_TYPES.map((type) => ({ label: type, value: type }))}
          value={resourceType}
        />
        <Button icon={<IconPlus />} onClick={() => setCreateOpen(true)} type="primary">新建 Resource</Button>
      </Space>
      <ResourceTable onSelect={(summary) => void selectResource(summary)} resources={resources} />
      {selected ? (
        <ResourceDetail
          diagnostic={diagnostic}
          onCreateDraft={() => void createDraft()}
          onPublish={(resource) => setConfirmAction({ resource, type: "publish" })}
          onRollback={(resource, targetVersion) => setConfirmAction({ resource, targetVersion, type: "rollback" })}
          onSave={() => void saveDraft()}
          onSpecChange={setSpecText}
          onValidate={() => void validateDraft()}
          resource={selected}
          specText={specText}
          versionPage={versionPage}
          versions={versions}
          onVersionPageChange={(page) => void loadVersions(selected, page)}
        />
      ) : null}
      <RetentionPanel />
      <ResourceActionModal action={confirmAction} api={api} onClose={() => setConfirmAction(null)} onDone={handleConfirmDone} />
      {createOpen ? (
        <Modal
          cancelText="取消"
          okButtonProps={{ disabled: !createId.trim() || !createVersion.trim() }}
          okText="创建 Draft"
          onCancel={() => setCreateOpen(false)}
          onOk={() => void createResource()}
          title={`新建 ${resourceType}`}
          visible
        >
          <Space vertical align="start" style={{ width: "100%" }}>
            <Input aria-label="Resource ID" onChange={setCreateId} placeholder="resource-id" value={createId} />
            <Input aria-label="Version" onChange={setCreateVersion} value={createVersion} />
            <TextArea aria-label="新 Resource Spec JSON" autosize={{ minRows: 8, maxRows: 16 }} onChange={setCreateSpec} value={createSpec} />
          </Space>
        </Modal>
      ) : null}
    </div>
  );

  async function handleConfirmDone(message: string, resource: ResourceVersion): Promise<void> {
    setNotice(message);
    const latest = await api.getResource(resource.resourceType, resource.resourceId);
    setSelectedResource(latest);
    await loadResources();
    await loadVersions(latest, 1);
  }
}

const RESOURCE_TYPES: readonly ResourceType[] = [
  "runtime_profile",
  "skill",
  "mcp",
  "plugin",
  "policy"
];

function isResourceType(value: unknown): value is ResourceType {
  return typeof value === "string" && RESOURCE_TYPES.includes(value as ResourceType);
}

function ResourceTable({
  onSelect,
  resources
}: {
  readonly onSelect: (resource: ResourceSummary) => void;
  readonly resources: readonly ResourceSummary[];
}) {
  const columns = [
    {
      dataIndex: "resourceId",
      render: (_value: unknown, record: ResourceSummary) => (
        <Button onClick={() => onSelect(record)} type="tertiary">
          {record.resourceId}
        </Button>
      ),
      title: "Resource"
    },
    { dataIndex: "displayName", title: "Name" },
    { dataIndex: "currentVersion", title: "Current Version" },
    {
      render: (_value: unknown, record: ResourceSummary) => <StatusTag status={record.status} />,
      title: "Status"
    }
  ];
  return <Table columns={columns} dataSource={[...resources]} pagination={false} rowKey="resourceId" />;
}

function ResourceDetail(props: {
  readonly diagnostic: string | null;
  readonly resource: ResourceVersion;
  readonly specText: string;
  readonly versionPage: number;
  readonly versions: PageData<ResourceVersion> | null;
  readonly onCreateDraft: () => void;
  readonly onPublish: (resource: ResourceVersion) => void;
  readonly onRollback: (resource: ResourceVersion, version: string) => void;
  readonly onSave: () => void;
  readonly onSpecChange: (value: string) => void;
  readonly onValidate: () => void;
  readonly onVersionPageChange: (page: number) => void;
}) {
  return (
    <div className="detail-grid">
      <DraftEditor {...props} />
      <ResourceVersionsPanel {...props} />
    </div>
  );
}

function DraftEditor({
  diagnostic,
  onCreateDraft,
  onPublish,
  onSave,
  onSpecChange,
  onValidate,
  resource,
  specText
}: {
  readonly diagnostic: string | null;
  readonly resource: ResourceVersion;
  readonly specText: string;
  readonly onCreateDraft: () => void;
  readonly onPublish: (resource: ResourceVersion) => void;
  readonly onSave: () => void;
  readonly onSpecChange: (value: string) => void;
  readonly onValidate: () => void;
}) {
  return (
    <section aria-label="Draft Editor" className="panel">
      <Typography.Title heading={4}>Draft Editor</Typography.Title>
      <Descriptions row>
        <Descriptions.Item itemKey="Resource">{resource.resourceId}</Descriptions.Item>
        <Descriptions.Item itemKey="Version">{resource.version}</Descriptions.Item>
        <Descriptions.Item itemKey="Status"><StatusTag status={resource.status} /></Descriptions.Item>
      </Descriptions>
      <Input
        aria-label="Spec JSON"
        className="json-input"
        onChange={onSpecChange}
        value={specText}
      />
      {diagnostic ? <Typography.Text type="success">{diagnostic}</Typography.Text> : null}
      <Space wrap>
        <Button aria-label="创建 Draft" icon={<IconPlus />} onClick={onCreateDraft}>创建 Draft</Button>
        <Button aria-label="保存 Draft" icon={<IconSave />} onClick={onSave} type="primary">保存 Draft</Button>
        <Button aria-label="Validate" icon={<IconPlay />} onClick={onValidate}>Validate</Button>
        <Button aria-label="Publish" icon={<IconPlay />} onClick={() => onPublish(resource)} theme="solid" type="warning">
          Publish
        </Button>
      </Space>
    </section>
  );
}

function parseSpec(value: string): JsonRecord {
  const parsed: unknown = JSON.parse(value);
  if (isJsonRecord(parsed)) {
    return parsed;
  }
  throw new Error("Spec 必须是 JSON Object");
}

function isJsonRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
