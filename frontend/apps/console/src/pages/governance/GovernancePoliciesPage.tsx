import { useCallback, useEffect, useRef, useState } from "react";

import { IconPlus } from "@douyinfe/semi-icons";
import { Button, Descriptions, Modal, Select, SideSheet, Space, Table, Tag, Toast, Typography } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "../../components/PageHeader";
import { EmptyState } from "../../components/EmptyState";
import { RelativeTime } from "../../components/RelativeTime";
import { ResourceId } from "../../components/ResourceId";
import {
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, ResourceStatus } from "../../types/console";
import { CreatePolicyModal } from "./CreatePolicyModal";

interface GovernancePoliciesPageProps {
  readonly api: ConsoleApi;
}

interface ListRow {
  readonly key: string;
  readonly name: string;
  readonly resourceId: string;
  readonly version: string;
  readonly status: string;
  readonly updatedAt: string;
  readonly allowCount: number;
  readonly denyCount: number;
}

const PAGE_SIZE = 10;

/** TASK-022（§8.11 用户决策 B：完整开放）：授权规则页——CreatePolicyModal +
 * 独立 Policy Editor + 只读 SideSheet + StandardListShell（搜索/过滤/分页/行操作）。
 * policy 变更影响 Agent 可调用的工具面。 */
export function GovernancePoliciesPage({ api }: GovernancePoliciesPageProps) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<readonly ListRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [detail, setDetail] = useState<ListRow | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const refresh = useCallback(async () => {
    requestSeq.current += 1;
    const requestId = requestSeq.current;
    setError(null);
    setRows(null);
    try {
      // FEAT-03：分页/搜索/状态全部服务端化（GET /api/v1/resources，同一集合
      // 分页与 count），受控状态 + 请求序号 guard 防乱序覆盖。
      const result = await api.listResources("policy", {
        page,
        pageSize: PAGE_SIZE,
        keyword: debouncedSearch.trim() || undefined,
        status: (statusFilter || undefined) as ResourceStatus | undefined
      });
      if (requestId !== requestSeq.current) return;
      // 名单计数页内 join（随页大小有界），失败回退 0。
      const specs = await Promise.all(
        result.items.map(async (item) => {
          try {
            const detail = await api.getResource("policy", item.resourceId, item.currentVersion);
            const spec = detail.spec as Record<string, unknown>;
            return {
              allow: Array.isArray(spec.allowed_tools) ? spec.allowed_tools.length : 0,
              deny: Array.isArray(spec.denied_tools) ? spec.denied_tools.length : 0
            };
          } catch {
            return { allow: 0, deny: 0 };
          }
        })
      );
      if (requestId !== requestSeq.current) return;
      setRows(
        result.items.map((item, index) => ({
          key: `${item.resourceId}@${item.currentVersion}`,
          name: item.displayName || item.resourceId,
          resourceId: item.resourceId,
          version: item.currentVersion,
          status: item.status,
          updatedAt: item.updatedAt,
          allowCount: specs[index]?.allow ?? 0,
          denyCount: specs[index]?.deny ?? 0
        }))
      );
      setTotal(result.total);
    } catch (cause) {
      if (requestId !== requestSeq.current) return;
      setError(cause instanceof Error ? cause.message : "加载失败");
    }
  }, [api, debouncedSearch, page, statusFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh, reloadKey]);

  function reload(): void {
    setReloadKey((key) => key + 1);
  }

  function confirmDelete(row: ListRow): void {
    Modal.confirm({
      title: `删除授权规则「${row.name}」？`,
      content:
        "将把当前发布版本标记为已弃用；绑定此策略的租户工具面随之放宽，请确认影响范围。",
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          const resource = await api.getResource("policy", row.resourceId, row.version);
          if (resource.status !== "published") {
            Toast.warning("草稿不可删除；请先发布，或在版本治理中处理未发布版本");
            return;
          }
          await api.deprecateVersion(resource, "Console 列表删除操作");
          Toast.success("策略版本已弃用");
          reload();
        } catch (cause) {
          Toast.error(cause instanceof Error ? cause.message : "删除失败");
        }
      }
    });
  }

  async function publish(row: ListRow): Promise<void> {
    try {
      const resource = await api.getResource("policy", row.resourceId, row.version);
      await api.publishVersion(resource);
      Toast.success("策略已发布");
      reload();
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "发布失败");
    }
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="授权规则"
        description="策略约束 Agent 可调用的工具与能力：白名单非空时仅放行所列，黑名单始终优先拒绝。"
      />
      <div aria-label="授权规则列表">
        <StandardListCard
          empty={rows !== null && total === 0}
          emptyDescription="暂无授权规则"
          error={error}
          footer={
            rows !== null && total > 0 ? (
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
                  <span className="sr-only" id="policy-status-filter-label">
                    状态过滤
                  </span>
                  <Select
                    aria-labelledby="policy-status-filter-label"
                    onChange={(value) => {
                      setStatusFilter(String(value ?? ""));
                      setPage(1);
                    }}
                    optionList={[
                      { label: "全部状态", value: "" },
                      { label: "草稿", value: "draft" },
                      { label: "已发布", value: "published" },
                      { label: "已弃用", value: "deprecated" }
                    ]}
                    placeholder="状态"
                    style={{ width: 130 }}
                    value={statusFilter}
                  />
                </>
              }
              primary={
                <Button
                  aria-label="新建规则"
                  icon={<IconPlus />}
                  onClick={() => setCreateOpen(true)}
                  theme="solid"
                  type="primary"
                >
                  新建规则
                </Button>
              }
              search={
                <StandardListSearch
                  onChange={(value) => {
                    setSearch(value);
                  }}
                  placeholder="搜索授权规则"
                  value={search}
                />
              }
            />
          }
        >
          <Table<ListRow>
            aria-label="授权规则表格"
            empty={
              <EmptyState
                description="白名单非空时仅放行所列工具；黑名单始终优先拒绝。先新建规则，再进入编辑器配置名单。"
                title="暂无授权规则"
              />
            }
            columns={[
              {
                dataIndex: "name",
                render: (_value, record) => (
                  <Button
                    onClick={() => navigate(`/build/policies/${record.resourceId}/edit`)}
                    type="tertiary"
                  >
                    {record.name}
                  </Button>
                ),
                title: "规则名"
              },
              {
                dataIndex: "resourceId",
                render: (value: string) => <ResourceId id={value} />,
                title: "ID"
              },
              { dataIndex: "version", title: "版本" },
              {
                dataIndex: "status",
                render: (status: string) => <StatusTag status={status as ResourceStatus} />,
                title: "状态"
              },
              {
                dataIndex: "updatedAt",
                render: (value: string) => <RelativeTime value={value} />,
                title: "更新时间"
              },
              {
                dataIndex: "allowCount",
                render: (_value, record) => (
                  <Space>
                    <Tag>白 {record.allowCount}</Tag>
                    <Tag color="red">黑 {record.denyCount}</Tag>
                  </Space>
                ),
                title: "名单"
              },
              {
                dataIndex: "resourceId",
                render: (_value, record) => (
                  <RowActions
                    immediate={[
                      {
                        key: "edit",
                        content: "编辑",
                        onClick: () => navigate(`/build/policies/${record.resourceId}/edit`)
                      }
                    ]}
                    more={[
                      { key: "publish", content: "发布", onClick: () => void publish(record) },
                      { key: "detail", content: "详情", onClick: () => setDetail(record) },
                      { key: "delete", content: "删除", onClick: () => confirmDelete(record) }
                    ]}
                  />
                ),
                title: "操作"
              }
            ]}
            dataSource={[...(rows ?? [])]}
            pagination={false}
            rowKey="key"
          />
        </StandardListCard>
      </div>
      <CreatePolicyModal
        api={api}
        onClose={() => setCreateOpen(false)}
        onCreated={(created) => {
          setCreateOpen(false);
          reload();
          navigate(`/build/policies/${created.resourceId}/edit`);
        }}
        visible={createOpen}
      />
      <SideSheet
        onCancel={() => setDetail(null)}
        title="授权规则详情"
        visible={detail !== null}
        width={640}
      >
        {detail ? <PolicyDetail api={api} resourceId={detail.resourceId} /> : null}
      </SideSheet>
    </div>
  );
}

