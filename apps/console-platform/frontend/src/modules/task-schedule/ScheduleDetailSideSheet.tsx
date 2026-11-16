import { Spin, Tabs, Tag, Typography } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { ErrorState } from '../../components/common/ErrorState';
import { StatusTag } from '../../components/common/StatusTag';
import {
  budgetSummary,
  concurrencyPolicyLabel,
  deliveryIntentLabel,
  endReasonLabel,
  fireStatusLabel,
  memoryModeLabel,
  scheduleModeLabel,
  scheduleStatusFallback,
  scheduleStatusOptions,
  type Translate
} from './scheduleCatalog';
import { ScheduleHistoryTable } from './ScheduleHistoryTable';
import { getSchedule, type ScheduleDetail } from './services/schedules';

export interface ScheduleDetailSideSheetProps {
  scheduleId: string | null;
  onCancel(): void;
  reloadKey?: number;
  onOpenTask?(taskId: string): void;
  /** ESC 关闭本面板；由页面只在它是当前最上层面板时开启。 */
  closeOnEsc?: boolean;
}

/**
 * Schedule 详情（design §3.10）：`status` 为主标签、`end_reason` 为次级标签；配置事实
 * （mode/timezone/Revision/config_version/依赖/投递意图/预算/记忆只读）全部来自后端投影。
 */
export function ScheduleDetailSideSheet(props: ScheduleDetailSideSheetProps) {
  const { t } = useTranslation();
  const translate: Translate = (key) => t(key);
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ScheduleDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!props.scheduleId) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const value = await getSchedule(props.scheduleId);
      if (current === requestSeq.current) {
        setDetail(value);
      }
    } catch {
      if (current === requestSeq.current) {
        setDetail(null);
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [props.scheduleId]);

  useEffect(() => {
    setActiveTab('basic');
    if (props.scheduleId) {
      void load();
    } else {
      setDetail(null);
      setFailed(false);
    }
  }, [props.scheduleId, props.reloadKey, load]);

  const statusOptions = scheduleStatusOptions(translate);

  return (
    <DetailSideSheet
      visible={props.scheduleId !== null}
      title={detail?.name ?? props.scheduleId ?? ''}
      subtitle={
        detail
          ? `${scheduleStatusFallback(translate, detail.status).label} · ${scheduleModeLabel(
              translate,
              detail.mode
            )}`
          : t('schedule.detail.basic')
      }
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      notice={failed ? <ErrorState onRetry={() => void load()} /> : undefined}
      closeOnEsc={props.closeOnEsc}
    >
      <Tabs.TabPane itemKey="basic" tab={t('schedule.detail.basic')}>
        {loading ? <Spin /> : null}
        {detail ? (
          <>
            <div className="detail-section-title">{t('schedule.detail.basic')}</div>
            <DetailGrid
              items={[
                { label: t('schedule.columns.name'), value: detail.name },
                {
                  label: t('schedule.columns.status'),
                  value: (
                    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                      <StatusTag
                        status={detail.status}
                        options={statusOptions}
                        fallback={scheduleStatusFallback(translate, detail.status)}
                      />
                      {detail.end_reason ? (
                        <Tag data-testid="schedule-detail-end-reason" color="grey">
                          {endReasonLabel(translate, detail.end_reason)}
                        </Tag>
                      ) : null}
                    </span>
                  )
                },
                { label: t('schedule.columns.mode'), value: scheduleModeLabel(translate, detail.mode) },
                { label: t('schedule.columns.timezone'), value: detail.timezone },
                { label: t('schedule.detail.revision'), value: detail.revision_no },
                { label: t('schedule.detail.configVersion'), value: detail.config_version },
                {
                  label: t('schedule.columns.nextFireAt'),
                  value: (
                    <span data-testid="schedule-detail-next-fire">
                      {detail.next_fire_at ? <DateTimeText value={detail.next_fire_at} /> : '-'}
                    </span>
                  )
                },
                {
                  label: t('schedule.columns.lastFireAt'),
                  value: detail.last_fire ? (
                    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                      <DateTimeText value={detail.last_fire.scheduled_at} />
                      <StatusTag
                        status={detail.last_fire.status}
                        options={{
                          [detail.last_fire.status]: {
                            color: 'grey',
                            label: fireStatusLabel(translate, detail.last_fire.status)
                          }
                        }}
                      />
                    </span>
                  ) : (
                    '-'
                  )
                },
                {
                  label: t('schedule.detail.attentionReason'),
                  value: (
                    <span data-testid="schedule-detail-attention">
                      {detail.attention_reason ?? '-'}
                    </span>
                  )
                },
                {
                  label: t('schedule.detail.dependencies'),
                  value: detail.dependencies.length > 0 ? detail.dependencies.join(', ') : '-'
                },
                {
                  label: t('schedule.detail.deliveryIntent'),
                  value: deliveryIntentLabel(translate, detail.delivery_intent)
                },
                {
                  label: t('schedule.detail.concurrency'),
                  value: concurrencyPolicyLabel(translate, detail.policy.concurrency_policy)
                },
                {
                  label: t('schedule.detail.budget'),
                  value: budgetSummary(detail.budget)
                },
                {
                  label: t('schedule.detail.memory'),
                  value: memoryModeLabel(translate, detail.memory_mode)
                },
                {
                  fullWidth: true,
                  label: t('schedule.detail.instruction'),
                  value: <span data-testid="schedule-detail-instruction">{detail.instruction}</span>
                }
              ]}
            />
          </>
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="history" tab={t('schedule.detail.history')}>
        {detail && activeTab === 'history' ? (
          <>
            {/* 深链与审计「关联」同一形状：跨页定位统一落到任务列表并携带 id 查询参数
                （见 `AuditDetailSideSheet`），任务列表据此预置按 schedule 的筛选。 */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
              <Typography.Text
                link
                data-testid="schedule-history-view-all"
                onClick={() => navigate(`/tasks?scheduleId=${detail.schedule_id}`)}
              >
                {t('schedule.detail.historyViewAll')}
              </Typography.Text>
            </div>
            <ScheduleHistoryTable
              scheduleId={detail.schedule_id}
              onOpenTask={(taskId) => props.onOpenTask?.(taskId)}
            />
          </>
        ) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
