import type { Table } from '@douyinfe/semi-ui';
import type { ComponentProps, ReactNode } from 'react';

import { PaginationFooter } from './PaginationFooter';
import { DataTable } from './DataTable';
import type { ColumnGrowth } from './tableLayout';

type SemiTableProps = ComponentProps<typeof Table>;

export interface RemoteTableProps<T extends object> {
  className?: string;
  scroll?: SemiTableProps['scroll'];
  columns: SemiTableProps['columns'];
  columnGrowth?: ColumnGrowth;
  dataSource: T[];
  rowKey: string;
  loading?: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number): void;
  onPageSizeChange?(pageSize: number): void;
  empty?: ReactNode;
  rowSelection?: SemiTableProps['rowSelection'];
}

export function RemoteTable<T extends object>(props: RemoteTableProps<T>) {
  return (
    <>
      <DataTable<T>
        className={props.className}
        scroll={props.scroll}
        columns={props.columns}
        columnGrowth={props.columnGrowth}
        dataSource={props.dataSource}
        rowKey={props.rowKey}
        loading={props.loading}
        pagination={false}
        empty={props.empty}
        rowSelection={props.rowSelection}
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