function PolicyDetail({ api, resourceId }: { readonly api: ConsoleApi; readonly resourceId: string }) {
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let active = true;
    void (async () => {
      const resource = await api.getResource("policy", resourceId);
      if (active) setSpec(resource.spec);
    })();
    return () => {
      active = false;
    };
  }, [api, resourceId]);
  const allowed = Array.isArray(spec?.allowed_tools) ? (spec?.allowed_tools as string[]) : [];
  const denied = Array.isArray(spec?.denied_tools) ? (spec?.denied_tools as string[]) : [];
  return (
    <div aria-label="授权规则详情" style={{ display: "grid", gap: 16 }}>
      <Descriptions row>
        <Descriptions.Item itemKey="ID">{resourceId}</Descriptions.Item>
        <Descriptions.Item itemKey="名称">{String(spec?.name ?? "-")}</Descriptions.Item>
      </Descriptions>
      <div>
        <Typography.Text strong>工具白名单</Typography.Text>
        <div style={{ paddingTop: 8 }}>
          {allowed.length ? (
            allowed.map((tool) => <Tag key={tool}>{tool}</Tag>)
          ) : (
            <Typography.Text type="tertiary">留空（不限定）</Typography.Text>
          )}
        </div>
      </div>
      <div>
        <Typography.Text strong>工具黑名单</Typography.Text>
        <div style={{ paddingTop: 8 }}>
          {denied.length ? (
            denied.map((tool) => (
              <Tag color="red" key={tool}>
                {tool}
              </Tag>
            ))
          ) : (
            <Typography.Text type="tertiary">无</Typography.Text>
          )}
        </div>
      </div>
    </div>
  );
}
