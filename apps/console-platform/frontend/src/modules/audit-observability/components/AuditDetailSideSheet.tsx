/**
 * 审计只读详情 SideSheet（设计 §3.3 CMP-03、§3.4、§3.6；场景 S-07 / E-07）。
 *
 * 壳层全部复用公共 `DetailSideSheet`：标题/副标题居左，关闭 X 由 Semi Header 与标题同行靠右，
 * Tabs 在其下——设计 §3.3 的 `DetailTabs` 即由该公共组件内建 `Tabs` 的 children（`Tabs.TabPane`）
 * 承载，仓库无独立 DetailTabs 模块，故本组件不自造 Header/关闭按钮、也不传 `actions`（只读，
 * S-07「无操作按钮」）。详情状态机归 `hooks/useAuditDetail`（只经 TASK-010 的 service 层），
 * 文案一律 i18n key。
 *
 * 内容分区：交互稿「基本信息」八列 + 按 `auditType` 渲染来源表独有字段（CONFIG before/after、
 * TOOL argsPreview、EGRESS/MODEL 各自字段），关联区展示 Run/Task 链接。
 * [E-07] 后端 `relatedMissing` 为真时关联区渲染 `ErrorState`，绝不回退编造关联数据；审计自身
 * 字段与来源字段照常展示。
 */

import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import type { TFunction } from 'i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { DetailGrid, type DetailGridItem } from '../../../components/common/DetailGrid';
import { DetailSideSheet } from '../../../components/common/DetailSideSheet';
import { EmptyState } from '../../../components/common/EmptyState';
import { EntityLink } from '../../../components/common/EntityLink';
import { ErrorState } from '../../../components/common/ErrorState';
import { StatusTag, type StatusTagOption } from '../../../components/common/StatusTag';
import { useAuditDetail } from '../hooks/useAuditDetail';
import type { AuditDetail, AuditListItem } from '../types';

/** 详情 SideSheet 入参（设计 §3.4）：与页面导出的 `AuditDetailSideSheetProps` 同结构（保持单向 import）。 */
export interface AuditDetailSideSheetProps {
  visible: boolean;
  auditType: AuditListItem['auditType'];
  auditId: string | null;
  onClose(): void;
}

/** 可跳转的关联实体（S-07）：Run/Task 记录在 Console 统一由任务列表承载。 */
type AuditRelation = 'run' | 'task';

/** 各审计类型独有字段（后端 `_detail_extras` 的 camelCase 键），顺序即设计 §3.4 口径。 */
const TYPE_FIELDS: Record<AuditListItem['auditType'], readonly string[]> = {
  CONFIG: ['before', 'after', 'sourceIp'],
  TOOL: ['argsPreview', 'toolCallId', 'toolKind', 'preparedArgsHash', 'errorCode'],
  EGRESS: [
    'targetType',
    'adapterKey',
    'platformId',
    'method',
    'policyDecision',
    'statusCode',
    'errorCode'
  ],
  MODEL: [
    'provider',
    'model',
    'attempt',
    'retryReason',
    'inputTokens',
    'outputTokens',
    'errorCode'
  ]
};

/** 结果标签配色：域同后端 `audit_query_service.RESULT_STATUSES`（SUCCESS/FAILED/DENIED）。 */
const RESULT_COLORS: Record<string, StatusTagOption['color']> = {
  SUCCESS: 'green',
  FAILED: 'red',
  DENIED: 'red'
};

/** 空值统一显示 `-`：缺字段不编造内容。 */
function text(value: unknown): string {
  return value === null || value === undefined || value === '' ? '-' : String(value);
}

