import { MarkdownRender, RadioGroup } from '@douyinfe/semi-ui';
import { useState, type ComponentPropsWithoutRef } from 'react';
import { useTranslation } from 'react-i18next';

import { CopyButton } from '../../components/common/CopyButton';

function MarkdownLink({ href, children, title }: ComponentPropsWithoutRef<'a'>) {
  let url: URL | undefined;
  try {
    url = href ? new URL(href, window.location.origin) : undefined;
  } catch {
    return <span>{children}</span>;
  }
  if (!url || !['http:', 'https:', 'mailto:'].includes(url.protocol)) return <span>{children}</span>;
  return <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>;
}

const markdownComponents = {
  a: MarkdownLink,
  code: (props: ComponentPropsWithoutRef<'code'>) => <code {...props} />,
  table: (props: ComponentPropsWithoutRef<'table'>) => <table {...props} />
};

export function SkillMarkdownViewer({ source, testId }: { source: string; testId: string }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'preview' | 'source'>('preview');
  return (
    <div className="skill-markdown-viewer" data-testid={testId}>
      <div className="skill-content-toolbar">
        <div className="detail-section-title">{t('skill.detail.skillMd')}</div>
        {source ? (
          <div className="skill-content-controls">
            <RadioGroup type="button" buttonSize="small" value={mode}
              aria-label={t('skill.content.mode')}
              options={[
                { label: t('skill.content.preview'), value: 'preview' },
                { label: t('skill.content.source'), value: 'source' }
              ]}
              onChange={(event) => setMode(event.target.value === 'source' ? 'source' : 'preview')}
            />
            <CopyButton value={source} label={t('skill.content.copy')} />
          </div>
        ) : null}
      </div>
      {!source ? <div className="detail-hint">{t('skill.artifact.skillMdEmpty')}</div>
        : mode === 'source' ? <pre className="skill-markdown-source">{source}</pre>
          : <div className="skill-markdown-preview">
            <MarkdownRender raw={source} format="md" remarkGfm components={markdownComponents} />
          </div>}
    </div>
  );
}
