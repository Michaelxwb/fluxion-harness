import { useEffect, useState } from "react";

import { Pagination, Space, Table, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import type { AuditRecord, ConsoleApi, PageData } from "../../types/console";

interface AuditPageProps {
  readonly api: ConsoleApi;
}

const PAGE_SIZE = 20;

export function AuditPage({ api }: AuditPageProps) {
  const [page, setPage] = useState<PageData<AuditRecord> | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  async function loadAudit(nextPage: number): Promise<void> {
    try {
      setCurrentPage(nextPage);
      setPage(await api.listAudit({ page: nextPage, pageSize: PAGE_SIZE }));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "未知错误");
    }
  }

  useEffect(() => {
    void loadAudit(1);
  }, []);

  const columns = [
    { dataIndex: "action", title: "Action" },
    { dataIndex: "actorId", title: "Actor" },
    { dataIndex: "resourceId", title: "Resource" },
    { dataIndex: "resourceVersion", title: "Version" },
    { dataIndex: "at", title: "Time" }
  ];

  return (
    <div className="page-stack">
      <PageHeader description="Audit 是独立事实源，不以普通日志替代。" title="Audit" />
      <ErrorBanner message={error} />
      <Table columns={columns} dataSource={[...(page?.items ?? [])]} pagination={false} rowKey="id" />
      <Space>
        <Typography.Text>Audit 保留 30 天热查询</Typography.Text>
        <Pagination
          currentPage={currentPage}
          onPageChange={(nextPage) => void loadAudit(nextPage)}
          pageSize={PAGE_SIZE}
          total={page?.total ?? 0}
        />
      </Space>
    </div>
  );
}
