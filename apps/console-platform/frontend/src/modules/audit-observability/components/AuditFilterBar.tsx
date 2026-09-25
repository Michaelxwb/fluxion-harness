/**
 * 审计列表筛选栏（设计 §3.3 CMP-04 / §3.3.1）。
 *
 * 受控展示组件：`value` 由 `AuditPage` 持有，本组件只上抛筛选补丁，不直接调 service。
 * 所有变更统一经 `emit` 出口，把 `page` 重置为 1（设计 §3.4）；`undefined` 表示清空该筛选项
 * （service 层会丢弃未设置项，不会污染查询串）。后端 snake_case 只出现在 service 层。
 */

import { Button, DatePicker, Input, Select } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { AuditListQuery } from '../types';

export interface AuditFilterBarProps {
  value: AuditListQuery;
  onChange(patch: Partial<AuditListQuery>): void;
  /** 立即按当前条件重查（文本输入回车触发，见各 `onEnterPress`）。 */
  onSearch(): void;
  /** 清空全部筛选并回到第 1 页（设计 §3.3.1「重置」）。 */
  onReset(): void;
  /** 按当前筛选重取当前页（设计 §3.3.1「刷新」）。 */
  onRefresh(): void;
}

const AUDIT_TYPES = ['CONFIG', 'TOOL', 'EGRESS', 'MODEL'] as const;

/** 「Agent」列对应审计资源的资源类型（config 审计为配置对象类型）。 */
const RESOURCE_TYPES = [
  'AGENT',
  'SKILL',
  'MCP',
  'MODEL',
  'PROJECT_PLATFORM',
  'USER',
  'GRANT'
] as const;

/** 执行结果域取后端 `audit_query_service.RESULT_STATUSES`（Console 归一口径）：含策略拒绝。 */
const RESULT_STATUSES = ['SUCCESS', 'FAILED', 'DENIED'] as const;

function toIsoStart(value: Date | null | undefined): string | undefined {
  return value ? value.toISOString() : undefined;
}

/** dateRange 的结束日是当天 00:00；审计时间区间「起止含端点」须含结束日整天。 */
function toIsoEndOfDay(value: Date | null | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  const end = new Date(value);
  end.setHours(23, 59, 59, 999);
  return end.toISOString();
}

export function AuditFilterBar(props: AuditFilterBarProps) {
  const { t } = useTranslation();
  const { value } = props;

  /** 唯一上抛出口：任一筛选变更都把 page 重置为 1（设计 §3.4）。 */
  function emit(patch: Partial<AuditListQuery>): void {
    props.onChange({ ...patch, page: 1 });
  }

  const timeRange: [Date, Date] | undefined =
    value.startTime && value.endTime
      ? [new Date(value.startTime), new Date(value.endTime)]
      : undefined;

  return (
    <>
      <DatePicker
        data-testid="audit-filter-time"
        type="dateRange"
        style={{ width: 260 }}
        value={timeRange}
        placeholder={[t('audit.filter.timeFrom'), t('audit.filter.timeTo')]}
        onChange={(range) => {
          const days = Array.isArray(range) ? (range as Date[]) : [];
          emit({ startTime: toIsoStart(days[0]), endTime: toIsoEndOfDay(days[1]) });
        }}
      />
      <Select
        data-testid="audit-filter-auditType"
        style={{ width: 150 }}
        showClear
        placeholder={t('audit.filter.auditType')}
        value={value.auditType ?? undefined}
        optionList={AUDIT_TYPES.map((type) => ({
          value: type,
          label: t(`audit.auditType.${type}`)
        }))}
        onChange={(raw) =>
          emit({ auditType: (raw as AuditListQuery['auditType']) ?? undefined })
        }
      />
      <Input
        data-testid="audit-filter-actorUserId"
        style={{ width: 180 }}
        showClear
        placeholder={t('audit.filter.actorUserId')}
        value={value.actorUserId ?? ''}
        onChange={(text) => emit({ actorUserId: text || undefined })}
        onEnterPress={() => props.onSearch()}
      />
      <Select
        data-testid="audit-filter-agent"
        style={{ width: 150 }}
        showClear
        placeholder={t('audit.filter.agent')}
        value={value.resourceType ?? undefined}
        optionList={RESOURCE_TYPES.map((type) => ({
          value: type,
          label: t(`audit.resourceType.${type}`)
        }))}
        onChange={(raw) => emit({ resourceType: (raw as string) ?? undefined })}
      />
      <Input
        data-testid="audit-filter-resourceId"
        style={{ width: 180 }}
        showClear
        placeholder={t('audit.filter.resourceId')}
        value={value.resourceId ?? ''}
        onChange={(text) => emit({ resourceId: text || undefined })}
        onEnterPress={() => props.onSearch()}
      />
      <Input
        data-testid="audit-filter-action"
        style={{ width: 150 }}
        showClear
        placeholder={t('audit.filter.action')}
        value={value.action ?? ''}
        onChange={(text) => emit({ action: text || undefined })}
        onEnterPress={() => props.onSearch()}
      />
      <Select
        data-testid="audit-filter-resultStatus"
        style={{ width: 130 }}
        showClear
        placeholder={t('audit.filter.resultStatus')}
        value={value.resultStatus ?? undefined}
        optionList={RESULT_STATUSES.map((status) => ({
          value: status,
          label: t(`audit.resultStatus.${status}`)
        }))}
        onChange={(raw) => emit({ resultStatus: (raw as string) ?? undefined })}
      />
      <Input
        data-testid="audit-filter-traceId"
        style={{ width: 200 }}
        showClear
        placeholder={t('audit.filter.traceId')}
        value={value.traceId ?? ''}
        onChange={(text) => emit({ traceId: text || undefined })}
        onEnterPress={() => props.onSearch()}
      />
      <Button data-testid="audit-reset" onClick={() => props.onReset()}>
        {t('common.reset')}
      </Button>
      <Button data-testid="audit-refresh" onClick={() => props.onRefresh()}>
        {t('common.refresh')}
      </Button>
    </>
  );
}
