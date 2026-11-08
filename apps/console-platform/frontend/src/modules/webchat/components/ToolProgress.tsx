/**
 * 工具执行进度（公开事件 `run.tool`）：只显示工具名与本地秒表。
 *
 * 长工具调用（实测一次整盘 `find` 跑了 106 秒）期间页面上只有「已连接」，会被误读成卡住。
 * 开始时刻取 **STARTED 事件的时间戳**（不是组件挂载时刻）：断流重连后计时仍从真实开始算。
 * 定时器只在本组件内（每秒重渲染这一行），不触发页面级状态更新。
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ActiveRunTool } from '../runState';

const TICK_MS = 1000;

export function ToolProgress(props: { tool: ActiveRunTool }) {
  const { t } = useTranslation();
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  const startedMs = props.tool.startedAt === null ? null : Date.parse(props.tool.startedAt);
  const elapsedSeconds =
    startedMs === null || Number.isNaN(startedMs)
      ? null
      : Math.max(0, Math.floor((nowMs - startedMs) / 1000));
  return (
    <span className="chat-run-tool" data-testid="chat-run-tool" data-tool-name={props.tool.name}>
      <span className="chat-run-tool-name">
        {t('chat.run.tool.executing', { name: props.tool.name })}
      </span>
      {elapsedSeconds === null || elapsedSeconds < 1 ? null : (
        <span className="chat-run-tool-elapsed">
          {t('chat.run.tool.elapsed', { seconds: elapsedSeconds })}
        </span>
      )}
    </span>
  );
}
