import { Button, Input, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import {
  headerIssueKey,
  type HeaderRow,
  type HeaderValidationIssue
} from './authPayload';

export interface HeaderRowsEditorProps {
  rows: HeaderRow[];
  issues: HeaderValidationIssue[];
  onChange(rows: HeaderRow[]): void;
}

const ROW_STYLE = {
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr) auto',
  gap: 8,
  alignItems: 'start'
} as const;

export function HeaderRowsEditor(props: HeaderRowsEditorProps) {
  const { t } = useTranslation();

  const update = (index: number, patch: Partial<HeaderRow>): void => {
    props.onChange(props.rows.map((row, current) => (
      current === index ? { ...row, ...patch } : row
    )));
  };

  const remove = (index: number): void => {
    props.onChange(props.rows.filter((_, current) => current !== index));
  };

  const add = (): void => {
    props.onChange([...props.rows, { name: '', value: '' }]);
  };

  return (
    <div>
      <div className="form-section-title">{t('mcp.form.headers')}</div>
      {props.rows.map((row, index) => {
        const rowIssues = props.issues.filter((issue) => issue.rowIndex === index);
        return (
          <div key={index} style={{ marginTop: 8 }}>
            <div style={ROW_STYLE}>
              <Input
                value={row.name}
                placeholder={t('mcp.form.headerName')}
                aria-label={t('mcp.form.headerName')}
                data-testid={`mcp-header-name-${index}`}
                onChange={(value) => update(index, { name: value })}
              />
              <Input
                value={row.value}
                placeholder={t('mcp.form.headerValue')}
                aria-label={t('mcp.form.headerValue')}
                data-testid={`mcp-header-value-${index}`}
                onChange={(value) => update(index, { value })}
              />
              <Button
                type="danger"
                theme="borderless"
                aria-label={t('mcp.form.removeHeader')}
                onClick={() => remove(index)}
              >
                {t('mcp.form.removeHeader')}
              </Button>
            </div>
            {rowIssues.map((issue, issueIndex) => (
              <Typography.Text
                key={`${issue.kind}-${issueIndex}`}
                type="danger"
                size="small"
                style={{ display: 'block' }}
              >
                {t(headerIssueKey(issue.kind))}
              </Typography.Text>
            ))}
          </div>
        );
      })}
      <Button
        theme="borderless"
        data-testid="mcp-add-header"
        onClick={add}
        style={{ marginTop: 8 }}
      >
        {t('mcp.form.addHeader')}
      </Button>
    </div>
  );
}
