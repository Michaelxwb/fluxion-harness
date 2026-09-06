import { useEffect, useMemo, useState } from "react";

import { Button, Select, Table, Tag } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import {
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord } from "../../types/console";
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

const PAGE_SIZE = 20;

/** TASK-009：凭据完整 Journey（Golden Path blocker）。
 *
 * 标准列表 Shell（左上新增/右上过滤+搜索/右下总数+分页）+ 创建 Modal（明文只写，
 * 规则 17）+ 行操作（编辑元数据/轮换/禁用，高风险带二次确认与影响说明）+
 * 只读详情 SideSheet。SecretRef 保留展示（元数据非明文）。使用方为客户端 join
 * （Provider spec.credential_ref 匹配），TASK-025 Projection API 统一消除。
 */
export function CredentialsPage({ api }: CredentialsPageProps) {
  const [rows, setRows] = useState<readonly CredentialRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [modalVisible, setModalVisible] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [editRow, setEditRow] = useState<CredentialRow | null>(null);
  const [rotateRow, setRotateRow] = useState<CredentialRow | null>(null);
  const [disableRow, setDisableRow] = useState<CredentialRow | null>(null);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const pageData = await api.listResources("secret");
        const [details, providerDetails] = await Promise.all([
          Promise.all(
            pageData.items.map((item) => api.getResource("secret", item.resourceId))
          ),
          api.listResources("model_provider").then((result) =>
            Promise.all(
              result.items.map((provider) =>
                api.getResource("model_provider", provider.resourceId)
              )
            )
          )
        ]);
        if (!active) return;
        setRows(
          pageData.items.map((item, index) => {
            const spec = (details[index]?.spec ?? {}) as JsonRecord;
            const secretRef = String(spec.secret_ref ?? "-");
            return {
              key: item.resourceId,
              displayName: String(spec.name ?? item.displayName),
              resourceId: item.resourceId,
              secretRef,
              purpose: String(spec.purpose ?? ""),
              revoked: spec.revoked === true,
              updatedAt: details[index]?.updatedAt ?? "-",
              // 使用方 join：Provider spec.credential_ref → 该凭据 SecretRef
              consumers: providerDetails
                .filter((provider) => {
                  const credentialRef = String(
                    (provider.spec as JsonRecord | undefined)?.credential_ref ?? ""
                  );
                  return credentialRef === secretRef;
                })
                .map(
                  (provider) =>
                    String((provider.spec as JsonRecord | undefined)?.name ?? provider.resourceId)
                ),
              status: item.status
            };
          })
        );
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  const typeOptions = useMemo(() => {
    const purposes = new Set((rows ?? []).map((row) => row.purpose).filter(Boolean));
    return Array.from(purposes, (purpose) => ({
      label: purpose,
      value: purpose
    }));
  }, [rows]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const keyword = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (typeFilter !== undefined && row.purpose !== typeFilter) return false;
      if (statusFilter !== undefined) {
        if (statusFilter === "revoked" ? !row.revoked : row.status !== statusFilter) return false;
      }
      if (!keyword) return true;
      return (
        row.displayName.toLowerCase().includes(keyword) ||
        row.resourceId.toLowerCase().includes(keyword)
      );
    });
  }, [rows, search, typeFilter, statusFilter]);

  const total = filtered.length;
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function reload(): void {
    setReloadKey((key) => key + 1);
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
      <StandardListCard
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
                <span className="sr-only" id="credential-type-filter-label">
                  凭据类型过滤
                </span>
                <Select
                  aria-labelledby="credential-type-filter-label"
                  onChange={(value) => {
                    setTypeFilter(value === "" ? undefined : String(value));
                    setPage(1);
                  }}
                  optionList={[{ label: "全部类型", value: "" }, ...typeOptions]}
                  placeholder="类型"
                  showClear
                  style={{ width: 140 }}
                  value={typeFilter ?? ""}
                />
                <span className="sr-only" id="credential-status-filter-label">
                  凭据状态过滤
                </span>
                <Select
                  aria-labelledby="credential-status-filter-label"
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
                  theme="borderless"
                  type="tertiary"
                >
                  {value}
                </Button>
              )
            },
            { title: "类型", dataIndex: "purpose", render: (value: string) => value || "-" },
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
                value.length > 0 ? value.join("、") : "-"
            },
            { title: "SecretRef", dataIndex: "secretRef" },
            { title: "更新时间", dataIndex: "updatedAt" },
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
                    {
                      key: "detail",
                      content: "查看详情",
                      onClick: () => setDetailId(record.resourceId)
                    },
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
          dataSource={paged.map((row) => row)}
          pagination={false}
          rowKey="key"
        />
      </StandardListCard>
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
