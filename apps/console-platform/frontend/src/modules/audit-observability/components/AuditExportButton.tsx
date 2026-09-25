/**
 * 审计导出按钮（设计 §3.3.1「列表左主操作 · 导出」；S-08 / E-08 / E-09）。
 *
 * 工具栏左主操作位的唯一主操作：`Button theme="solid" type="primary"`，按当前筛选创建导出任务，
 * 提交中禁用并展示进度。状态机归 `hooks/useAuditExport`（幂等键、有界轮询、下载都在那里）；本组件
 * 只把 catalog 错误码映射成 i18n 文案（设计 §3.5：组件不硬编码文案），[E-08] 失败保留页面筛选并
 * 就地给出显式重试入口（[E-09] 重试 = 新一次提交 → 新 key），不自动重提、不展示未完成产物。
 */

import { Banner, Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { useAuditExport } from '../hooks/useAuditExport';
import type { AuditExportCreateRequest } from '../types';

export interface AuditExportButtonProps {
  /** 当前筛选条件（设计 §3.5：与列表筛选同源，不含分页）。 */
  filters: AuditExportCreateRequest['filters'];
}

/** 默认导出格式：设计 §3.3.1 只有单一「导出」按钮、未指定格式，取 CSV（表格软件可直接打开）。 */
export const EXPORT_FORMAT_DEFAULT: AuditExportCreateRequest['exportFormat'] = 'CSV';

/** catalog 错误码 → i18n key（E-08/E-09）；未登记的码降级为统一文案，不伪造领域文案。 */
const EXPORT_ERROR_KEYS: Record<string, string> = {
  IDEMPOTENCY_MISMATCH: 'audit.export.error.IDEMPOTENCY_MISMATCH',
  COMMON_INTERNAL_ERROR: 'audit.export.error.COMMON_INTERNAL_ERROR'
};

function exportErrorKey(errorCode: string): string {
  return EXPORT_ERROR_KEYS[errorCode] ?? 'audit.export.errorFallback';
}

export function AuditExportButton(props: AuditExportButtonProps) {
  const { t } = useTranslation();
  const { errorCode, busy, start, retry } = useAuditExport();

  return (
    <>
      <Button
        data-testid="audit-export"
        theme="solid"
        type="primary"
        loading={busy}
        disabled={busy}
        onClick={() => start({ exportFormat: EXPORT_FORMAT_DEFAULT, filters: props.filters })}
      >
        {busy ? t('audit.export.busy') : t('audit.export.action')}
      </Button>
      {errorCode ? (
        <div
          data-testid="audit-export-error"
          style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 420 }}
        >
          <Banner type="danger" closeIcon={null} description={t(exportErrorKey(errorCode))} />
          <Button
            theme="borderless"
            type="danger"
            data-testid="audit-export-retry"
            onClick={retry}
          >
            {t('common.retry')}
          </Button>
        </div>
      ) : null}
    </>
  );
}
