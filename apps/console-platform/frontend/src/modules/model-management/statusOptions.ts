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

/**
 * 物理 Key 检测结果 → StatusTag 选项。
 *
 * 与 `testStatusOptions`（逻辑模型的 `last_test_status`）刻意分开：检测结果多一个
 * `NOT_EXECUTED`（未申请到容量 = 明确未执行），且**不**回写逻辑模型状态，两套口径混用会
 * 把「这一次没探」显示成「模型不可用」。
 */
export function probeStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return {
    AVAILABLE: { color: 'green', label: t('model.credential.probe.status.AVAILABLE') },
    FAILED: { color: 'red', label: t('model.credential.probe.status.FAILED') },
    NOT_EXECUTED: { color: 'amber', label: t('model.credential.probe.status.NOT_EXECUTED') }
  };
}
