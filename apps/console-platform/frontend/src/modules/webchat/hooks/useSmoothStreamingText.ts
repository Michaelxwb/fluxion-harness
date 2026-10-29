/**
 * 流式正文的平滑吐字：上游 delta 可能是几十字一块，直接替换 DOM 会像“整段蹦出”。
 * 这里用固定节拍把目标文本逐段揭示，积压越多单步越大，避免长回复落后太多。
 *
 * 用 `setInterval` 而不是 `requestAnimationFrame`：后台标签页的 rAF 会被暂停，
 * 用户切回来时只剩最终消息；定时器即使被节流，回到前台仍会继续追赶。
 */

import { useEffect, useRef, useState } from 'react';

const TICK_MS = 33;
const CATCH_UP_STEPS = 20;

export function useSmoothStreamingText(target: string, runId: string): string {
  // SSR/静态渲染没有 effect/定时器：首屏直接给出目标正文，避免产物里出现空流式正文。
  // 真实浏览器有 matchMedia（悬浮/主题判定也依赖它），客户端才启用逐字平滑。
  const canAnimate =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function';
  const initial = canAnimate ? '' : target;
  const [visible, setVisible] = useState(initial);
  const visibleRef = useRef(initial);
  const targetRef = useRef(target);
  targetRef.current = target;

  useEffect(() => {
    visibleRef.current = '';
    setVisible('');
  }, [runId]);

  useEffect(() => {
    const timer = setInterval(() => {
      const nextTarget = targetRef.current;
      const current = visibleRef.current;
      if (current.length >= nextTarget.length) return;
      if (!nextTarget.startsWith(current)) {
        visibleRef.current = '';
        setVisible('');
        return;
      }
      const backlog = nextTarget.length - current.length;
      const step = Math.max(1, Math.ceil(backlog / CATCH_UP_STEPS));
      const next = nextTarget.slice(0, current.length + step);
      visibleRef.current = next;
      setVisible(next);
    }, TICK_MS);
    return () => clearInterval(timer);
  }, []);

  return visible;
}
