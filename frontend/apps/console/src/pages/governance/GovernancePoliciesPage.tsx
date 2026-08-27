import { useCallback, useEffect, useState } from "react";

import { Button, Table, Typography } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import { SchemaForm, specFromSchema } from "../../components/SchemaForm";
import type { ConsoleApi, JsonRecord, JsonSchemaNode } from "../../types/console";

interface GovernancePoliciesPageProps {
  readonly api: ConsoleApi;
}

interface ListRow {
  readonly key: string;
  readonly name: string;
  readonly resourceId: string;
  readonly version: string;
}

interface DraftState {
  readonly schema: JsonSchemaNode;
  readonly value: JsonRecord;
  readonly errors: Record<string, string>;
}

/** TASK-020 / FEAT-F07：治理-授权规则页。policy 变更影响 Agent 可调用的工具面。 */
export function GovernancePoliciesPage({ api }: GovernancePoliciesPageProps) {
  const [rows, setRows] = useState<readonly ListRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<DraftState | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    const page = await api.listVisibleResources("policy");
    setRows(
      page.map((item) => ({
        key: `${item.resourceId}@${item.currentVersion}`,
        name: item.displayName || item.resourceId,
        resourceId: item.resourceId,
        version: item.currentVersion
      }))
    );
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openCreate = async () => {
    const schema = await api.getResourceSchema("policy");
    setDraft({ schema, value: specFromSchema(schema), errors: {} });
  };

  const submitDraft = async () => {
    if (draft === null) {
      return;
    }
    const required = (draft.schema.required ?? []) as readonly string[];
    const errors: Record<string, string> = {};
    for (const key of required) {
      const raw = draft.value[key];
      if (raw === undefined || (typeof raw === "string" && raw.trim() === "")) {
        const title = (draft.schema.properties?.[key] as { title?: string } | undefined)?.title ?? key;
        errors[key] = `${title}：必填`;
      }
    }
    if (Object.keys(errors).length > 0) {
      setDraft({ ...draft, errors });
      return;
    }
    const resourceId = `pol_${Math.random().toString(36).slice(2, 10)}`;
    await api.createResource({
      resourceType: "policy",
      resourceId,
      version: "1",
      visibility: "private",
      spec: draft.value
    });
    setDraft(null);
    await refresh();
  };

  return (
    <div>
      <PageHeader
        title="授权规则"
        description="策略约束 Agent 可调用的工具与能力：白名单非空时仅放行所列，黑名单始终优先拒绝。"
      />
      <div style={{ margin: "12px 0" }}>
        <Button theme="solid" onClick={() => void openCreate()}>
          新建规则
        </Button>
      </div>
      <Table
        loading={loading}
        dataSource={rows.map((row) => ({ ...row }))}
        rowKey="key"
        pagination={false}
        columns={[
          { title: "规则名", dataIndex: "name" },
          { title: "ID", dataIndex: "resourceId" },
          { title: "版本", dataIndex: "version" }
        ]}
        empty="暂无数据"
      />

      {draft !== null ? (
        <div
          style={{ marginTop: 12, padding: 16, border: "1px solid var(--semi-color-border)" }}
          aria-label="新建授权规则"
        >
          <SchemaForm
            schema={draft.schema}
            value={draft.value}
            onChange={(next) => setDraft({ ...draft, value: next })}
          />
          {Object.entries(draft.errors).map(([field, message]) => (
            <Typography.Text type="danger" key={field}>
              {message}
            </Typography.Text>
          ))}
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <Button theme="solid" onClick={() => void submitDraft()}>
              提交
            </Button>
            <Button onClick={() => setDraft(null)}>取消</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
