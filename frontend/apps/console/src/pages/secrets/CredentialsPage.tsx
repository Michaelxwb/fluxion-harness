import { useEffect, useRef, useState } from "react";

import { Button, Select, Space, Table, Tag, Toast } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import { EmptyState } from "../../components/EmptyState";
import { RelativeTime } from "../../components/RelativeTime";
import { ResourceId } from "../../components/ResourceId";
import {
  DEFAULT_PAGE_SIZE,
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, ResourceStatus } from "../../types/console";
import { CreateCredentialModal } from "./CreateCredentialModal";
import { CredentialDetailSideSheet } from "./CredentialDetailSideSheet";
import {
  DisableCredentialModal,
  EditCredentialModal,
  RotateCredentialModal
} from "./CredentialMetadataModals";
import type { CredentialRow } from "./credentialRow";

interface CredentialsPageProps {
  readonly api: ConsoleApi;
}

/** TASK-009：凭据完整 Journey（Golden Path blocker）。

/** TASK-009：凭据完整 Journey（Golden Path blocker）。
 *
 * 标准列表 Shell（左上新增/右上过滤+搜索/右下总数+分页）+ 创建 Modal（明文只写，
 * 规则 17）+ 行操作（编辑元数据/轮换/禁用，高风险带二次确认与影响说明）+
 * 只读详情 SideSheet。SecretRef 保留展示（元数据非明文）。
 * FEAT-04：列表经 Credential Projection 单请求（固定 3 SQL），删除此前的
 * 2+N+M 客户端 join；用途（purpose）下拉因需服务端枚举支持而移除（follow-up），
 * 可经搜索框按名称/ID 检索，purpose 精确过滤走接口参数保留。
 */
