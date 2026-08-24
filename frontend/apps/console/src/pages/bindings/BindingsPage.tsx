import { useEffect, useState } from "react";

import { Button, Input, Select, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconKey, IconSave } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
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

export function BindingsPage({ api }: BindingsPageProps) {
  const [bindings, setBindings] = useState<readonly BindingRecord[]>([]);
  const [credentials, setCredentials] = useState<readonly CredentialMetadata[]>([]);
  const [resources, setResources] = useState<readonly ResourceSummary[]>([]);
  const [credentialRef, setCredentialRef] = useState<string | null>("secret://openai-prod");
  const [resourceType, setResourceType] = useState<ResourceType>("mcp");
  const [subjectId, setSubjectId] = useState("bind-user-001");
  const [error, setError] = useState<string | null>(null);

  async function loadPage(): Promise<void> {
    try {
      const [visibleResources, metadata, existingBindings] = await Promise.all([
        api.listVisibleResources(resourceType),
        api.listCredentials(),
        api.listBindings()
      ]);
      setResources(visibleResources);
      setCredentials(metadata);
      setCredentialRef((current) => metadata[0]?.credentialRef ?? current);
      setBindings(existingBindings);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  useEffect(() => {
    void loadPage();
  }, [resourceType]);

  async function bindResource(resource: ResourceSummary): Promise<void> {
    try {
      await api.saveBinding(bindingInput(resource, credentialRef, subjectId));
      setBindings(await api.listBindings());
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  return (
    <div className="page-stack">
      <PageHeader description="Binding 承载用户级差异，Credential 只展示 SecretRef。" title="Bindings / Policies" />
      <ErrorBanner message={error} />
      <section className="panel">
        <Space wrap align="center">
          <Select
            aria-label="Binding Resource 类型"
            data-testid="binding-resource-type-select"
            onChange={(value) => {
              if (isBindableResourceType(value)) setResourceType(value);
            }}
            optionList={BINDABLE_RESOURCE_TYPES.map((type) => ({ label: type, value: type }))}
            value={resourceType}
          />
          <Input aria-label="Binding User ID" onChange={setSubjectId} value={subjectId} />
        </Space>
      </section>
      <CredentialSelector credentials={credentials} value={credentialRef} onChange={setCredentialRef} />
      <VisibleResources onBind={(resource) => void bindResource(resource)} resources={resources} />
      <BindingTable bindings={bindings} />
    </div>
  );
}

function CredentialSelector({
  credentials,
  onChange,
  value
}: {
  readonly credentials: readonly CredentialMetadata[];
  readonly onChange: (credentialRef: string | null) => void;
  readonly value: string | null;
}) {
  return (
    <section className="panel">
      <Space align="center">
        <IconKey />
        <Typography.Text strong>CredentialRef</Typography.Text>
        <Input
          aria-label="CredentialRef"
          list="credential-refs"
          onChange={(next) => onChange(next.trim() ? next : null)}
          value={value ?? ""}
        />
        <datalist id="credential-refs">
          {credentials.map((credential) => (
            <option key={credential.credentialRef} value={credential.credentialRef}>
              {credential.status}
            </option>
          ))}
        </datalist>
      </Space>
    </section>
  );
}

function VisibleResources({
  onBind,
  resources
}: {
  readonly onBind: (resource: ResourceSummary) => void;
  readonly resources: readonly ResourceSummary[];
}) {
  const columns = [
    { dataIndex: "resourceId", title: "Resource" },
    { dataIndex: "currentVersion", title: "Version" },
    {
      render: (_value: unknown, record: ResourceSummary) => (
        <Button aria-label={`绑定 ${record.resourceId}`} icon={<IconSave />} onClick={() => onBind(record)} type="primary">
          绑定 {record.resourceId}
        </Button>
      ),
      title: "Action"
    }
  ];
  return <Table columns={columns} dataSource={[...resources]} pagination={false} rowKey="resourceId" />;
}

function BindingTable({ bindings }: { readonly bindings: readonly BindingRecord[] }) {
  const columns = [
    { dataIndex: "subjectId", title: "Subject" },
    { dataIndex: "resourceId", title: "Resource" },
    { dataIndex: "policyId", title: "Policy" },
    { dataIndex: "credentialRef", title: "CredentialRef" },
    {
      render: (_value: unknown, record: BindingRecord) => (
        <StatusTag status={record.enabled ? "active" : "deprecated"} />
      ),
      title: "Status"
    }
  ];
  return (
    <section className="panel">
      <Typography.Title heading={4}>Binding 状态</Typography.Title>
      <Table columns={columns} dataSource={[...bindings]} pagination={false} rowKey="bindingId" />
    </section>
  );
}

const BINDABLE_RESOURCE_TYPES: readonly ResourceType[] = ["skill", "mcp", "plugin", "policy"];

function isBindableResourceType(value: unknown): value is ResourceType {
  return typeof value === "string" && BINDABLE_RESOURCE_TYPES.includes(value as ResourceType);
}

function bindingInput(
  resource: ResourceSummary,
  credentialRef: string | null,
  subjectId: string
) {
  return {
    credentialRef,
    policyId: "policy-default",
    resourceId: resource.resourceId,
    resourceType: resource.resourceType,
    subjectId,
    subjectType: "user" as const,
    versionSelector: "latest-published"
  };
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
