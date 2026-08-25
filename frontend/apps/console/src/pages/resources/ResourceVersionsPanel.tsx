import { Button, Card, Empty, Table } from "@douyinfe/semi-ui";
import { IconUndo } from "@douyinfe/semi-icons";

import { ListPager } from "../../components/ListPager";
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
    <Card aria-label="Versions" bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }} title={`版本（共 ${page.total} 个）`}>
      <Table
        columns={versionColumns(resource, onRollback)}
        dataSource={[...page.items]}
        empty={<Empty description="暂无版本" />}
        pagination={false}
        rowKey="version"
      />
      <ListPager
        onChange={onVersionPageChange}
        page={versionPage}
        pageSize={VERSION_PAGE_SIZE}
        total={page.total}
      />
    </Card>
  );
}

function versionColumns(resource: ResourceVersion, onRollback: (resource: ResourceVersion, version: string) => void) {
  return [
    { dataIndex: "version", title: "版本" },
    {
      render: (_value: unknown, record: ResourceVersion) => <StatusTag status={record.status} />,
      title: "状态"
    },
    {
      render: (_value: unknown, record: ResourceVersion) =>
        record.status === "published" && record.version !== resource.version ? (
          <Button
            aria-label={`回滚到 ${record.version}`}
            icon={<IconUndo />}
            onClick={() => onRollback(resource, record.version)}
          >
            回滚到 {record.version}
          </Button>
        ) : null,
      title: "操作"
    }
  ];
}
