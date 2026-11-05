/**
 * 统一轻提示（tips）：一次性动作反馈的唯一出口。
 *
 * 页面/表单/加载错误仍用 `Banner` / `ErrorState` 常驻展示；本模块只负责“做完一件事”的
 * 成功/失败/警告/信息反馈。位置、时长、宽度与去重口径只在这里定义，业务模块不再直接
 * import Semi `Toast`。
 */

import { Toast } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

export interface TipOptions {
  /** 显示时长（秒）；缺省用统一值。 */
  duration?: number;
  /**
   * 去重键：只在短窗口内抑制**同一键**的重复提示（如全局 API 拦截器与页面同时上报同一错误）。
   * 不传则每次调用都展示，动作反馈不会被误吞。
   */
  dedupeKey?: string;
}

export type TipKind = 'success' | 'error' | 'warning' | 'info';

const DEFAULT_TIP_DURATION_SEC = 3;
const DEDUPE_WINDOW_MS = 1500;
const TIP_BOTTOM_PX = 24;
const TIP_RIGHT_PX = 24;

const recent = new Map<string, number>();

function shouldEmit(dedupeKey: string | undefined): boolean {
  if (dedupeKey === undefined) return true;
  const now = Date.now();
  for (const [key, at] of recent) {
    if (now - at > DEDUPE_WINDOW_MS) recent.delete(key);
  }
  if (recent.has(dedupeKey)) return false;
  recent.set(dedupeKey, now);
  return true;
}

function show(
  kind: TipKind,
  content: ReactNode,
  options: TipOptions | undefined
): void {
  if (!shouldEmit(options?.dedupeKey)) return;
  const payload = {
    content,
    duration: options?.duration ?? DEFAULT_TIP_DURATION_SEC,
    bottom: TIP_BOTTOM_PX,
    right: TIP_RIGHT_PX
  };
  if (kind === 'success') Toast.success(payload);
  else if (kind === 'error') Toast.error(payload);
  else if (kind === 'warning') Toast.warning(payload);
  else Toast.info(payload);
}

/** 一次性动作反馈：右下角、自动消失、统一宽度与时长。 */
export const Tips = {
  success(content: ReactNode, options?: TipOptions): void {
    show('success', content, options);
  },
  error(content: ReactNode, options?: TipOptions): void {
    show('error', content, options);
  },
  warning(content: ReactNode, options?: TipOptions): void {
    show('warning', content, options);
  },
  info(content: ReactNode, options?: TipOptions): void {
    show('info', content, options);
  }
};

/** 测试辅助：清空去重窗口（生产代码不调用）。 */
export function resetTipsDedupe(): void {
  recent.clear();
}
