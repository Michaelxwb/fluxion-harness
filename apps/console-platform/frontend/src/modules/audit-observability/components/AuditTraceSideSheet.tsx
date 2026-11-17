/**
 * Trace 详情侧栏：只读展示某条 Trace 下的全部审计记录。
 *
 * 与运行详情分工：Trace 回答「这条链路上发生了哪些审计事实」，运行详情回答「这次对话怎么跑的」。
 * 数据复用列表 service 的明细视图（`view=detail` + `trace_id`），不新增后端接口。
 */

import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { DetailSideSheet } from '../../../components/common/DetailSideSheet';
import { EmptyState } from '../../../components/common/EmptyState';
import { ErrorState } from '../../../components/common/ErrorState';
import { StatusTag } from '../../../components/common/StatusTag';
import { auditActionLabel, auditStatusOptions } from '../auditLabels';
import { useAuditList } from '../hooks/useAuditList';
import type { AuditListQuery } from '../types';

export interface AuditTraceSideSheetProps {
  /** 为 null 时不渲染。 */
  traceId: string | null;
  onClose(): void;
}

/** 单条 Trace 的记录上限取列表 page_size 上限；超出时 footer 文案仍是真实总数。 */
const TRACE_PAGE_SIZE = 100;

export function AuditTraceSideSheet({ traceId, onClose }: AuditTraceSideSheetProps): JSX.Element | null {
  const { t } = useTranslation();
  // 查询对象必须稳定：`useAuditList` 按引用重取，每次渲染新建会形成请求循环
  const query = useMemo<AuditListQuery>(
    () => ({
      view: 'detail',
      traceId: traceId ?? undefined,
      page: 1,
      pageSize: TRACE_PAGE_SIZE
    }),
    [traceId]
  );
  const { items, total, loading, failed, reload } = useAuditList(query);
  const statusOptions = auditStatusOptions(t);

  if (traceId === null) {
    return null;
  }

  return (
    <DetailSideSheet
      visible
      title={t('audit.trace.title')}
      subtitle={<span className="audit-object-id">{traceId}</span>}
      activeTab="records"
      onCancel={onClose}
      closeOnEsc
      notice={failed ? <ErrorState onRetry={() => void reload()} /> : undefined}
    >
      <Tabs.TabPane itemKey="records" tab={t('audit.trace.records', { count: total })}>
        {loading && items.length === 0 ? <Spin /> : null}
        {!loading && !failed && items.length === 0 ? (
          <EmptyState title={t('audit.trace.empty')} />
        ) : null}
        {items.length > 0 ? (
          <ul className="audit-trace-list">
            <li className="audit-trace-row audit-trace-head" aria-hidden="true">
              <span>{t('audit.columns.time')}</span>
              <span>{t('audit.columns.auditType')}</span>
              <span>{t('audit.columns.action')}</span>
              <span>{t('audit.columns.result')}</span>
            </li>
            {items.map((item) => (
              <li key={item.auditId} className="audit-trace-row" data-testid={`audit-trace-row-${item.auditId}`}>
                <DateTimeText value={item.occurredAt} />
                <span className="audit-trace-type">
                  <span className="audit-type-dot" data-kind={item.auditType} />
                  {t(`audit.auditType.${item.auditType}`)}
                </span>
                <span className="audit-trace-action" title={auditActionLabel(t, item)}>
                  {auditActionLabel(t, item)}
                </span>
                <StatusTag status={item.resultStatus} options={statusOptions} />
              </li>
            ))}
          </ul>
        ) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
