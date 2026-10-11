import { Button, Toast, Tooltip } from '@douyinfe/semi-ui';
import { IconCopy } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const { t } = useTranslation();
  const copy = async (): Promise<void> => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(value);
      Toast.success(t('common.copySuccess'));
    } catch {
      Toast.error(t('common.copyFailed'));
    }
  };
  const title = label ?? t('common.copy');
  return (
    <Tooltip content={title}>
      <Button
        className="copy-button"
        theme="borderless"
        size="small"
        icon={<IconCopy />}
        aria-label={title}
        onClick={(event) => {
          event.stopPropagation();
          void copy();
        }}
      />
    </Tooltip>
  );
}
