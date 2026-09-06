import { useEffect, useMemo, useState } from "react";

import { IconPlus } from "@douyinfe/semi-icons";
import { Button, Modal, Select, Table, Toast, Typography } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import {
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { SpecDiffModal } from "../../components/SpecDiffModal";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord, ResourceSummary, ResourceVersion } from "../../types/console";
import { CreateWorkflowModal } from "./CreateWorkflowModal";

interface WorkflowsPageProps {
  readonly api: ConsoleApi;
}

const PAGE_SIZE = 10;

interface WorkflowRow extends ResourceSummary {
  readonly key: string;
}

/** TASK-015（§8.2）：工作流标准列表——新建 Modal、搜索/过滤、右下分页、
 * 行操作（编辑直达独立 Designer；发布/版本历史/删除收纳 Dropdown）。
 * Editor 已迁出为独立路由 `/build/workflows/:id/edit`（不再内联列表下方）。
 */
export function WorkflowsPage({ api }: WorkflowsPageProps) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<readonly WorkflowRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [versionsFor, setVersionsFor] = useState<ResourceSummary | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    setRows(null);
    void (async () => {
      try {
        const result = await api.listResources("workflow");
        if (!active) return;
        setRows(
          result.items
            .map((item) => ({ ...item, key: item.resourceId }))
            .sort((left, right) =>
              (left.displayName || left.resourceId).localeCompare(
                right.displayName || right.resourceId,
                "zh-CN",
                { numeric: true }
              )
            )
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
    const keyword = search.trim().toLowerCase();
    return (rows ?? []).filter((row) => {
      if (statusFilter && row.status !== statusFilter) return false;
      return (
        !keyword ||
        (row.displayName || "").toLowerCase().includes(keyword) ||
        row.resourceId.toLowerCase().includes(keyword)
      );
    });
  }, [rows, search, statusFilter]);
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function reload(): void {
    setReloadKey((key) => key + 1);
  }

  function confirmDelete(row: WorkflowRow): void {
    Modal.confirm({
      title: `删除工作流「${row.displayName || row.resourceId}」？`,
      content:
        "将把当前发布版本标记为已弃用；引用此工作流的 Agent 需重新选择默认工作流。运行中的 ExecutionSnapshot 不受影响。",
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          const resource = await api.getResource("workflow", row.resourceId, row.currentVersion);
          if (resource.status !== "published") {
            Toast.warning("草稿不可删除；请先发布，或在版本治理中处理未发布版本");
            return;
          }
          await api.deprecateVersion(resource, "Console 列表删除操作");
          Toast.success("工作流版本已弃用");
          reload();
        } catch (cause) {
          Toast.error(cause instanceof Error ? cause.message : "删除失败");
        }
      }
    });
  }

  return (
    <div className="page-stack">
      <PageHeader
        description="管理工作流定义（WorkflowDefinition）DSL、校验与不可变版本。"
        title="流程编排"
      />
      <ErrorBanner message={error} />
      <div aria-label="工作流列表">
        <StandardListCard
          empty={rows !== null && filtered.length === 0}
          emptyDescription="暂无工作流"
          error={error}
          footer={
            rows !== null && filtered.length > 0 ? (
              <StandardListFooter
                onPageChange={setPage}
                page={page}
                pageSize={PAGE_SIZE}
                total={filtered.length}
              />
            ) : undefined
          }
          loading={rows === null && !error}
          onRetry={reload}
          toolbar={
            <StandardListToolbar
              filters={
                <>
                  <span className="sr-only" id="workflow-status-filter-label">
                    状态过滤
                  </span>
                  <Select
                    aria-labelledby="workflow-status-filter-label"
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
                  aria-label="新建工作流"
                  icon={<IconPlus />}
                  onClick={() => setCreateOpen(true)}
                  theme="solid"
                  type="primary"
                >
                  新建工作流
                </Button>
              }
              search={
                <StandardListSearch
                  onChange={(value) => {
                    setSearch(value);
                    setPage(1);
                  }}
                  placeholder="搜索工作流"
                  value={search}
                />
              }
            />
          }
        >
          <Table<WorkflowRow>
            aria-label="工作流列表表格"
            columns={[
              {
                dataIndex: "displayName",
                render: (_value, record) => (
                  <Button
                    onClick={() => navigate(`/build/workflows/${record.resourceId}/edit`)}
                    type="tertiary"
                  >
                    {record.displayName || record.resourceId}
                  </Button>
                ),
                title: "名称"
              },
              { dataIndex: "resourceId", title: "工作流 ID" },
              { dataIndex: "currentVersion", title: "当前版本" },
              {
                render: (_value, record) => <StatusTag status={record.status} />,
                title: "状态"
              },
              {
                render: (_value, record) => (
                  <RowActions
                    immediate={[
                      {
                        key: "edit",
                        content: "编辑",
                        onClick: () => navigate(`/build/workflows/${record.resourceId}/edit`)
                      }
                    ]}
                    more={[
                      {
                        key: "versions",
                        content: "版本历史",
                        onClick: () => setVersionsFor(record)
                      },
                      {
                        key: "delete",
                        content: "删除",
                        onClick: () => confirmDelete(record)
                      }
                    ]}
                  />
                ),
                title: "操作"
              }
            ]}
            dataSource={[...paged]}
            pagination={false}
            rowKey="key"
          />
        </StandardListCard>
      </div>
      <CreateWorkflowModal
        api={api}
        onClose={() => setCreateOpen(false)}
        onCreated={(created) => {
          setCreateOpen(false);
          reload();
          navigate(`/build/workflows/${created.resourceId}/edit`);
        }}
        visible={createOpen}
      />
      <VersionsModal api={api} onClose={() => setVersionsFor(null)} target={versionsFor} />
    </div>
  );
}

