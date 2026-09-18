import { Table } from '@douyinfe/semi-ui';
import type { ComponentProps } from 'react';

type SemiTableProps = ComponentProps<typeof Table>;

export interface RemoteTableProps<T extends Record<string, unknown>> {
  columns: SemiTableProps['columns'];
  dataSource: T[];
  rowKey: string;
  loading?: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number): void;
}

export function RemoteTable<T extends Record<string, unknown>>(props: RemoteTableProps<T>) {
  return (
    <Table
      columns={props.columns}
      dataSource={props.dataSource}
      rowKey={props.rowKey}
      loading={props.loading}
      pagination={{
        currentPage: props.page,
        pageSize: props.pageSize,
        total: props.total,
        onPageChange: props.onPageChange
      }}
    />
  );
}
