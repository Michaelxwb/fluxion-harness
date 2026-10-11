import type { ComponentProps } from 'react';
import type { Table } from '@douyinfe/semi-ui';

type TableProps = ComponentProps<typeof Table>;
export type ColumnGrowth = Readonly<Record<string, number>>;
const DEFAULT_COLUMN_WIDTH = 176;

export function actionColumn(width = 160) {
  return { key: 'actions', fixed: 'right' as const, width, className: 'table-actions-cell' };
}

export function tableLayout(
  columns: TableProps['columns'], scroll: TableProps['scroll'],
  availableWidth = 0, columnGrowth?: ColumnGrowth
) {
  if (!columns?.some((column) => column.key === 'actions')) return { columns, scroll };
  const sized = columns.map((column) => ({ ...column, width: column.width ?? DEFAULT_COLUMN_WIDTH }));
  const width = sized.reduce((total, column) => total + (typeof column.width === 'number' ? column.width : DEFAULT_COLUMN_WIDTH), 0);
  const extra = Math.max(0, availableWidth - width);
  const expanded = columnGrowth ? sized.map((column) => ({
    ...column,
    width: typeof column.width === 'number'
      ? column.width + extra * (columnGrowth[String(column.dataIndex ?? column.key)] ?? 0)
      : column.width
  })) : sized;
  const expandedWidth = expanded.reduce((total, column) => total + (typeof column.width === 'number' ? column.width : DEFAULT_COLUMN_WIDTH), 0);
  return {
    columns: expanded,
    scroll: { ...scroll, x: typeof scroll?.x === 'number' ? Math.max(scroll.x, expandedWidth) : scroll?.x ?? expandedWidth }
  };
}