function VersionsModal({
  api,
  onClose,
  target
}: {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly target: ResourceSummary | null;
}) {
  const [versions, setVersions] = useState<readonly ResourceVersion[]>([]);
  const [diff, setDiff] = useState<{ left: { label: string; spec: JsonRecord }; right: { label: string; spec: JsonRecord } } | null>(null);
  useEffect(() => {
    if (!target) return;
    let active = true;
    void (async () => {
      const result = await api.listVersions("workflow", target.resourceId, { page: 1, pageSize: 20 });
      if (active) setVersions(result.items);
    })();
    return () => {
      active = false;
    };
  }, [api, target]);

  // TASK-024（§14 P2）：选中两版本 → SpecDiffModal 键级变更摘要
  async function compare(selected: readonly ResourceVersion[]): Promise<void> {
    if (selected.length !== 2 || !target) return;
    const [first, second] = selected;
    if (!first || !second) return;
    const [leftSpec, rightSpec] = await Promise.all([
      api.getResource("workflow", target.resourceId, first.version),
      api.getResource("workflow", target.resourceId, second.version)
    ]);
    setDiff({
      left: { label: `v${first.version}`, spec: leftSpec.spec },
      right: { label: `v${second.version}`, spec: rightSpec.spec }
    } as { left: { label: string; spec: JsonRecord }; right: { label: string; spec: JsonRecord } });
  }

  return (
    <>
      <Modal
        cancelText="关 闭"
        footer={null}
        onCancel={onClose}
        title={target ? `版本历史 · ${target.displayName || target.resourceId}` : "版本历史"}
        visible={target !== null}
      >
        <Table
          columns={[
            { dataIndex: "version", title: "版本" },
            { dataIndex: "status", title: "状态" },
            { dataIndex: "updatedAt", title: "更新时间" }
          ]}
          dataSource={[...versions]}
          pagination={false}
          rowKey="version"
          rowSelection={{ onChange: (keys, rows) => void compare(rows as ResourceVersion[]) }}
          size="small"
        />
        <Typography.Text type="tertiary" size="small">
          勾选两个版本可对比 spec 键级变更。
        </Typography.Text>
      </Modal>
      <SpecDiffModal
        left={diff?.left ?? null}
        onClose={() => setDiff(null)}
        right={diff?.right ?? null}
        visible={diff !== null}
      />
    </>
  );
}
