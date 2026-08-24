import { Button, Pagination, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconUndo } from "@douyinfe/semi-icons";

import { StatusTag } from "../../components/StatusTag";
import type { PageData, ResourceVersion } from "../../types/console";

export const VERSION_PAGE_SIZE = 20;

interface ResourceVersionsPanelProps {
  readonly resource: ResourceVersion;
  readonly versionPage: number;
  readonly versions: PageData<ResourceVersion> | null;
  readonly onRollback: (resource: ResourceVersion, version: string) => void;
  readonly onVersionPageChange: (page: number) => void;
}

export function ResourceVersionsPanel({
  onRollback,
  onVersionPageChange,
  resource,
  versionPage,
  versions
}: ResourceVersionsPanelProps) {
  const page = versions ?? { items: [], page: 1, pageSize: VERSION_PAGE_SIZE, total: 0 };
  return (
    <section aria-label="Versions" className="panel">
      <Typography.Title heading={4}>Versions</Typography.Title>
      <Typography.Text>版本总数 {page.total}</Typography.Text>
      <Table columns={versionColumns(resource, onRollback)} dataSource={[...page.items]} pagination={false} rowKey="version" />
      <VersionPager onChange={onVersionPageChange} page={versionPage} total={page.total} />
    </section>
  );
}

export function RetentionPanel() {
  return (
    <section className="panel">
      <Typography.Text>Audit 保留 30 天热查询</Typography.Text>
      <Typography.Text>Trace 历史按 execution_id 查询</Typography.Text>
    </section>
  );
}

function VersionPager({
  onChange,
  page,
  total
}: {
  readonly onChange: (page: number) => void;
  readonly page: number;
  readonly total: number;
}) {
  return (
    <Space>
      <Button disabled={page <= 1} onClick={() => onChange(page - 1)}>
        上一页
      </Button>
      <Typography.Text>第 {page} 页</Typography.Text>
      <Button disabled={page * VERSION_PAGE_SIZE >= total} onClick={() => onChange(page + 1)}>
        下一页
      </Button>
      <Pagination
        currentPage={page}
        nextText="Next"
        onPageChange={onChange}
        pageSize={VERSION_PAGE_SIZE}
        prevText="Prev"
        total={total}
      />
    </Space>
  );
}

function versionColumns(resource: ResourceVersion, onRollback: (resource: ResourceVersion, version: string) => void) {
  return [
    { dataIndex: "version", title: "Version" },
    {
      render: (_value: unknown, record: ResourceVersion) => <StatusTag status={record.status} />,
      title: "Status"
    },
    {
      render: (_value: unknown, record: ResourceVersion) =>
        record.status === "published" && record.version !== resource.version ? (
          <Button
            aria-label={`Rollback to ${record.version}`}
            icon={<IconUndo />}
            onClick={() => onRollback(resource, record.version)}
          >
            Rollback to {record.version}
          </Button>
        ) : null,
      title: "Action"
    }
  ];
}
