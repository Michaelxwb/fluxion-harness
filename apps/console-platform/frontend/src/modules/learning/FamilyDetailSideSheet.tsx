import { Button, Input, Spin, Tabs, Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { Tips } from '../../components/common/Tips';
import { ExperienceList } from './ExperienceList';
import { familyControl } from './services/learning';
import { familyLabel, ownerLabel, reportStatusLabel } from './labels';
import { getFamily } from './services/learning';
import type { ExperienceItem, FamilyDetail } from './types';

export interface FamilyDetailSideSheetProps {
  family: string | null;
  onClose(): void;
  onOpenExperience(userId: string, key: string): void;
}

export function FamilyDetailSideSheet(props: FamilyDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<FamilyDetail | null>(null);
  const [reason, setReason] = useState('');
  const [activeTab, setActiveTab] = useState('overview');

  useEffect(() => {
    let cancelled = false;
    if (props.family === null) {
      setDetail(null);
      return;
    }
    getFamily(props.family, { errorTip: 'always' })
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [props.family]);

  return (
    <DetailSideSheet
      visible={props.family !== null}
      onCancel={props.onClose}
      title={familyLabel(t, props.family)}
      subtitle={detail ? `${detail.source} · ${detail.window}` : undefined}
      activeTab={activeTab}
      onTabChange={setActiveTab}
    >
      {props.family !== null && detail === null && <Spin />}
      {detail && (
        <Tabs.TabPane itemKey="overview" tab={t('learning.family.overview')}>
          <div className="detail-section-title">{t('learning.family.overview')}</div>
          <div>
          <DetailGrid
            items={[
              {
                label: t('learning.family.source'),
                value:
                  detail.source === 'skill'
                    ? t('learning.enum.family.sourceSkill')
                    : detail.source === 'agent'
                      ? t('learning.enum.family.sourceAgent')
                      : t('learning.enum.family.sourceOther')
              },
              {
                label: t('learning.family.runs'),
                value: `${detail.run_count}${detail.run_count_capped ? '+' : ''}`
              },
              { label: t('learning.family.window'), value: detail.window },
              {
                label: t('learning.family.paused'),
                value: detail.paused ? (
                  <Tag color="orange">{t('learning.family.paused')}</Tag>
                ) : (
                  t('learning.family.running')
                )
              }
            ]}
          />
          <Typography.Text type="tertiary">
            {t('learning.detail.reasonLabel')}
          </Typography.Text>
          <Input value={reason} placeholder={t('learning.detail.reason')} onChange={setReason} />
          <div className="detail-actions" style={{ justifyContent: 'flex-start' }}>
          <Button
            onClick={async () => {
              if (!reason.trim()) {
                Tips.warning(t('learning.errors.reasonRequired'));
                return;
              }
              try {
                await familyControl(detail.family, !detail.paused, reason, newRequestId(), {
                  errorTip: 'never'
                });
                setDetail(await getFamily(detail.family, { errorTip: 'always' }));
                setReason('');
              } catch {
                Tips.error(t('learning.errors.action'));
              }
            }}
          >
            {detail.paused ? t('learning.actions.resume') : t('learning.actions.pause')}
          </Button>
          </div>
          <div className="detail-section-title">{t('learning.family.experiences')}</div>
          {detail.experiences.map((group) => (
            <div key={group.user_id}>
              <div>{group.user_id}</div>
              <ExperienceList
                items={group.items}
                onOpen={(item: ExperienceItem) =>
                  props.onOpenExperience(item.user_id, item.memory_key)
                }
              />
            </div>
          ))}
          <div className="detail-section-title">{t('learning.family.reports')}</div>
          <DataTable
            dataSource={detail.reports}
            pagination={false}
            rowKey="version"
            columns={[
              { title: t('learning.detail.version'), dataIndex: 'version' },
              {
                title: t('learning.columns.reportStatus'),
                dataIndex: 'status',
                render: (value: string) => reportStatusLabel(t, value)
              },
              {
                title: t('learning.detail.owner'),
                dataIndex: 'owner',
                render: (value: string) => ownerLabel(t, value)
              },
              {
                title: t('learning.columns.updated'),
                dataIndex: 'create_time',
                render: (item: string) => <DateTimeText value={item} />
              }
            ]}
          />
          </div>
        </Tabs.TabPane>
      )}
    </DetailSideSheet>
  );
}
