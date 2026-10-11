import { useTranslation } from 'react-i18next';

import { SkillMarkdownViewer } from './SkillMarkdownViewer';

export function SkillContent({ description, source }: { description: string; source: string }) {
  const { t } = useTranslation();
  return (
    <section className="skill-content" data-testid="skill-content" aria-label={t('skill.content.title')}>
      <div className="detail-section-title">{t('skill.content.title')}</div>
      {description ? (
        <div className="skill-content-description">
          <div className="detail-hint">{t('skill.detail.description')}</div>
          <p>{description}</p>
        </div>
      ) : null}
      <SkillMarkdownViewer source={source} testId="skill-md-preview" />
    </section>
  );
}
