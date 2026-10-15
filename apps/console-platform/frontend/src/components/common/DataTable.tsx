import { Button, Popover, Table } from '@douyinfe/semi-ui';
import { IconMore } from '@douyinfe/semi-icons';
import type { TableProps } from '@douyinfe/semi-ui/lib/es/table/interface';
import { isValidElement, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { tableLayout, type ColumnGrowth } from './tableLayout';

interface DataTableProps<T extends object> extends TableProps<T> {
  columnGrowth?: ColumnGrowth;
  /**
   * 容器过窄时是否把行内动作收进右侧固定 56px「更多」菜单（默认 true）。
   *
   * 折叠口径是「容器宽度 < 操作列宽 + 200px 数据」，对主列表成立（表宽跟随容器）。详情表的最小
   * 表宽由 CSS `--app-table-min-width` 固定、窄屏靠**表内**横向滚动承载，表宽本身始终放得下操作列
   * 与数据；此时按容器折叠会把动作藏进菜单，与「窄屏保留表内滚动与右侧固定操作列」相冲突
   * （记忆表 en-US 的三个行内动作需要 196px 列宽，而 390px 视口下容器只有 366px），这些表显式关闭。
   */
  collapseActions?: boolean;
}

/** Common column sizing and horizontal scrolling for tables with fixed row actions. */
export function DataTable<T extends object>(props: DataTableProps<T>) {
  const { columnGrowth, collapseActions = true, ...tableProps } = props;
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
    if (column.key !== 'actions' || !collapseActions || width === 0 || width >= Number(column.width ?? 160) + 200) return column;
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
