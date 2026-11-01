import { Button, Input, SideSheet, Spin, Tag, Toast } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
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
    <SideSheet
      visible={props.family !== null}
      onCancel={props.onClose}
      title={props.family ?? t('learning.family.title')}
      width={860}
    >
      {props.family !== null && detail === null && <Spin />}
      {detail && (
        <div>
          <div className="detail-section-title">{t('learning.family.overview')}</div>
          <div>{t('learning.family.source')}: {detail.source}</div>
          <div>{t('learning.family.runs')}: {detail.run_count}{detail.run_count_capped ? '+' : ''}</div>
          {detail.paused && <Tag color="orange">{t('learning.family.paused')}</Tag>}
          <Input value={reason} placeholder={t('learning.detail.reason')} onChange={setReason} />
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
          {detail.reports.map((report) => (
            <div key={`${report.version}-${report.experience_user_id}`}>
              v{report.version} · {report.status} · {report.owner}
            </div>
          ))}
        </div>
      )}
    </SideSheet>
  );
}
