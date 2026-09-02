import { useEffect, useState } from "react";

import { Button, Card, Empty, Spin, Table, Tag, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord, ResourceStatus } from "../../types/console";
import { ModelResourceEditor } from "./ModelResourceEditor";

interface ModelsPageProps {
  readonly api: ConsoleApi;
}

interface ProviderRow {
  readonly key: string;
  readonly displayName: string;
  readonly resourceId: string;
  readonly version: string;
  readonly baseUrl: string;
  readonly status: ResourceStatus;
  readonly models: readonly { readonly id: string; readonly name: string; readonly version: string }[];
}

/** TASK-016（返工 / FEAT-F09）：模型页按 Provider → Model 产品语义呈现。
 *
 * Provider 承载连接与凭据（ProviderDefinition），Model 承载模型身份
 * （ModelDefinition，ADR-A008 三层链）——按 provider_ref 分组展示。
 * 列表 + 详情均经 Promise.all 并行批量拉取，不做逐条串行 N+1。
 */
export function ModelsPage({ api }: ModelsPageProps) {
  const [rows, setRows] = useState<readonly ProviderRow[] | null>(null);
  const [unmatched, setUnmatched] = useState<
    readonly { readonly id: string; readonly name: string; readonly version: string }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [editor, setEditor] = useState<{
    readonly kind: "model_provider" | "model_definition";
    readonly resourceId: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        // 两类资源并行列表（Provider 连接 + ModelDefinition 模型身份）
        const [providersPage, modelsPage] = await Promise.all([
          api.listResources("model_provider"),
          api.listResources("model_definition")
        ]);
        // 详情并行批量（base_url / 模型名在 spec），非串行 N+1
        const [providerDetails, modelDetails] = await Promise.all([
          Promise.all(
            providersPage.items.map((provider) => api.getResource("model_provider", provider.resourceId))
          ),
          Promise.all(
            modelsPage.items.map((model) => api.getResource("model_definition", model.resourceId))
          )
        ]);
        if (!active) return;
        const models = modelsPage.items.map((summary, index) => {
          const spec = modelDetails[index]?.spec as JsonRecord | undefined;
          const providerRef = spec?.provider_ref as JsonRecord | undefined;
          return {
            id: summary.resourceId,
            name: String(spec?.name ?? summary.displayName),
            version: summary.currentVersion,
            providerId: String(providerRef?.id ?? "")
          };
        });
        const grouped = new Map<string, typeof models>();
        for (const model of models) {
          if (!model.providerId) continue;
          const bucket = grouped.get(model.providerId);
          if (bucket) bucket.push(model);
          else grouped.set(model.providerId, [model]);
        }
        setRows(
          providersPage.items.map((provider, index) => ({
            key: provider.resourceId,
            displayName: provider.displayName,
            resourceId: provider.resourceId,
            version: provider.currentVersion,
            baseUrl: String(
              (providerDetails[index]?.spec as JsonRecord | undefined)?.base_url ?? "-"
            ),
            status: provider.status,
            models: (grouped.get(provider.resourceId) ?? []).map(({ id, name, version }) => ({
              id,
              name,
              version
            }))
          }))
        );
        const providerIds = new Set(providersPage.items.map((provider) => provider.resourceId));
        setUnmatched(
          models
            .filter((model) => !providerIds.has(model.providerId))
            .map(({ id, name, version }) => ({ id, name, version }))
        );
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <div className="page-stack">
      <PageHeader
        description="Provider 承载连接与凭据，Model 承载模型身份（ADR-A008 三层链）；按 Provider 分组展示。"
        title="模型"
      />
      <ErrorBanner message={error} onRetry={() => setReloadKey((key) => key + 1)} />
      <Card aria-label="模型列表">
        {rows === null ? (
          <div aria-label="模型加载中">
            <Spin />
          </div>
        ) : rows.length === 0 ? (
          <Empty description="暂无模型服务" />
        ) : (
          <Table
            columns={[
              { title: "Provider", dataIndex: "displayName" },
              { title: "资源 ID", dataIndex: "resourceId" },
              { title: "Base URL", dataIndex: "baseUrl" },
              {
                title: "模型（ModelDefinition）",
                render: (_value: unknown, record: ProviderRow) =>
                  record.models.length === 0 ? (
                    <Typography.Text type="tertiary">-</Typography.Text>
                  ) : (
                    <span>
                      {record.models.map((model) => (
                        <Button
                          aria-label={`编辑模型 ${model.id}`}
                          key={model.id}
                          onClick={() => setEditor({
                            kind: "model_definition",
                            resourceId: model.id
                          })}
                          theme="borderless"
                        >
                          <Tag style={{ margin: 2 }}>{`${model.name}（${displayVersion(model.version)}）`}</Tag>
                        </Button>
                      ))}
                    </span>
                  )
              },
              {
                title: "状态",
                dataIndex: "status",
                render: (value: string) => <StatusTag status={value as ResourceStatus} />
              },
              {
                title: "操作",
                render: (_value: unknown, record: ProviderRow) => (
                  <Button
                    aria-label={`编辑模型服务 ${record.resourceId}`}
                    onClick={() => setEditor({
                      kind: "model_provider",
                      resourceId: record.resourceId
                    })}
                    theme="borderless"
                  >
                    编辑
                  </Button>
                )
              }
            ]}
            dataSource={rows.map((row) => row)}
            pagination={false}
          />
        )}
        {rows !== null && rows.length > 0 ? (
          <Typography.Text type="tertiary">共 {rows.length} 个模型服务</Typography.Text>
        ) : null}
        {unmatched.length > 0 ? (
          <div aria-label="未挂载模型">
            <Typography.Text type="tertiary">未挂载 Provider 的模型：</Typography.Text>
            {unmatched.map((model) => (
              <Tag key={model.id} style={{ margin: 2 }}>
                {`${model.name}（${displayVersion(model.version)}）`}
              </Tag>
            ))}
          </div>
        ) : null}
      </Card>
      {editor ? (
        <ModelResourceEditor
          api={api}
          kind={editor.kind}
          onClose={() => setEditor(null)}
          onSaved={() => setReloadKey((key) => key + 1)}
          providerOptions={(rows ?? []).map((row) => ({
            label: row.displayName || row.resourceId,
            value: `${row.resourceId}@${row.version}`
          }))}
          resourceId={editor.resourceId}
        />
      ) : null}
    </div>
  );
}

function displayVersion(version: string): string {
  return version.startsWith("v") ? version : `v${version}`;
}
