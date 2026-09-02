import { useEffect, useState } from "react";

import { Card, Empty, Spin, Table, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord, ResourceSummary } from "../../types/console";

interface CredentialsPageProps {
  readonly api: ConsoleApi;
}

interface CredentialRow {
  readonly key: string;
  readonly displayName: string;
  readonly resourceId: string;
  readonly secretRef: string;
  readonly status: ResourceSummary["status"];
}

/** TASK-016（FEAT-F02/F-S-10）：凭据领域独立列表页（替代万能 Resource 页）。
 *
 * 只读列表：Secret 资源元数据 + SecretRef 引用；明文密钥不存在于 Registry
 * （规则 17——Secret 不进 Resource Spec/日志/Trace），页面不提供任何回显。
 * 详情（含 secret_ref spec）经 Promise.all 并行批量拉取，不做逐条串行 N+1。
 */
export function CredentialsPage({ api }: CredentialsPageProps) {
  const [rows, setRows] = useState<readonly CredentialRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const page = await api.listResources("secret");
        // 并行批量详情（spec.secret_ref），非串行 N+1
        const details = await Promise.all(
          page.items.map((item) => api.getResource("secret", item.resourceId))
        );
        if (!active) return;
        setRows(
          page.items.map((item, index) => ({
            key: item.resourceId,
            displayName: item.displayName,
            resourceId: item.resourceId,
            secretRef: String(
              (details[index]?.spec as JsonRecord | undefined)?.secret_ref ?? "-"
            ),
            status: item.status
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

  return (
    <div className="page-stack">
      <PageHeader
        description="凭据以 SecretRef 引用外部 SecretStore；Console 只管理元数据，不出现明文。"
        title="凭据"
      />
      <ErrorBanner message={error} onRetry={() => setReloadKey((key) => key + 1)} />
      <Card aria-label="凭据列表">
        {rows === null ? (
          <div aria-label="凭据加载中">
            <Spin />
          </div>
        ) : rows.length === 0 ? (
          <Empty description="暂无凭据" />
        ) : (
          <Table
            columns={[
              { title: "名称", dataIndex: "displayName" },
              { title: "资源 ID", dataIndex: "resourceId" },
              { title: "SecretRef", dataIndex: "secretRef" },
              {
                title: "状态",
                dataIndex: "status",
                render: (value: string) => <StatusTag status={value as ResourceSummary["status"]} />
              }
            ]}
            dataSource={rows.map((row) => row)}
            pagination={false}
          />
        )}
        {rows !== null && rows.length > 0 ? (
          <Typography.Text type="tertiary">共 {rows.length} 个凭据</Typography.Text>
        ) : null}
      </Card>
    </div>
  );
}
