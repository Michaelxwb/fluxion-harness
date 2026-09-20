import type { StatusTagOption } from '../../components/common/StatusTag';

type Translate = (key: string) => string;

/**
 * 枚举 → StatusTag 选项的唯一映射处。
 * 列表列、筛选下拉、结果 Modal、详情都用这一份，避免同一枚举在多处各写一套口径。
 */
export function testStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return {
    UNTESTED: { color: 'grey', label: t('model.test.status.untested') },
    AVAILABLE: { color: 'green', label: t('model.test.status.available') },
    FAILED: { color: 'red', label: t('model.test.status.failed') }
  };
}

export function enabledOptions(t: Translate): Record<string, StatusTagOption> {
  return {
    true: { color: 'green', label: t('common.status.enabled') },
    false: { color: 'grey', label: t('common.status.disabled') }
  };
}

export function apiKeyOptions(t: Translate): Record<string, StatusTagOption> {
  return {
    true: { color: 'green', label: t('model.apiKeyConfigured') },
    false: { color: 'grey', label: t('model.apiKeyMissing') }
  };
}
