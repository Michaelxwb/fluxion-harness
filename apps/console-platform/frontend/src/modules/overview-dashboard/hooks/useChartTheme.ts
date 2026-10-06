/**
 * ECharts 主题适配：图表在 canvas 里渲染，**吃不到 CSS 变量**，必须在渲染期把主题色解析成
 * 实际色值。这里读 `body` 的 computed style（亮/暗两套 `--semi-color-*`），并用 MutationObserver
 * 监听 `theme-mode` 属性——ThemeButton 切换主题时图表拿到新色值重渲染。
 */

import { useEffect, useState } from 'react';

export interface ChartColors {
  primary: string;
  success: string;
  warning: string;
  danger: string;
  /** 中性灰（未知状态/弱化系列） */
  muted: string;
  /** 轴标签文字 */
  text2: string;
  /** 轴线 */
  border: string;
  /** 分隔线 */
  fill: string;
}

export interface ChartTheme {
  colors: ChartColors;
  /** 主题代际：仅用于依赖数组触发 ECharts 重渲染 */
  revision: number;
}

function readTheme(revision: number): ChartTheme {
  const style = getComputedStyle(document.body);
  const read = (name: string): string => style.getPropertyValue(name).trim();
  return {
    revision,
    colors: {
      primary: read('--semi-color-primary'),
      success: read('--semi-color-success'),
      warning: read('--semi-color-warning'),
      danger: read('--semi-color-danger'),
      muted: read('--semi-color-text-3'),
      text2: read('--semi-color-text-2'),
      border: read('--semi-color-border'),
      fill: read('--semi-color-fill-1')
    }
  };
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(() => readTheme(0));

  useEffect(() => {
    let revision = 0;
    const observer = new MutationObserver(() => {
      revision += 1;
      setTheme(readTheme(revision));
    });
    observer.observe(document.body, { attributes: true, attributeFilter: ['theme-mode'] });
    return () => observer.disconnect();
  }, []);

  return theme;
}

/** 用户偏好减少动效时关闭图表动画（与全局 prefers-reduced-motion 口径一致）。 */
export function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
