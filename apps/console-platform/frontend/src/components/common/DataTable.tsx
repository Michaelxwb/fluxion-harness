import { Button, Popover, Table } from '@douyinfe/semi-ui';
import { IconMore } from '@douyinfe/semi-icons';
import type { TableProps } from '@douyinfe/semi-ui/lib/es/table/interface';
import { isValidElement, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { tableLayout, type ColumnGrowth } from './tableLayout';

interface DataTableProps<T extends object> extends TableProps<T> {
  columnGrowth?: ColumnGrowth;
}

/** Common column sizing and horizontal scrolling for tables with fixed row actions. */
export function DataTable<T extends object>(props: DataTableProps<T>) {
  const { columnGrowth, ...tableProps } = props;
  const { t } = useTranslation();
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const columns = props.columns?.map((column) => {
    if (column.key !== 'actions' || width === 0 || width >= Number(column.width ?? 160) + 200) return column;
    return {
      ...column,
      width: 56,
      render: (...args: Parameters<NonNullable<typeof column.render>>) => {
        const actions = column.render?.(...args);
        if (!actions || actions === '-') return actions;
        if (typeof actions === 'object' && 'children' in actions && !isValidElement(actions)) return actions;
        return (
          <Popover trigger="click" position="bottomRight" content={<div className="table-actions-menu">{actions}</div>}>
            <Button theme="borderless" size="small" icon={<IconMore />} aria-label={t('common.more')} />
          </Popover>
        );
      }
    };
  });
  return <div ref={container}><Table<T> {...tableProps} {...tableLayout(columns, props.scroll, width, columnGrowth)} /></div>;
}
