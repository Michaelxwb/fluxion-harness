/**
 * Run 详情（只读）。两个入口共用：运行审计「关联 Run」与后台任务详情「来源运行」。
 *
 * **只读、且只展示结构**：运行的用户输入原文与事件负载都不在投影里（后端 `GET /api/v1/runs/{id}`
 * 就不返回它们），所以这里也不可能渲染出来。Console 至今零暴露对话原文，要不要开这个口是产品
 * 决定，不该由「闭环一条 issue」顺带定——先不加易、后撤难。
 *
 * 轮廓已剔除流式增量（`message.delta`，一次运行可达数千行），超上限时后端置
 * `timeline_truncated`，页面**如实说明还有更多**，不假装完整。
 */

import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import { getRun, type RunDetail } from './services/runs';

const STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  CREATED: 'grey',
  RUNNING: 'blue',
  WAITING_INPUT: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

export interface RunDetailSideSheetProps {
  runId: string | null;
  onCancel(): void;
}

export function RunDetailSideSheet(props: RunDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!props.runId) {
      return;
    }
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const value = await getRun(props.runId);
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
  }, [props.runId]);

  useEffect(() => {
    setActiveTab('basic');
    if (props.runId) {
      void load();
    } else {
      setDetail(null);
      setFailed(false);
    }
  }, [props.runId, load]);

  const statusOptions = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(STATUS_COLORS).map(([status, color]) => [
          status,
          { color, label: t(`run.status.${status}`) }
        ])
      ),
    [t]
  );

  return (
    <DetailSideSheet
      visible={props.runId !== null}
      title={props.runId ?? ''}
      subtitle={detail ? `${t(`run.status.${detail.status}`)} · ${detail.trace_id}` : t('run.detail.basic')}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onCancel={props.onCancel}
      notice={failed ? <ErrorState onRetry={() => void load()} /> : undefined}
    >
      <Tabs.TabPane itemKey="basic" tab={t('run.detail.basic')}>
        {loading ? <Spin /> : null}
        {detail ? (
          <>
            <div className="detail-section-title">{t('run.detail.basic')}</div>
            <DetailGrid
              items={[
                {
                  label: t('run.columns.status'),
                  value: <StatusTag status={detail.status} options={statusOptions} />
                },
                { label: t('run.columns.agent'), value: detail.agent_name ?? detail.agent_id },
                { label: t('run.columns.actor'), value: detail.actor_name ?? detail.actor_user_id },
                { label: t('run.columns.trace'), value: detail.trace_id },
                {
                  label: t('run.columns.startTime'),
                  value: detail.start_time ? <DateTimeText value={detail.start_time} /> : '-'
                },
                {
                  label: t('run.columns.endTime'),
                  value: detail.end_time ? <DateTimeText value={detail.end_time} /> : '-'
                },
                {
                  fullWidth: true,
                  label: t('run.detail.error'),
                  value: (
                    <span data-testid="run-detail-error">
                      {detail.error_code ? `${detail.error_code}: ` : ''}
                      {detail.error_message ?? '-'}
                    </span>
                  )
                }
              ]}
            />
          </>
        ) : null}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="timeline" tab={t('run.detail.timeline')}>
        {detail ? (
          <div data-testid="run-detail-timeline">
            {detail.timeline.length === 0 ? (
              <EmptyState title={t('run.detail.timelineEmpty')} />
            ) : (
              <ul className="detail-list">
                {detail.timeline.map((event) => (
                  <li key={event.seq}>
                    <span className="mono">{event.event_type}</span>
                    {' · '}
                    <DateTimeText value={event.create_time} />
                    {event.has_artifact ? ` · ${t('run.detail.hasArtifact')}` : ''}
                  </li>
                ))}
              </ul>
            )}
            {detail.timeline_truncated ? (
              <p data-testid="run-detail-truncated">{t('run.detail.truncated')}</p>
            ) : null}
          </div>
        ) : null}
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