export function CredentialsPage({ api }: CredentialsPageProps) {
  const [rows, setRows] = useState<readonly CredentialRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [modalVisible, setModalVisible] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [editRow, setEditRow] = useState<CredentialRow | null>(null);
  const [rotateRow, setRotateRow] = useState<CredentialRow | null>(null);
  const [disableRow, setDisableRow] = useState<CredentialRow | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    requestSeq.current += 1;
    const requestId = requestSeq.current;
    setError(null);
    setRows(null);
    void (async () => {
      try {
        const result = await api.listCredentialProjection({
          page,
          pageSize,
          keyword: debouncedSearch.trim() || undefined,
          status: (statusFilter === "revoked" ? undefined : statusFilter) as ResourceStatus | undefined,
          revoked: statusFilter === undefined ? undefined : statusFilter === "revoked"
        });
        if (requestId !== requestSeq.current) return;
        setRows(
          result.items.map((item) => ({
            key: item.credentialId,
            displayName: item.displayName,
            resourceId: item.credentialId,
            secretRef: item.secretRef,
            purpose: item.purpose,
            revoked: item.revoked,
            updatedAt: item.updatedAt,
            consumers: item.consumers.map((consumer) => consumer.providerName),
            status: item.status,
            version: item.version
          }))
        );
        setTotal(result.total);
      } catch (cause) {
        if (requestId !== requestSeq.current) return;
        setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      requestSeq.current += 1;
    };
  }, [api, debouncedSearch, page, pageSize, reloadKey, statusFilter]);

  function reload(): void {
    setReloadKey((key) => key + 1);
  }

  async function publishRow(row: CredentialRow): Promise<void> {
    try {
      const resource = await api.getResource("secret", row.resourceId, row.version);
      if (resource.status !== "draft") {
        Toast.warning("仅草稿版本可发布");
        return;
      }
      await api.publishVersion(resource);
      Toast.success("凭据已发布");
      reload();
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "发布失败");
    }
  }

  function closeRowModals(): void {
    setEditRow(null);
    setRotateRow(null);
    setDisableRow(null);
  }

  return (
    <div className="page-stack">
      <PageHeader
        description="凭据以 SecretRef 引用外部 SecretStore；Console 只管理元数据，不出现明文。"
        title="凭据"
      />
      <div aria-label="凭据列表">
      <StandardListCard
        empty={rows !== null && total === 0}
        emptyDescription="暂无凭据"
        error={error}
        footer={
          rows !== null && total > 0 ? (
            <StandardListFooter
              onPageChange={setPage}
              onPageSizeChange={(next) => {
                setPageSize(next);
                setPage(1);
              }}
              page={page}
              pageSize={pageSize}
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
                <span className="sr-only" id="credential-status-filter-label">
                  凭据状态过滤
                </span>
                  <Select
                    aria-labelledby="credential-status-filter-label"
                    data-testid="credential-status-filter"
                  onChange={(value) => {
                    setStatusFilter(value === "" ? undefined : String(value));
                    setPage(1);
                  }}
                  optionList={[
                    { label: "全部状态", value: "" },
                    { label: "已禁用", value: "revoked" },
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
                aria-label="新增凭据"
                onClick={() => setModalVisible(true)}
                theme="solid"
                type="primary"
              >
                + 新增凭据
              </Button>
            }
            search={
              <StandardListSearch
                onChange={setSearch}
                placeholder="搜索名称 / 资源 ID"
                value={search}
              />
            }
          />
        }
      >
        <Table
          columns={[
            {
              title: "名称",
              dataIndex: "displayName",
              render: (value: string, record: CredentialRow) => (
                <Button
                  aria-label={`查看凭据 ${value}`}
                  onClick={() => setDetailId(record.resourceId)}
                  style={record.revoked ? { textDecoration: "line-through" } : undefined}
                  theme="borderless"
                  type="primary"
                >
                  {value}
                </Button>
              )
            },
            { title: "类型", dataIndex: "purpose", render: (value: string) => <PurposeTag purpose={value} /> },
            {
              title: "状态",
              dataIndex: "status",
              render: (value: string, record: CredentialRow) => (
                <span>
                  <StatusTag status={value as CredentialRow["status"]} />
                  {record.revoked ? (
                    <Tag color="red" style={{ marginLeft: 8 }}>
                      已禁用
                    </Tag>
                  ) : null}
                </span>
              )
            },
            {
              title: "使用方",
              dataIndex: "consumers",
              render: (value: readonly string[]) =>
                value.length > 0 ? (
                  <Space wrap>
                    {value.map((consumer) => (
                      <Tag key={consumer}>{consumer}</Tag>
                    ))}
                  </Space>
                ) : (
                  "-"
                )
            },
            {
              title: "SecretRef",
              dataIndex: "secretRef",
              render: (value: string) => <ResourceId id={value} />
            },
            {
              title: "更新时间",
              dataIndex: "updatedAt",
              render: (value: string) => <RelativeTime value={value} />
            },
            {
              title: "操作",
              dataIndex: "resourceId",
              render: (_value: string, record: CredentialRow) => (
                <RowActions
                  immediate={[
                    {
                      key: "edit",
                      content: "编辑",
                      onClick: () => setEditRow(record)
                    }
                  ]}
                  more={[
                    ...(record.status === "draft"
                      ? [
                          {
                            key: "publish",
                            content: "发布",
                            onClick: () => void publishRow(record)
                          }
                        ]
                      : []),
                    {
                      key: "rotate",
                      content: "轮换",
                      onClick: () => setRotateRow(record)
                    },
                    {
                      key: "disable",
                      content: "禁用",
                      onClick: () => setDisableRow(record)
                    }
                  ]}
                />
              )
            }
          ]}
          dataSource={[...(rows ?? [])]}
          empty={
            <EmptyState
              description="凭据以 SecretRef 引用外部 SecretStore；先新增凭据，再到模型服务中引用。"
              title="暂无凭据"
            />
          }
          pagination={false}
          rowKey="key"
        />
      </StandardListCard>
      </div>
      <CreateCredentialModal
        api={api}
        onClose={() => setModalVisible(false)}
        onCreated={() => {
          setModalVisible(false);
          reload();
        }}
        visible={modalVisible}
      />
      <CredentialDetailSideSheet api={api} onClose={() => setDetailId(null)} resourceId={detailId} />
      <EditCredentialModal
        api={api}
        onClose={closeRowModals}
        onDone={() => {
          closeRowModals();
          reload();
        }}
        row={editRow}
      />
      <RotateCredentialModal
        api={api}
        onClose={closeRowModals}
        onDone={() => {
          closeRowModals();
          reload();
        }}
        row={rotateRow}
      />
      <DisableCredentialModal
        api={api}
        onClose={closeRowModals}
        onDone={() => {
          closeRowModals();
          reload();
        }}
        row={disableRow}
      />
    </div>
  );
}

/** 用途缺失显示"未分类"（warn 色），不再裸奔横线；存量随编辑补齐。 */
function PurposeTag({ purpose }: { readonly purpose: string }) {
  if (!purpose) {
    return <Tag color="amber">未分类</Tag>;
  }
  return <Tag>{purpose}</Tag>;
}
