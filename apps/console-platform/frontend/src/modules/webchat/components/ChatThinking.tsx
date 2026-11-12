/**
 * 思考占位（design frontend §3.3 消息区接缝）。
 *
 * 只在「Run 已受理、首个正文增量未到」这段时间渲染：这时消息区里没有任何内容，用户既看不到
 * 回复也看不到进度，容易以为请求丢了。占位给出两件事——"正在思考"与已等待时长。
 *
 * **性能约束**：每秒走秒的定时器只活在本组件里，首个 delta 到达后组件卸载、定时器随之清除；
 * 流式输出期间不存在任何 1Hz 重渲染。锚点缺失（执行状态还没到）时连定时器都不启动。
 */

import { Avatar } from '@douyinfe/semi-ui';
import { IconCustomerSupport } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

import { MESSAGE_ROLE_KEYS } from './MessageThread';
import type { RunStatus, WebchatExecutionStatus } from '../types';
import { isTerminalRunStatus } from '../runState';
import { useRunClock } from '../hooks/useRunClock';

/** 已用秒数：锚点是服务端时间戳，页面时钟可能略偏——负数按 0 处理，不用它推断顺序。 */
export function elapsedSecondsSince(anchor: string, now: number): number | null {
  const started = Date.parse(anchor);
  if (!Number.isFinite(started)) return null;
  return Math.max(0, Math.floor((now - started) / 1000));
}

/** 计时展示：`mm:ss`，超过一小时用 `h:mm:ss`（纯函数，边界由测试钉死）。 */
export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  const mmss = `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
  return hours > 0 ? `${hours}:${mmss}` : mmss;
}

/** 思考占位的判定输入：只要「非终态且还没有任何正文/附件」就显示；否则返回 undefined。 */
export interface ThinkingSource {
  runStatus: RunStatus;
  executionStatus: WebchatExecutionStatus | null;
  /** 是否已经有可见内容（正文或已发布的附件卡片）。 */
  hasVisibleContent: boolean;
}

/**
 * 返回计时锚点（`undefined` = 不显示占位，`null` = 显示但没有锚点、不启动定时器）。
 *
 * 判据只看两个持久事实：Run 是否终态、是否已有可见内容。首字一到就返回 undefined，
 * 占位卸载即停表——这是"回答阶段不显示耗时"的唯一实现点。
 */
export function thinkingAnchorOf(source: ThinkingSource): string | null | undefined {
  if (isTerminalRunStatus(source.runStatus)) return undefined;
  if (source.hasVisibleContent) return undefined;
  return source.executionStatus?.started_at ?? source.executionStatus?.queued_at ?? null;
}

export interface ChatThinkingProps {
  /** 计时锚点（服务端 `started_at`，未开始执行时用 `queued_at`）；缺失时只显示文字。 */
  anchor: string | null;
}

/** 助手思考占位：`anchor` 存在时每秒刷新一次已用时长。 */
export function ChatThinking(props: ChatThinkingProps) {
  const { t } = useTranslation();
  const now = useRunClock(props.anchor !== null);
  const elapsed = props.anchor === null ? null : elapsedSecondsSince(props.anchor, now);
  return (
    <div
      className="chat-message-item chat-message-item-assistant chat-message-item-thinking"
      data-testid="chat-thinking"
    >
      <Avatar size="small" className="chat-message-avatar" aria-hidden="true">
        <IconCustomerSupport />
      </Avatar>
      <div className="chat-message-body">
        <div className="chat-message-meta">
          <span className="chat-message-role">{t(MESSAGE_ROLE_KEYS.ASSISTANT)}</span>
        </div>
        <div className="chat-message-thinking-line" data-testid="chat-thinking-line">
          {elapsed === null
            ? t('chat.thinking')
            : t('chat.thinking.elapsed', { elapsed: formatElapsed(elapsed) })}
        </div>
      </div>
    </div>
  );
}
