import type { ReactNode } from 'react';
import { Card, Table } from '@douyinfe/semi-ui';
import type { ColumnProps } from '@douyinfe/semi-ui/lib/es/table';
import { ListFooter, type ListPagination } from './ListFooter';
import { ListToolbar } from './ListToolbar';

interface StandardListPageProps<T extends Record<string, unknown>> {
  rowKey: string | ((record: T | undefined) => string);
  columns: ColumnProps<T>[];
  dataSource: T[];
  loading?: boolean;
  primaryActions?: ReactNode;
  filters?: ReactNode;
  pagination: ListPagination;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  empty?: ReactNode;
}

/**
 * Mandatory shell for Console list pages.
 *
 * Layout contract:
 * 1) primary actions in top-left;
 * 2) filters/search in top-right;
 * 3) table in the middle;
 * 4) pagination footer at bottom-right.
 *
 * Remote paging is controlled outside the Semi Table. We intentionally set
 * pagination={false} so every page shares one footer implementation.
 */
export function StandardListPage<T extends Record<string, unknown>>({
  rowKey,
  columns,
  dataSource,
  loading,
  primaryActions,
  filters,
  pagination,
  onPageChange,
  onPageSizeChange,
  empty,
}: StandardListPageProps<T>) {
  return (
    <Card className="standard-list-card" bodyStyle={{ padding: 20 }}>
      <ListToolbar primaryActions={primaryActions} filters={filters} />
      <div className="standard-list-table">
        <Table<T>
          rowKey={rowKey}
          columns={columns}
          dataSource={dataSource}
          loading={loading}
          pagination={false}
          empty={empty}
        />
      </div>
      <ListFooter
        {...pagination}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </Card>
  );
}
