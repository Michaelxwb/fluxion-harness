import { Button, Tooltip } from '@douyinfe/semi-ui';
import { IconCopy } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

import { Tips } from './Tips';

function copyWithoutClipboardApi(value: string): void {
  const previousFocus = document.activeElement;
  const input = document.createElement('textarea');
  input.value = value;
  input.setAttribute('readonly', '');
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  try {
    input.focus();
    input.select();
    if (!document.execCommand('copy')) throw new Error('copy command failed');
  } finally {
    input.remove();
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }
}

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const { t } = useTranslation();
  const copy = async (): Promise<void> => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        copyWithoutClipboardApi(value);
      }
      Tips.success(t('common.copySuccess'));
    } catch {
      Tips.error(t('common.copyFailed'));
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
