import { Button } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Tips } from '../../components/common/Tips';
import { downloadArtifact, skillDownloadErrorCode } from './services/skills';

export interface SkillArtifactDownloadButtonProps {
  skillId: string;
  artifactId: string;
  version?: string;
  disabled?: boolean;
  onDownloaded?(checksum: string | null): void;
}

function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  // 部分浏览器在 click() 之后才真正读取 blob URL。
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadError(code: string | undefined, t: (key: string) => string): string {
  switch (code) {
    case 'COMMON_FORBIDDEN':
      return t('skill.artifact.downloadForbidden');
    case 'COMMON_NOT_FOUND':
      return t('skill.artifact.downloadNotFound');
    case 'SKILL_ARTIFACT_INTEGRITY_ERROR':
      return t('skill.artifact.integrityError');
    default:
      return t('skill.artifact.downloadFailed');
  }
}

/** 管理员下载 PENDING/APPROVED 包；校验和来自响应头，失败信息常驻可重试。 */
export function SkillArtifactDownloadButton(props: SkillArtifactDownloadButtonProps) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [checksum, setChecksum] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const download = async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const result = await downloadArtifact(props.skillId, props.artifactId);
      const name = result.fileName
        ?? (props.version ? `skill-${props.version}.zip` : `skill-${props.artifactId}.zip`);
      saveBlob(result.blob, name);
      setChecksum(result.checksum);
      Tips.success(t('skill.artifact.downloadSuccess'));
      props.onDownloaded?.(result.checksum);
    } catch (failure) {
      const code = await skillDownloadErrorCode(failure);
      setError(downloadError(code ?? undefined, t));
    } finally {
      setLoading(false);
    }
  };

  const baseLabel = props.version
    ? `${t('skill.artifact.download')} v${props.version}`
    : t('skill.artifact.download');
  const ariaLabel = props.disabled
    ? `${baseLabel} — ${t('skill.noPermission')}`
    : baseLabel;

  return (
    <span className="skill-download">
      <Button
        theme="borderless"
        type="primary"
        data-testid={`download-artifact-${props.artifactId}`}
        aria-label={ariaLabel}
        loading={loading}
        disabled={props.disabled}
        onClick={() => void download()}
      >
        {loading ? t('skill.artifact.downloading') : t('skill.artifact.download')}
      </Button>
      {checksum ? (
        <span className="detail-hint" data-testid="skill-download-checksum">
          {t('skill.artifact.checksum')}: {checksum}
        </span>
      ) : null}
      {error ? (
        <span className="skill-action-error" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
