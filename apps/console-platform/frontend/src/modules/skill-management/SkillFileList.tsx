import { useTranslation } from 'react-i18next';

import type { SkillArtifactDetail } from './services/skills';

function sizeLabel(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

export function SkillFileList(props: { manifest: SkillArtifactDetail['manifest'] }) {
  const { t } = useTranslation();
  const files = props.manifest.files ?? [];
  const fileCount = props.manifest.file_count ?? files.length;
  return (
    <div data-testid="skill-review-file-list">
      <div className="detail-section-title">{t('skill.artifact.fileList')}</div>
      {files.length === 0 ? (
        <div className="detail-hint">{t('skill.review.fileCount', { count: fileCount })}</div>
      ) : (
        <ul className="detail-list">
          {files.map((file) => (
            <li key={file.path}>
              {file.path}（{sizeLabel(file.size)}）
            </li>
          ))}
        </ul>
      )}
      <div className="detail-hint">{t('skill.review.fileCount', { count: fileCount })}</div>
    </div>
  );
}
