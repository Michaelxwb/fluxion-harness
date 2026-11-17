/**
 * 步骤明细视图提示条（设计 §3.6）：说明当前列表粒度，并给出返回操作视图的唯一出口。
 *
 * 明细视图是筛选的产物（审计类型 = 工具/模型调用），所以提示条不持有状态，只上抛回调。
 */

import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export interface AuditViewNoticeProps {
  /** 当前是否按某个 Trace 下钻；有值时额外给出清除入口。 */
  traceId?: string | null;
  onClearTrace?(): void;
  onBack(): void;
}

export function AuditViewNotice({
  traceId = null,
  onClearTrace,
  onBack
}: AuditViewNoticeProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      data-testid="audit-view-notice"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexWrap: 'wrap',
        padding: '8px 12px',
        borderRadius: 6,
        background: 'var(--semi-color-primary-light-default)',
        color: 'var(--semi-color-primary)',
        fontSize: 13
      }}
    >
      <strong>{t('audit.views.detailTitle')}</strong>
      <span>{t('audit.views.detailHint')}</span>
      {traceId ? (
        <>
          <span data-testid="audit-trace-filter" style={{ fontWeight: 500 }}>
            {t('audit.views.traceFiltered', { trace: traceId.slice(0, 8) })}
          </span>
          <Button
            theme="borderless"
            size="small"
            data-testid="audit-trace-clear"
            onClick={onClearTrace}
          >
            {t('audit.views.clearTrace')}
          </Button>
        </>
      ) : null}
      <Button
        theme="borderless"
        type="primary"
        size="small"
        data-testid="audit-view-back"
        onClick={onBack}
      >
        {t('audit.views.backToOperation')}
      </Button>
    </div>
  );
}
