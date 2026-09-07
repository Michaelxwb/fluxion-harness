import { useEffect, useMemo, useState } from "react";

import { Button, Select, Table, Tag, Toast, Typography } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import {
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, ProjectionModel, ResourceStatus } from "../../types/console";
import { ConnectModelProviderModal } from "./ConnectModelProviderModal";
import { ModelDetailSideSheet } from "./ModelDetailSideSheet";
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
  readonly credentialRef: string;
  readonly status: ResourceStatus;
  readonly models: readonly { readonly id: string; readonly name: string; readonly version: string }[];
}

const PAGE_SIZE = 20;

/** TASK-016（返工 / FEAT-F09）+ TASK-010：模型页标准 Shell 化。
 *
 * Provider 承载连接与凭据（ProviderDefinition），Model 承载模型身份
 * （ModelDefinition，ADR-A008 三层链）——按 provider_ref 分组展示。
 * TASK-010 新增：`[+ 连接模型服务]` 主操作（ConnectModelProviderModal）、
 * 搜索/状态过滤/右下分页、只读详情 SideSheet、刷新模型行操作。
 * 详情/模型仍并行批量拉取（O(N) 请求 TASK-025 Projection API 统一消除）。
 */
export function ModelsPage({ api }: ModelsPageProps) {
  const [rows, setRows] = useState<readonly ProviderRow[] | null>(null);
  const [unmatched, setUnmatched] = useState<
    readonly { readonly id: string; readonly name: string; readonly version: string }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [connectVisible, setConnectVisible] = useState(false);
  const [refreshTarget, setRefreshTarget] = useState<ProviderRow | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [editor, setEditor] = useState<{
    readonly kind: "model_provider" | "model_definition";
    readonly resourceId: string;
  } | null>(null);
  const [credentialOptions, setCredentialOptions] = useState<
    readonly { readonly label: string; readonly value: string }[]
  >([]);

  /** 连通探测：一次性 test-connection，结果 Toast 呈现（不写回资源）。 */
  async function probeProvider(row: ProviderRow): Promise<void> {
    try {
      const result = await api.testModelProviderConnection(row.resourceId);
      if (result.reachable) {
        Toast.success(`连通正常，发现 ${result.discoveredModels.length} 个模型`);
      } else {
        Toast.error(`连通失败：${result.error ?? "未知错误"}`);
      }
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "探测失败");
    }
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        // TASK-025：聚合投影单请求（服务端单查询消除 N+1，网络请求数为常数）
        const projection = await api.getModelLabProjection();
        if (!active) return;
        const grouped = new Map<string, ProjectionModel[]>();
        for (const model of projection.models) {
          if (!model.providerId) continue;
          const bucket = grouped.get(model.providerId);
          if (bucket) bucket.push(model);
          else grouped.set(model.providerId, [model]);
        }
        setRows(
          projection.providers.map((provider) => ({
            key: provider.resourceId,
            displayName: provider.displayName,
            resourceId: provider.resourceId,
            version: provider.version,
            baseUrl: provider.baseUrl,
            credentialRef: provider.credentialRef,
            status: provider.status as unknown as ResourceStatus,
            models: (grouped.get(provider.resourceId) ?? []).map(({ resourceId, name, version }) => ({
              id: resourceId,
              name,
              version
            }))
          }))
        );
        const providerIds = new Set(projection.providers.map((provider) => provider.resourceId));
        setUnmatched(
          projection.models
            .filter((model) => !providerIds.has(model.providerId))
            .map(({ resourceId, name, version }) => ({ id: resourceId, name, version }))
        );
        // 凭据选择器选项（Provider 编辑器 / 连接 Modal 共用）
        setCredentialOptions(
          projection.credentials.map((credential) => ({
            label: credential.label,
            value: credential.value
          }))
        );
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const keyword = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (statusFilter !== undefined && row.status !== statusFilter) return false;
      if (!keyword) return true;
      return (
        row.displayName.toLowerCase().includes(keyword) ||
        row.resourceId.toLowerCase().includes(keyword) ||
        row.baseUrl.toLowerCase().includes(keyword)
      );
    });
  }, [rows, search, statusFilter]);

  const total = filtered.length;
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function reload(): void {
    setReloadKey((key) => key + 1);
  }

  return (
    <div className="page-stack">
      <PageHeader
        description="Provider 承载连接与凭据，Model 承载模型身份（ADR-A008 三层链）；按 Provider 分组展示。"
        title="模型"
      />
      <div aria-label="模型列表">
      <StandardListCard
        empty={rows !== null && rows.length === 0 && unmatched.length === 0}
        emptyDescription="暂无模型服务"
        error={error}
        footer={
          rows !== null && rows.length > 0 ? (
            <StandardListFooter
              onPageChange={setPage}
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
            />
          ) : undefined
        }
        loading={rows === null && !error}
        onRetry={reload}
        toolbar={
          <StandardListToolbar
            filters={
              <>
                <span className="sr-only" id="model-status-filter-label">
                  模型状态过滤
                </span>
                <Select
                  aria-labelledby="model-status-filter-label"
                  onChange={(value) => {
                    setStatusFilter(value === "" ? undefined : String(value));
                    setPage(1);
                  }}
                  optionList={[
                    { label: "全部状态", value: "" },
                    { label: "草稿", value: "draft" },
                    { label: "已发布", value: "published" }
                  ]}
                  placeholder="状态"
                  showClear
                  style={{ width: 140 }}
                  value={statusFilter ?? ""}
                />
              </>
            }
            primary={
              <Button
                aria-label="连接模型服务"
                onClick={() => setConnectVisible(true)}
                theme="solid"
                type="primary"
              >
                + 连接模型服务
              </Button>
            }
            search={
              <StandardListSearch
                onChange={setSearch}
                placeholder="搜索名称 / 资源 ID / Endpoint"
                value={search}
              />
            }
          />
        }
      >
        <Table
          columns={[
            {
              title: "Provider",
              dataIndex: "displayName",
              render: (value: string, record: ProviderRow) => (
                <Button
                  aria-label={`查看模型服务 ${value}`}
                  onClick={() => setDetailId(record.resourceId)}
                  theme="borderless"
                  type="tertiary"
                >
                  {value}
                </Button>
              )
            },
            { title: "Base URL", dataIndex: "baseUrl", render: (value: string) => (
              <Typography.Text ellipsis={{ showTooltip: true }} style={{ maxWidth: 280 }}>
                {value}
              </Typography.Text>
            ) },
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
                <RowActions
                  immediate={[
                    {
                      key: "edit",
                      content: "编辑",
                      onClick: () => setEditor({
                        kind: "model_provider",
                        resourceId: record.resourceId
                      })
                    }
                  ]}
                    more={[
                      {
                        key: "detail",
                        content: "查看详情",
                        onClick: () => setDetailId(record.resourceId)
                      },
                      {
                        key: "probe",
                        content: "探测连通",
                        onClick: () => void probeProvider(record)
                      },
                      {
                        key: "refresh-models",
                        content: "刷新模型",
                        onClick: () => setRefreshTarget(record)
                      }
                    ]}
                />
              )
            }
          ]}
          dataSource={paged.map((row) => row)}
          pagination={false}
          rowKey="key"
        />
        {unmatched.length > 0 ? (
          <div aria-label="未挂载模型" style={{ marginTop: 12 }}>
            <Typography.Text type="tertiary">未挂载 Provider 的模型：</Typography.Text>
            {unmatched.map((model) => (
              <Tag key={model.id} style={{ margin: 2 }}>
                {`${model.name}（${displayVersion(model.version)}）`}
              </Tag>
            ))}
          </div>
        ) : null}
      </StandardListCard>
      </div>
      <ConnectModelProviderModal
        api={api}
        onClose={() => setConnectVisible(false)}
        onConnected={() => {
          setConnectVisible(false);
          reload();
        }}
        visible={connectVisible}
      />
      {refreshTarget !== null ? (
        <ConnectModelProviderModal
          api={api}
          onClose={() => setRefreshTarget(null)}
          onConnected={() => {
            setRefreshTarget(null);
            reload();
          }}
          provider={{
            baseUrl: refreshTarget.baseUrl === "-" ? "" : refreshTarget.baseUrl,
            credentialRef: refreshTarget.credentialRef,
            displayName: refreshTarget.displayName,
            resourceId: refreshTarget.resourceId
          }}
          visible={refreshTarget !== null}
        />
      ) : null}
      <ModelDetailSideSheet api={api} onClose={() => setDetailId(null)} resourceId={detailId} />
      {editor ? (
        <ModelResourceEditor
          api={api}
          credentialOptions={credentialOptions}
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
