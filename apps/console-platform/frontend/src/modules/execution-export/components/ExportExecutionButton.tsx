import { IconDownload } from '@douyinfe/semi-icons';
import { Button, Tooltip } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../../auth/AuthContext';
import { useExecutionExport } from '../hooks/useExecutionExport';
import type { ExecutionExportFormat, ExecutionKind } from '../services/executionExport';

export interface ExportExecutionButtonProps {
  kind: ExecutionKind;
  id: string | null;
  format?: ExecutionExportFormat;
  disabled?: boolean;
  /** API still enforces ADMIN; hiding here is UX only. */
  adminOnly?: boolean;
}

export function ExportExecutionButton(props: ExportExecutionButtonProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const { busy, start } = useExecutionExport(props.kind, props.id);

  if (!props.id || (props.adminOnly !== false && account?.role !== 'ADMIN')) {
    return null;
  }

  return (
    <Tooltip content={t('executionExport.tooltip')}>
      <Button
        data-testid="execution-export"
        aria-label={t('executionExport.ariaLabel')}
        icon={<IconDownload />}
        loading={busy}
        disabled={props.disabled || busy}
        onClick={() => {
          void start(props.format ?? 'ZIP');
        }}
      >
        {busy ? t('executionExport.busy') : t('executionExport.action')}
      </Button>
    </Tooltip>
  );
}
