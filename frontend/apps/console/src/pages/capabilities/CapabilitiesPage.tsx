import { useCallback, useEffect, useState } from "react";

import { Button, Table, Tabs, Typography } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import { SchemaForm, specFromSchema } from "../../components/SchemaForm";
import type {
  ConsoleApi,
  JsonRecord,
  JsonSchemaNode,
  ResourceSummary,
  ResourceType
} from "../../types/console";

interface CapabilitiesPageProps {
  readonly api: ConsoleApi;
  /** 测试/嵌入场景可直接指定初始 kind；默认 skill。 */
  readonly initialKind?: CapabilityKind;
}

type CapabilityKind = "skill" | "tool" | "mcp";

const KIND_TABS: readonly { readonly key: CapabilityKind; readonly text: string }[] = [
  { key: "skill", text: "技能" },
  { key: "tool", text: "工具" },
  { key: "mcp", text: "MCP" }
];

const KIND_LABELS: Record<CapabilityKind, string> = {
  skill: "技能",
  tool: "工具",
  mcp: "MCP"
};

interface ListRow {
  readonly key: string;
  readonly name: string;
  readonly resourceId: string;
  readonly version: string;
  readonly status: string;
}

interface DraftState {
  readonly schema: JsonSchemaNode;
  readonly value: JsonRecord;
  readonly errors: Record<string, string>;
}

/** TASK-014 / FEAT-F04：Capabilities 管理页——skill/tool/mcp 三类 Tab + SchemaForm 内联新建。 */
export function CapabilitiesPage({ api, initialKind = "skill" }: CapabilitiesPageProps) {
  const [kind, setKind] = useState<CapabilityKind>(initialKind);
  const [rows, setRows] = useState<readonly ListRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    const page = await api.listVisibleResources(kind as ResourceType);
    const list = page.map((item: ResourceSummary) => ({
      key: `${item.resourceId}@${item.currentVersion}`,
      name: item.displayName || item.resourceId,
      resourceId: item.resourceId,
      version: item.currentVersion,
      status: item.status
    }));
    setRows(list);
    setLoading(false);
  }, [api, kind]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openCreate = async () => {
    const schema = await api.getResourceSchema(kind as ResourceType);
    setDraft({ schema, value: specFromSchema(schema), errors: {} });
  };

  const submitDraft = async () => {
    if (draft === null) {
      return;
    }
    // FE-E-03：必填缺失 → 字段定位 + 不提交。
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
    setSubmitting(true);
    try {
      const resourceId = `cap_${Math.random().toString(36).slice(2, 10)}`;
      await api.createResource({
        resourceType: kind as ResourceType,
        resourceId,
        version: "1",
        visibility: "private",
        spec: draft.value
      });
      setDraft(null);
      await refresh();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <PageHeader title="能力" description="技能 / 工具 / MCP 三类能力资源的统一管理入口" />
      {/* 非受控：Semi Tabs 受控回写在 jsdom 下不触发 onChange（同 Select animationend 陷阱家族） */}
      <Tabs
        defaultActiveKey="skill"
        onChange={(key) => {
          // Semi Tabs 在 jsdom/动画态可能回传 undefined——guard 防御。
          if (typeof key === "string" && key) setKind(key as CapabilityKind);
        }}
      >
        {KIND_TABS.map((tab) => (
          <Tabs.TabPane key={tab.key} tab={tab.text} />
        ))}
      </Tabs>
      <div style={{ margin: "12px 0" }}>
        <Button theme="solid" onClick={() => void openCreate()}>
          新建
        </Button>
      </div>
      <Table
        loading={loading}
        dataSource={rows.map((row) => ({ ...row }))}
        rowKey="key"
        pagination={false}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "ID", dataIndex: "resourceId" },
          { title: "版本", dataIndex: "version" },
          { title: "状态", dataIndex: "status" }
        ]}
        empty="暂无数据"
      />

      {draft !== null ? (
        <div
          style={{ marginTop: 12, padding: 16, border: "1px solid var(--semi-color-border)" }}
          aria-label={`新建${KIND_LABELS[kind]}`}
          data-debug="draft-panel"
        >
            <SchemaForm
              schema={draft.schema}
              value={draft.value}
              onChange={(next) => setDraft({ ...draft, value: next })}
              disabled={submitting}
            />
            {Object.entries(draft.errors).map(([field, message]) => (
              <Typography.Text type="danger" key={field}>
                {message}
              </Typography.Text>
            ))}
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <Button theme="solid" onClick={() => void submitDraft()} loading={submitting}>
              提交
            </Button>
            <Button onClick={() => setDraft(null)}>取消</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
