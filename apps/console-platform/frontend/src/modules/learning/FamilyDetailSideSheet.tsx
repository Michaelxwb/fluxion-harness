import { Button, Input, Spin, Tabs, Tag, Toast } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { ExperienceList } from './ExperienceList';
import { familyControl } from './services/learning';
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
    getFamily(props.family)
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
      title={props.family ?? t('learning.family.title')}
      subtitle={detail ? `${detail.source} · ${detail.window}` : undefined}
      activeTab={activeTab}
      onTabChange={setActiveTab}
    >
      {props.family !== null && detail === null && <Spin />}
      {detail && (
        <Tabs.TabPane itemKey="overview" tab={t('learning.family.overview')}>
          <div>
          <DetailGrid
            items={[
              { label: t('learning.family.source'), value: detail.source },
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
          <Input value={reason} placeholder={t('learning.detail.reason')} onChange={setReason} />
          <div className="detail-actions">
          <Button
            onClick={async () => {
              if (!reason.trim()) {
                Toast.warning(t('learning.errors.reasonRequired'));
                return;
              }
              try {
                await familyControl(detail.family, !detail.paused, reason, newRequestId());
                setDetail(await getFamily(detail.family));
                setReason('');
              } catch {
                Toast.error(t('learning.errors.action'));
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
              { title: t('learning.columns.state'), dataIndex: 'status' },
              { title: t('learning.detail.owner'), dataIndex: 'owner' },
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
