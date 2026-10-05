/**
 * CMP-08：版本历史面板（前端设计 §3.3/§3.4，容器）。
 *
 * 唯一另一处触达服务层的位置（经 `useSettingsRevisions`）：历史分页读取 + 回滚二次确认。
 * 回滚是破坏性操作，用 Semi `Modal.confirm` 明确说明"会生成新版本、历史不删除"（§3.8）。
 * 回滚成功后调用 `onRestored` 让页面重新读取当前设置（服务端产生新版本，一切以重读为准）。
 */

import { Button, Modal, Skeleton, TabPane } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DetailSideSheet } from '../../../components/common/DetailSideSheet';
import { EmptyState } from '../../../components/common/EmptyState';
import { ErrorState } from '../../../components/common/ErrorState';
import { PaginationFooter } from '../../../components/common/PaginationFooter';
import { useSettingsRevisions } from '../hooks/useSettingsRevisions';
import { VersionRow } from './VersionRow';

export interface VersionHistoryPanelProps {
  visible: boolean;
  currentRevision: number;
  onClose(): void;
  onRestored(): void;
}

export function VersionHistoryPanel({
  visible,
  currentRevision,
  onClose,
  onRestored
}: VersionHistoryPanelProps) {
  const { t } = useTranslation();
  const { items, page, pageSize, total, loading, failed, restoring, reload, setPage, restore } =
    useSettingsRevisions();

  const handleRestore = (revision: number): void => {
    Modal.confirm({
      title: t('settings.history.restoreTitle'),
      content: t('settings.history.restoreConfirm', { revision }),
      okText: t('settings.history.restore'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        await restore(revision);
        onRestored();
      }
    });
  };

  let body: JSX.Element;
  if (loading && items.length === 0) {
    body = <Skeleton placeholder={<Skeleton.Paragraph rows={4} />} loading />;
  } else if (failed && items.length === 0) {
    body = <ErrorState description={t('common.loadFailed')} onRetry={reload} />;
  } else if (items.length === 0) {
    body = <EmptyState title={t('settings.history.empty')} />;
  } else {
    body = (
      <div className="settings-version-list" data-testid="settings-version-list">
        {items.map((item) => (
          <VersionRow
            key={item.revision}
            item={item}
            currentRevision={currentRevision}
            resting={restoring}
            onRestore={handleRestore}
          />
        ))}
        {failed ? (
          <Button theme="borderless" type="danger" size="small" onClick={reload}>
            {t('common.retry')}
          </Button>
        ) : null}
        <PaginationFooter
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={(nextPage) => setPage(nextPage, pageSize)}
        />
      </div>
    );
  }

  return (
    <DetailSideSheet visible={visible} title={t('settings.history.title')} onCancel={onClose}>
      <TabPane itemKey="history" tab={t('settings.history.title')}>
        {body}
      </TabPane>
    </DetailSideSheet>
  );
}
