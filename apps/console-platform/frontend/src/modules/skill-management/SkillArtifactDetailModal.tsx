import { Modal, Spin, Tabs } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { ErrorState } from '../../components/common/ErrorState';
import { getArtifact, type SkillArtifactDetail } from './services/skills';

export interface SkillArtifactDetailModalProps {
  visible: boolean;
  skillId: string | null;
  artifactId: string | null;
  onCancel(): void;
}

export function SkillArtifactDetailModal(props: SkillArtifactDetailModalProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SkillArtifactDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!props.visible || !props.skillId || !props.artifactId) {
      setDetail(null);
      setFailed(false);
      return;
    }
    let cancelled = false;
    setDetail(null);
    setLoading(true);
    setFailed(false);
    getArtifact(props.skillId, props.artifactId)
      .then((value) => {
        if (!cancelled) {
          setDetail(value);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [props.visible, props.skillId, props.artifactId]);

  const files = detail?.manifest?.files ?? [];
  const skillMd = detail?.instructions ?? '';

  return (
    <Modal
      visible={props.visible}
      title={t('skill.artifact.title', { version: detail?.version ?? '' })}
      footer={null}
      width={720}
      onCancel={props.onCancel}
    >
      {failed ? (
        <ErrorState />
      ) : loading || detail === null ? (
        <Spin style={{ display: 'block', margin: '16px auto' }} />
      ) : (
        <Tabs type="line">
          <Tabs.TabPane itemKey="manifest" tab={t('skill.artifact.tabs.manifest')}>
            <DetailGrid
              items={[
                { label: t('skill.columns.currentVersion'), value: detail.version },
                { label: t('skill.artifact.checksum'), value: detail.checksum },
                { label: t('skill.artifact.storageKey'), value: detail.storage_key },
                { label: t('skill.columns.executionMode'), value: detail.execution_mode },
                { label: t('skill.artifact.validationStatus'), value: detail.validation_status },
                { label: t('skill.artifact.packageSize'), value: `${(detail.package_size / 1024).toFixed(1)} KiB` },
                { label: t('skill.artifact.createdAt'), value: <DateTimeText value={detail.create_time} /> }
              ]}
            />
            <h5>{t('skill.artifact.fileList')}</h5>
            <ul data-testid="artifact-file-list">
              {files.map((file) => (
                <li key={file.path}>
                  {file.path}（{(file.size / 1024).toFixed(1)} KiB）
                </li>
              ))}
            </ul>
          </Tabs.TabPane>
          <Tabs.TabPane itemKey="skillmd" tab="SKILL.md">
            {skillMd ? (
              <pre
                data-testid="artifact-skill-md"
                style={{ whiteSpace: 'pre-wrap', maxHeight: 360, overflow: 'auto' }}
              >
                {skillMd}
              </pre>
            ) : (
              <div className="detail-hint">{t('skill.artifact.skillMdEmpty')}</div>
            )}
          </Tabs.TabPane>
        </Tabs>
      )}
    </Modal>
  );
}
