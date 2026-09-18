import { Table } from '@douyinfe/semi-ui';
import type { ComponentProps } from 'react';

import { PaginationFooter } from './PaginationFooter';

type SemiTableProps = ComponentProps<typeof Table>;

export interface RemoteTableProps<T extends object> {
  columns: SemiTableProps['columns'];
  dataSource: T[];
  rowKey: string;
  loading?: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number): void;
  onPageSizeChange?(pageSize: number): void;
}

export function RemoteTable<T extends object>(props: RemoteTableProps<T>) {
  return (
    <>
      <Table
        columns={props.columns}
        dataSource={props.dataSource}
        rowKey={props.rowKey}
        loading={props.loading}
        pagination={false}
      />
      <PaginationFooter
        page={props.page}
        pageSize={props.pageSize}
        total={props.total}
        onPageChange={props.onPageChange}
        onPageSizeChange={props.onPageSizeChange}
      />
    </>
  );
}
