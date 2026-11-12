/**
 * 运行计时时钟（design frontend §3.3 `RunActions` 接缝）。
 *
 * 展示组件是无状态单元、契约禁止自带定时器（`test_capacity_webchat_run_actions_ui.py`），
 * 所以"每秒走一次"只发生在这一层：有活跃 Run 时推进 `now`，Run 结束立刻停表。
 * 组件拿到的仍是一个纯毫秒数，只做投影，不持有时间事实。
 */

import { useEffect, useState } from 'react';

/** 毫秒时间戳；`active` 为 false 时不再推进（返回值停在最后一次刻度）。 */
export function useRunClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}
