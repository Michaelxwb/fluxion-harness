/**
 * 审计列表筛选栏（设计 §3.3 CMP-04 / §3.3.1）。
 *
 * 受控展示组件：`value` 由 `AuditPage` 持有，本组件只上抛筛选补丁，不直接调 service。
 * 所有变更统一经 `emit` 出口，把 `page` 重置为 1（设计 §3.4）；`undefined` 表示清空该筛选项
 * （service 层会丢弃未设置项，不会污染查询串）。后端 snake_case 只出现在 service 层。
 *
 * 关键字文本是**例外**：输入期间只改本地草稿（`keywordDraft`），**回车**才经 `emit` 提交 ——
 * 否则每敲一键都会触发一次查询。下拉/日期保持选中即生效。
 */

import { useEffect, useState } from 'react';
import { DatePicker, Input, Select } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { ListActionButton } from '../../../components/common/ListActionButton';
import type { AuditListQuery } from '../types';

export interface AuditFilterBarProps {
  value: AuditListQuery;
  onChange(patch: Partial<AuditListQuery>): void;
  /** 清空全部筛选并回到第 1 页（设计 §3.3.1「重置」）。 */
  onReset(): void;
  /** 按当前筛选重取当前页（设计 §3.3.1「刷新」）。 */
  onRefresh(): void;
}

const AUDIT_TYPES = ['CONFIG', 'TOOL', 'EGRESS', 'MODEL'] as const;

/**
 * 资源类型取值域：取后端投影列的资源类型**实际取值**，大小写原样保留。
 *
 * 配置侧来自 `muad_console_platform` 各 AppService 写入器（`AUDIT_*` 常量与内联字面量，后者含
 * `project_platform`/`user_credential_ref`/`shared_credential_ref` 三个小写形态）；运行侧为投影 SQL
 * 的字面量（`TOOL`/`MODEL`）与 egress 落库的目标类型（`MCP`）。
 *
 * 末三条是设计文档 v1.5 登记、但后端写入器从未产出的历史写法（实际产出的是
 * `project_platform`/`PLATFORM_USER`/`AGENT_ACCESS_GRANT`）；它们只作登记域存在，供详情行按词条
 * 呈现历史数据，不新增后端取值。值域开放，未登记的取值由详情行原样展示。
 */
export const RESOURCE_TYPES = [
  'AGENT',
  'AGENT_ACCESS_GRANT',
  'AGENT_SKILL_BINDING',
  'BIND_CODE',
  'CHANNEL_IDENTITY',
  'MCP',
  'MCP_SERVER',
  'MODEL',
  'PLATFORM_USER',
  'SKILL',
  'SKILL_ARTIFACT',
  'SKILL_USER_GRANT',
  'TOOL',
  'USER_MEMORY',
  'project_platform',
  'shared_credential_ref',
  'user_credential_ref',
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

  /** 关键字草稿：输入期间不查询，回车才经 `emit` 提交。 */
  const [keywordDraft, setKeywordDraft] = useState(value.keyword ?? '');

  // 仅在「已生效的 keyword」变化时回填草稿（回车提交、重置清空）。
  // 依赖只能写 value.keyword：写成 value 会让选任意下拉都冲掉正在输入的文本。
  useEffect(() => {
    setKeywordDraft(value.keyword ?? '');
  }, [value.keyword]);

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
      <Input
        data-testid="audit-filter-keyword"
        style={{ width: 340, maxWidth: '100%' }}
        maxLength={128}
        showClear
        aria-label={t('audit.filter.keyword')}
        placeholder={t('audit.filter.keyword')}
        value={keywordDraft}
        onChange={setKeywordDraft}
        onEnterPress={() => emit({ keyword: keywordDraft || undefined })}
      />
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
      <Select
        data-testid="audit-filter-resourceType"
        style={{ width: 180 }}
        showClear
        placeholder={t('audit.filter.resourceType')}
        value={value.resourceType ?? undefined}
        optionList={RESOURCE_TYPES.map((type) => ({
          value: type,
          label: t(`audit.resourceType.${type}`)
        }))}
        onChange={(raw) => emit({ resourceType: (raw as string) ?? undefined })}
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
      <ListActionButton data-testid="audit-reset" onClick={() => props.onReset()} action="reset" />
      <ListActionButton data-testid="audit-refresh" onClick={() => props.onRefresh()} action="refresh" />
    </>
  );
}