/** 对象/数组型来源字段（before/after/argsPreview）按 JSON 渲染，其余走 `text`。 */
function formatExtra(field: string, value: unknown): ReactNode {
  if (typeof value === 'object' && value !== null) {
    return (
      <pre
        data-testid={`audit-detail-${field}`}
        style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}
      >
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return text(value);
}

/** 交互稿「基本信息」八列 + 审计自身标识字段（设计 §3.4 类型契约的全部字段）。 */
function buildBasicItems(detail: AuditDetail, t: TFunction): DetailGridItem[] {
  const statusOptions = Object.fromEntries(
    Object.entries(RESULT_COLORS).map(([status, color]) => [
      status,
      { color, label: t(`audit.resultStatus.${status}`) }
    ])
  );
  return [
    { label: t('audit.columns.time'), value: <DateTimeText value={detail.occurredAt} /> },
    { label: t('audit.columns.auditType'), value: t(`audit.auditType.${detail.auditType}`) },
    { label: t('audit.columns.actor'), value: detail.actorName ?? detail.actorUserId },
    { label: t('audit.columns.agent'), value: text(detail.agentName ?? detail.agentId) },
    { label: t('audit.columns.target'), value: text(detail.target) },
    { label: t('audit.columns.action'), value: text(detail.action) },
    {
      label: t('audit.columns.result'),
      value: <StatusTag status={detail.resultStatus} options={statusOptions} />
    },
    { label: t('audit.columns.traceId'), value: text(detail.traceId) },
    { label: t('audit.detail.field.auditId'), value: detail.auditId },
    { label: t('audit.detail.field.resourceType'), value: text(detail.resourceType) },
    { label: t('audit.detail.field.resourceId'), value: text(detail.resourceId) },
    { label: t('audit.detail.field.latencyMs'), value: text(detail.latencyMs) }
  ];
}

/** 来源表独有字段：按 `auditType` 取 `detail.extras`，缺的字段整行不渲染（不补默认值）。 */
function buildTypeItems(detail: AuditDetail, t: TFunction): DetailGridItem[] {
  return TYPE_FIELDS[detail.auditType]
    .filter((field) => detail.extras[field] !== undefined && detail.extras[field] !== null)
    .map((field) => ({
      label: t(`audit.detail.field.${field}`),
      value: formatExtra(field, detail.extras[field])
    }));
}

/** 基础信息页签：审计自身字段与来源字段（与关联可读性无关，[E-07] 下照常展示）。 */
function renderBasicTab(detail: AuditDetail | null, loading: boolean, t: TFunction): ReactNode {
  if (detail === null) {
    return loading ? <Spin /> : null;
  }
  return (
    <>
      <div className="detail-section-title">{t('audit.detail.section.basic')}</div>
      <DetailGrid items={buildBasicItems(detail, t)} />
      <div className="detail-section-title">{t('audit.detail.section.source')}</div>
      <DetailGrid items={buildTypeItems(detail, t)} />
    </>
  );
}

/** 关联区（[E-07]）：`relatedMissing` 为真即渲染 ErrorState 并返回——不落链接、不编造关联 id。 */
function buildRelationSection(
  detail: AuditDetail,
  t: TFunction,
  onOpenRelated: (relation: AuditRelation, id: string) => void
): ReactNode {
  if (detail.relatedMissing) {
    return <ErrorState description={t('audit.detail.relatedMissing')} />;
  }
  const items: DetailGridItem[] = [];
  const runId = detail.related.runId;
  const taskId = detail.related.taskId;
  if (runId) {
    items.push({
      label: t('audit.detail.related.run'),
      value: (
        <EntityLink testId="audit-related-run" onClick={() => onOpenRelated('run', runId)}>
          {runId}
        </EntityLink>
      )
    });
  }
  if (taskId) {
    items.push({
      label: t('audit.detail.related.task'),
      value: (
        <EntityLink testId="audit-related-task" onClick={() => onOpenRelated('task', taskId)}>
          {taskId}
        </EntityLink>
      )
    });
  }
  if (items.length === 0) {
    return <EmptyState title={t('audit.detail.noRelated')} />;
  }
  return <DetailGrid items={items} />;
}

export function AuditDetailSideSheet(props: AuditDetailSideSheetProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('basic');
  const { detail, loading, failed, reload } = useAuditDetail(props.auditType, props.auditId);

  /** 关联跳转（S-07）：Console 无独立 Run 页面，Run/Task 关联统一落到任务列表并携带 id。 */
  const handleOpenRelated = useCallback(
    (relation: AuditRelation, id: string) => {
      navigate(`/tasks?${relation}Id=${id}`);
    },
    [navigate]
  );

  if (props.auditId === null) {
    return null;
  }

  return (
    <DetailSideSheet
      visible={props.visible}
      title={t('nav.audit')}
      subtitle={
        detail
          ? `${t(`audit.auditType.${detail.auditType}`)} · ${detail.auditId}`
          : props.auditId
      }
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onClose}
      notice={failed ? <ErrorState onRetry={() => void reload()} /> : undefined}
    >
      <Tabs.TabPane itemKey="basic" tab={t('audit.detail.tab.basic')}>
        {renderBasicTab(detail, loading, t)}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="relations" tab={t('audit.detail.tab.relations')}>
        {detail ? buildRelationSection(detail, t, handleOpenRelated) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
