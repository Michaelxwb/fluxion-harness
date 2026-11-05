import { Button, Input, Spin, Tabs, Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { Tips } from '../../components/common/Tips';
import {
  actorLabel,
  authorityLabel,
  channelLabel,
  familyLabel,
  improvementLabel,
  operatorLabel,
  ownerLabel,
  reportStatusLabel,
  scoreLabel,
  shortId,
  stateLabel,
  stateTone
} from './labels';
import { experienceAction, getExperience, replayExperience, reviseExperience } from './services/learning';
import type { ExperienceDetail } from './types';

function friendlyEvidenceLabel(t: (key: string, options?: Record<string, unknown>) => string, key: string): string {
  const mapping: Record<string, string> = {
    source_run_ids: 'learning.detail.evidenceSourceRuns',
    success_runs: 'learning.detail.evidenceSuccessRuns',
    item_total: 'learning.detail.evidenceItemTotal',
    trial_baseline_corrections: 'learning.detail.evidenceTrialBaseline'
  };
  return mapping[key] ? t(mapping[key]) : key;
}

export interface ExperienceDetailSideSheetProps {
  userId: string;
  memoryKey: string | null;
  onClose(): void;
  onMutated(): void;
}

export function ExperienceDetailSideSheet(props: ExperienceDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<ExperienceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [reason, setReason] = useState('');
  const [value, setValue] = useState('');
  const [activeTab, setActiveTab] = useState('overview');

  async function load(key: string) {
    setLoading(true);
    try {
      setDetail(await getExperience(props.userId, key));
    } catch {
      Tips.error(t('learning.errors.load'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (props.memoryKey === null) {
      setDetail(null);
      return;
    }
    void load(props.memoryKey);
  }, [props.userId, props.memoryKey]);

  async function act(action: 'activate' | 'retire' | 'regenerate' | 'replay') {
    if (props.memoryKey === null) return;
    if (action !== 'activate' && action !== 'replay' && !reason.trim()) {
      Tips.warning(t('learning.errors.reasonRequired'));
      return;
    }
    try {
      if (action === 'replay') {
        await replayExperience(props.userId, props.memoryKey, newRequestId(), {
          errorTip: 'never'
        });
      } else {
        await experienceAction(
          props.userId,
          props.memoryKey,
          action,
          reason || undefined,
          newRequestId(),
          { errorTip: 'never' }
        );
      }
      Tips.success(t('learning.actions.done'));
      setReason('');
      props.onMutated();
      await load(props.memoryKey);
    } catch {
      Tips.error(t('learning.errors.action'));
    }
  }

  async function revise() {
    if (detail === null || !reason.trim() || !value.trim()) {
      Tips.warning(t('learning.errors.reasonRequired'));
      return;
    }
    try {
      await reviseExperience(
        props.userId,
        detail.memory_key,
        value,
        detail.version,
        reason,
        newRequestId(),
        { errorTip: 'never' }
      );
      Tips.success(t('learning.actions.done'));
      setReason('');
      setValue('');
      props.onMutated();
      await load(detail.memory_key);
    } catch {
      Tips.error(t('learning.errors.action'));
    }
  }

  return (
    <DetailSideSheet
      visible={props.memoryKey !== null}
      onCancel={props.onClose}
      title={
        <>
          {t('learning.detail.title')}
          {detail ? (
            <Tag size="small" style={{ marginLeft: 8 }}>
              v{detail.version}
            </Tag>
          ) : null}
        </>
      }
      subtitle={
        detail
          ? `${familyLabel(t, detail.task_signature)} · ${stateLabel(t, detail.activation_state)}`
          : undefined
      }
      activeTab={activeTab}
      onTabChange={setActiveTab}
    >
      {loading && !detail ? <Spin /> : null}
      {detail ? (
        <>
          <Tabs.TabPane itemKey="overview" tab={t('learning.detail.overview')}>
            <div className="detail-section-title">{t('learning.detail.identity')}</div>
            <DetailGrid
              items={[
                {
                  label: t('learning.columns.key'),
                  value: <span title={detail.memory_key}>{shortId(detail.memory_key, 24)}</span>
                },
                { label: t('learning.columns.family'), value: familyLabel(t, detail.task_signature) },
                {
                  label: t('learning.columns.state'),
                  value: <Tag color={stateTone(detail.activation_state)}>{stateLabel(t, detail.activation_state)}</Tag>
                },
                { label: t('learning.columns.authority'), value: authorityLabel(t, detail.authority) },
                { label: t('learning.detail.version'), value: detail.version },
                { label: t('learning.detail.owner'), value: ownerLabel(t, detail.report_owner) },
                {
                  label: t('learning.columns.updated'),
                  value: <DateTimeText value={detail.update_time} />
                },
                {
                  label: t('learning.columns.content'),
                  value: String(detail.content?.value ?? '-'),
                  fullWidth: true
                }
              ]}
            />
            <div className="detail-section-title">{t('learning.detail.actions')}</div>
            <Typography.Text type="tertiary">
              {t('learning.detail.reasonLabel')}
            </Typography.Text>
            <Input
              value={reason}
              placeholder={t('learning.detail.reason')}
              onChange={setReason}
            />
            <div className="detail-actions" style={{ justifyContent: 'flex-start' }}>
              <Button onClick={() => act('replay')}>{t('learning.actions.replay')}</Button>
              <Button onClick={() => act('activate')}>{t('learning.actions.activate')}</Button>
              <Button onClick={() => act('retire')}>{t('learning.actions.retire')}</Button>
              <Button onClick={() => act('regenerate')}>{t('learning.actions.regenerate')}</Button>
            </div>
            <div className="detail-section-title">{t('learning.detail.reviseSection')}</div>
            <Input
              value={value}
              placeholder={t('learning.detail.newValue')}
              onChange={setValue}
            />
            <div className="detail-actions" style={{ justifyContent: 'flex-start' }}>
              <Button onClick={revise}>{t('learning.actions.revise')}</Button>
            </div>
          </Tabs.TabPane>
          <Tabs.TabPane itemKey="evidence" tab={t('learning.detail.evidence')}>
            <DetailGrid
              items={Object.entries(detail.evidence).map(([key, item]) => ({
                label: friendlyEvidenceLabel(t, key),
                value: Array.isArray(item)
                  ? item.map((value) => shortId(String(value))).join('、')
                  : typeof item === 'object'
                    ? JSON.stringify(item)
                    : String(item)
              }))}
            />
            <div className="detail-section-title">{t('learning.detail.history')}</div>
            <DataTable
              dataSource={detail.history}
              pagination={false}
              rowKey="version"
              columns={[
                { title: t('learning.detail.version'), dataIndex: 'version' },
                { title: t('learning.detail.reason'), dataIndex: 'change_reason' },
                { title: t('learning.detail.actor'), dataIndex: 'actor_type', render: (value: string) => actorLabel(t, value) },
                {
                  title: t('learning.columns.updated'),
                  dataIndex: 'create_time',
                  render: (item: string) => <DateTimeText value={item} />
                }
              ]}
            />
          </Tabs.TabPane>
          <Tabs.TabPane itemKey="reports" tab={t('learning.detail.reports')}>
            <DataTable
              dataSource={detail.reports}
              pagination={false}
              rowKey="version"
              columns={[
                { title: t('learning.detail.version'), dataIndex: 'version' },
                {
                  title: t('learning.columns.state'),
                  dataIndex: 'status',
                  render: (status: string) => <Tag>{status}</Tag>
                },
                { title: t('learning.detail.owner'), dataIndex: 'owner', render: (value: string) => ownerLabel(t, value) },
                { title: t('learning.detail.improvement'), dataIndex: 'improvement', render: (value: number | null) => improvementLabel(value) },
                {
                  title: t('learning.columns.updated'),
                  dataIndex: 'create_time',
                  render: (item: string) => <DateTimeText value={item} />
                }
              ]}
            />
          </Tabs.TabPane>
          <Tabs.TabPane itemKey="injections" tab={t('learning.detail.injections')}>
            <DataTable
              dataSource={detail.injections}
              pagination={false}
              rowKey="run_id"
              columns={[
                { title: t('learning.detail.run'), dataIndex: 'run_id', render: (value: string) => shortId(value) },
                { title: t('learning.detail.score'), dataIndex: 'score', render: (value: number | null) => scoreLabel(value) },
                { title: t('learning.detail.channel'), dataIndex: 'channel', render: (value: string | null) => channelLabel(t, value) },
                {
                  title: t('learning.columns.updated'),
                  dataIndex: 'create_time',
                  render: (item: string) => <DateTimeText value={item} />
                }
              ]}
            />
          </Tabs.TabPane>
          <Tabs.TabPane itemKey="audits" tab={t('learning.detail.audits')}>
            <DataTable
              dataSource={detail.audits}
              pagination={false}
              rowKey="action"
              columns={[
                { title: t('learning.detail.action'), dataIndex: 'action', render: (value: string) => t(`learning.enum.action.${value}`, { defaultValue: value }) },
                {
                title: t('learning.detail.operator'),
                dataIndex: 'operator_name',
                render: (_: unknown, row: Record<string, unknown>) => operatorLabel(t, row)
              },
                {
                  title: t('learning.columns.updated'),
                  dataIndex: 'create_time',
                  render: (item: string) => <DateTimeText value={item} />
                }
              ]}
            />
          </Tabs.TabPane>
        </>
      ) : null}
    </DetailSideSheet>
  );
}
