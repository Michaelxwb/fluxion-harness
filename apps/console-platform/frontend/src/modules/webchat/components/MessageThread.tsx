/**
 * 消息线程（design frontend §3.3 `MessageThread`/`LoadOlderMessages`、§3.5 `messages`、
 * §3.6 历史切换/旧消息、§3.7 样式方案、§3.8 可访问性；TASK-080）。
 *
 * DeepSeek 式单会话消息区：顶部按服务端 `hasOlder` 提供「加载更早消息」，下面按 `seq` 升序逐条
 * 渲染 USER/ASSISTANT 气泡与附件卡片，底部由输入区（TASK-081）接续。
 *
 * 1. **安全渲染**：消息正文只经**白名单 Markdown 子集**（行内代码/加粗/围栏代码/换行/安全协议
 *    链接）构造 React 元素，HTML 与 MDX 源码一律是文本（React 转义），不注入 HTML、不执行 MDX；
 *    链接只接受可独立解析的 `http:`/`https:`/`mailto:`，相对/协议相对/伪协议降级为文本。
 * 2. **附件只经授权下载面**：卡片只展示文件名/种类/大小与下载动作，取件走注入的下载端口
 *    （`createAttachmentDownloader` 绑到 `WebchatClient.download`，服务端逐请求复核授权）；
 *    组件不拼路径、不读存储键、不为附件生成任何 `href`；失效附件不出下载动作并如实标注。
 * 3. **身份与锚点**：每条消息把 `event_id`/`seq` 落到 DOM（滚动锚点按身份定位，不按下标）；
 *    上翻动作由 `useMessages` 绑定，组件只触发 `onLoadOlder`（滚动触发留给浏览器层）。
 * 4. **模式显式**：首屏未落地 → 骨架；无消息 → 空态；服务端失败 → 就地重试且**不清空**已有消息；
 *    本地动作失败 → 独立提示（不伪装成服务端失败）。
 *
 * 展示组件只收 typed props 与事件；不发 HTTP、不读 Cookie、不落持久存储、不自带定时器、不持有状态、
 * 不引用会话状态层（唯一来源是调用方传入的快照与动作）。
 */

import { Avatar, Banner, Button, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import {
  IconCustomerSupport,
  IconDownload,
  IconFile,
  IconImage,
  IconPaperclip,
  IconUser
} from '@douyinfe/semi-icons';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { CopyButton } from '../../../components/common/CopyButton';
import { formatDateTime } from '../../../components/common/DateTimeText';

import type {
  AttachmentKind,
  WebchatAttachment,
  WebchatHistoryMessage,
  WebchatMessageRole,
  WebchatRequestOptions
} from '../types';

/** 本地稳定码：失效附件不取件（UI 已不提供下载动作，绑定层再拒绝一次）。 */
export const ATTACHMENT_UNAVAILABLE = 'ATTACHMENT_UNAVAILABLE';

/** 失效附件取件请求：显式失败，绝不静默跳过。 */
export class AttachmentUnavailableError extends Error {
  readonly code: string;

  constructor() {
    super(ATTACHMENT_UNAVAILABLE);
    this.name = 'AttachmentUnavailableError';
    this.code = ATTACHMENT_UNAVAILABLE;
  }
}

/** 附件下载端口：`WebchatClient.download`（TASK-055/073 的授权下载面）结构满足。 */
export interface MessageAttachmentDownloadPort {
  download(artifactId: string, options?: WebchatRequestOptions): Promise<Blob>;
}

/** 附件动作失败端口：本地拒绝与取件失败都显式上报，绝不静默。 */
export type MessageAttachmentFailureHandler = (error: unknown) => void;

/** 把附件下载绑到授权下载面：只传 `artifact_id`（不拼路径、不读存储键），失败显式上报。 */
export function createAttachmentDownloader(
  port: MessageAttachmentDownloadPort,
  onFailure: MessageAttachmentFailureHandler
): (attachment: WebchatAttachment) => void {
  return (attachment) => {
    if (!attachment.available) {
      onFailure(new AttachmentUnavailableError());
      return;
    }
    port.download(attachment.artifact_id).then(undefined, onFailure);
  };
}

/** 行内标记：白名单构造（HTML/MDX 只能是文本）。 */
export type MessageInlineToken =
  | { kind: 'text'; value: string }
  | { kind: 'code'; value: string }
  | { kind: 'strong'; value: string }
  | { kind: 'emphasis'; value: string }
  | { kind: 'strike'; value: string }
  | { kind: 'link'; label: string; href: string }
  | { kind: 'break' };

/** 列表项：行内标记组成的单行/多行文本（嵌套列表按缩进文本保留，不丢内容）。 */
export interface MessageListItem {
  tokens: MessageInlineToken[];
}

/** 正文块：段落、标题、围栏代码、列表、引用、分隔线与表格（全部白名单构造）。 */
export type MessageBlock =
  | { kind: 'paragraph'; tokens: MessageInlineToken[] }
  | { kind: 'heading'; level: 1 | 2 | 3 | 4 | 5 | 6; tokens: MessageInlineToken[] }
  | { kind: 'code'; lang: string | null; value: string }
  | { kind: 'list'; ordered: boolean; items: MessageListItem[] }
  | { kind: 'quote'; blocks: MessageBlock[] }
  | { kind: 'hr' }
  | { kind: 'table'; header: MessageInlineToken[][]; rows: MessageInlineToken[][][] };

/** 附件种类词条：逐项映射（未知种类不伪造标签）。 */
export const MESSAGE_ATTACHMENT_KIND_KEYS: Record<AttachmentKind, string> = {
  IMAGE: 'chat.thread.attachment.image',
  DOCUMENT: 'chat.thread.attachment.document',
  OTHER: 'chat.thread.attachment.other'
};

/** 角色词条：逐项映射（消息角色只有 USER/ASSISTANT）。 */
export const MESSAGE_ROLE_KEYS: Record<WebchatMessageRole, string> = {
  USER: 'chat.thread.role.user',
  ASSISTANT: 'chat.thread.role.assistant'
};

const SAFE_LINK_PROTOCOLS: readonly string[] = ['http:', 'https:', 'mailto:'];
const INLINE_PATTERN =
  /`([^`\n]+)`|\*\*([^*\n]+)\*\*|__([^_\n]+)__|~~([^~\n]+)~~|\*([^*\n]+)\*|(?<!\w)_([^_\n]+)_(?!\w)|\[([^\]\n]+)\]\(([^)\s]+)\)/g;
const FENCE = '```';
const HEADING_PATTERN = /^(#{1,6})\s+(.*)$/;
const LIST_ITEM_PATTERN = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const QUOTE_PATTERN = /^\s*>\s?(.*)$/;
const HR_PATTERN = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const TABLE_SEPARATOR_PATTERN = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;

/** 链接协议白名单：只接受可独立解析的 http/https/mailto（相对/协议相对/伪协议一律 null）。 */
export function safeLinkHref(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  return SAFE_LINK_PROTOCOLS.includes(url.protocol) ? raw : null;
}

/** 纯文本入队：换行切成 `break`，空片段不产出（不构造任何标记）。 */
function pushText(tokens: MessageInlineToken[], value: string): void {
  value.split('\n').forEach((line, index) => {
    if (index > 0) tokens.push({ kind: 'break' });
    if (line !== '') tokens.push({ kind: 'text', value: line });
  });
}

/** 行内解析：行内代码/加粗/斜体/删除线/安全链接/换行，其余原样文本——HTML/MDX 不解析。 */
export function parseInlineTokens(text: string): MessageInlineToken[] {
  const tokens: MessageInlineToken[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const start = match.index ?? 0;
    pushText(tokens, text.slice(cursor, start));
    const href = match[8] === undefined ? null : safeLinkHref(match[8]);
    if (match[1] !== undefined) tokens.push({ kind: 'code', value: match[1] });
    else if (match[2] !== undefined) tokens.push({ kind: 'strong', value: match[2] });
    else if (match[3] !== undefined) tokens.push({ kind: 'strong', value: match[3] });
    else if (match[4] !== undefined) tokens.push({ kind: 'strike', value: match[4] });
    else if (match[5] !== undefined) tokens.push({ kind: 'emphasis', value: match[5] });
    else if (match[6] !== undefined) tokens.push({ kind: 'emphasis', value: match[6] });
    else if (href !== null && match[7] !== undefined) {
      tokens.push({ kind: 'link', label: match[7], href });
    } else pushText(tokens, match[0]);
    cursor = start + match[0].length;
  }
  pushText(tokens, text.slice(cursor));
  return tokens;
}

/** 表格起始：当前行含 `|` 且下一行是 GFM 分隔行。 */
function isTableStart(lines: readonly string[], index: number): boolean {
  return (
    index + 1 < lines.length &&
    lines[index].includes('|') &&
    TABLE_SEPARATOR_PATTERN.test(lines[index + 1])
  );
}

/** 表格行拆格：去掉首尾管道后按 `|` 切分；空单元格保留（不伪造内容）。 */
function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

/** 块起始判定：段落遇到这些行必须收束（标题/围栏/列表/引用/分隔线/表格）。 */
function startsBlock(lines: readonly string[], index: number): boolean {
  const line = lines[index];
  return (
    line.startsWith(FENCE) ||
    HEADING_PATTERN.test(line) ||
    HR_PATTERN.test(line) ||
    QUOTE_PATTERN.test(line) ||
    LIST_ITEM_PATTERN.test(line) ||
    isTableStart(lines, index)
  );
}

/** 块解析：标题/段落/围栏代码/列表/引用/分隔线/表格；未闭合围栏原样收尾，不抛错、不丢内容。 */
export function parseMessageBlocks(text: string): MessageBlock[] {
  const blocks: MessageBlock[] = [];
  const lines = text.split('\n');
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.startsWith(FENCE)) {
      const lang = line.slice(FENCE.length).trim() || null;
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith(FENCE)) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push({ kind: 'code', lang, value: code.join('\n') });
      continue;
    }
    if (isTableStart(lines, index)) {
      const header = tableCells(line).map((cell) => parseInlineTokens(cell));
      index += 2;
      const rows: MessageInlineToken[][][] = [];
      while (index < lines.length && lines[index].includes('|') && lines[index].trim() !== '') {
        rows.push(tableCells(lines[index]).map((cell) => parseInlineTokens(cell)));
        index += 1;
      }
      blocks.push({ kind: 'table', header, rows });
      continue;
    }
    const heading = line.match(HEADING_PATTERN);
    if (heading !== null) {
      const level = heading[1].length as 1 | 2 | 3 | 4 | 5 | 6;
      blocks.push({ kind: 'heading', level, tokens: parseInlineTokens(heading[2]) });
      index += 1;
      continue;
    }
    if (HR_PATTERN.test(line)) {
      blocks.push({ kind: 'hr' });
      index += 1;
      continue;
    }
    const quote = line.match(QUOTE_PATTERN);
    if (quote !== null) {
      const inner: string[] = [];
      while (index < lines.length) {
        const current = lines[index].match(QUOTE_PATTERN);
        if (current === null) break;
        inner.push(current[1]);
        index += 1;
      }
      blocks.push({ kind: 'quote', blocks: parseMessageBlocks(inner.join('\n')) });
      continue;
    }
    const listItem = line.match(LIST_ITEM_PATTERN);
    if (listItem !== null) {
      const ordered = /\d/.test(listItem[2]);
      const items: MessageListItem[] = [];
      while (index < lines.length) {
        const current = lines[index].match(LIST_ITEM_PATTERN);
        if (current === null || /\d/.test(current[2]) !== ordered) break;
        const itemLines = [current[3]];
        index += 1;
        while (index < lines.length) {
          const next = lines[index];
          if (next.trim() === '' || LIST_ITEM_PATTERN.test(next) || startsBlock(lines, index)) break;
          if (!/^\s+/.test(next)) break;
          itemLines.push(next.trim());
          index += 1;
        }
        items.push({ tokens: parseInlineTokens(itemLines.join('\n')) });
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() !== '' && !startsBlock(lines, index)) {
      paragraph.push(lines[index]);
      index += 1;
    }
    if (paragraph.length === 0) index += 1;
    else blocks.push({ kind: 'paragraph', tokens: parseInlineTokens(paragraph.join('\n')) });
  }
  return blocks;
}

/** 附件大小：按字节展示（B/KB/MB），非法值归 0（不显示负数或 NaN）。 */
export function formatAttachmentSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${Math.floor(bytes)} B`;
  const kilobytes = bytes / 1024;
  if (kilobytes < 1024) return `${kilobytes.toFixed(1)} KB`;
  return `${(kilobytes / 1024).toFixed(1)} MB`;
}


export interface MessageThreadProps {
  /** 当前会话的消息页（TASK-078 已按 `seq` 升序、按身份去重）。 */
  messages: readonly WebchatHistoryMessage[];
  /** 首屏消息页尚未落地（显示骨架，不显示空态）。 */
  loading: boolean;
  /** 更早页正在加载（顶部加载指示；加载中不重复触发）。 */
  loadingOlder: boolean;
  /** 服务端还有更早页（分页事实，不代表已渲染 Run 游标）。 */
  hasOlder: boolean;
  /** 服务端/传输失败（状态层 `history.errorCode`）：就地重试，已有内容不清空。 */
  errorCode: string | null;
  /** 本地动作失败（如控制器尚未选择会话）：独立提示，不静默。 */
  failureCode: string | null;
  onLoadOlder(): void;
  onRetry(): void;
  /** 附件取件端口（`createAttachmentDownloader` 绑到授权下载面）。 */
  onDownloadAttachment(attachment: WebchatAttachment): void;
}

/** 白名单标记 → React 元素；文本节点原样交给 React 转义（HTML/MDX 不会被解析成元素）。 */
function renderInlineTokens(tokens: readonly MessageInlineToken[], keyPrefix: string): ReactNode[] {
  return tokens.map((token, index) => {
    const key = `${keyPrefix}-${index}`;
    if (token.kind === 'code') {
      return (
        <code key={key} className="chat-message-code">
          {token.value}
        </code>
      );
    }
    if (token.kind === 'strong') return <strong key={key}>{token.value}</strong>;
    if (token.kind === 'emphasis') return <em key={key}>{token.value}</em>;
    if (token.kind === 'strike') return <del key={key}>{token.value}</del>;
    if (token.kind === 'break') return <br key={key} />;
    if (token.kind === 'link') {
      return (
        <a
          key={key}
          className="chat-message-link"
          href={token.href}
          target="_blank"
          rel="noopener noreferrer"
        >
          {token.label}
        </a>
      );
    }
    return token.value;
  });
}

/** 围栏代码：语言标签 + 复制；横向滚动由样式层承担。 */
function MessageCodeBlock(props: { block: Extract<MessageBlock, { kind: 'code' }> }) {
  return (
    <div className="chat-message-code-block" data-testid="chat-message-code-block">
      <div className="chat-message-code-head">
        <span className="chat-message-code-lang">{props.block.lang ?? 'code'}</span>
        <CopyButton value={props.block.value} />
      </div>
      <pre>
        <code>{props.block.value}</code>
      </pre>
    </div>
  );
}

/** 单行/多行列表项内容（嵌套列表按缩进文本保留，不丢内容）。 */
function renderListItem(item: MessageListItem, keyPrefix: string): ReactNode {
  return renderInlineTokens(item.tokens, keyPrefix);
}

/** 表格：横向滚动容器 + 表头/表体；单元格只收行内白名单标记。 */
function MessageTable(props: { block: Extract<MessageBlock, { kind: 'table' }>; keyPrefix: string }) {
  return (
    <div className="chat-message-table-wrap">
      <table className="chat-message-table">
        <thead>
          <tr>
            {props.block.header.map((cell, index) => (
              <th key={`${props.keyPrefix}-h-${index}`}>
                {renderInlineTokens(cell, `${props.keyPrefix}-h-${index}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.block.rows.map((row, rowIndex) => (
            <tr key={`${props.keyPrefix}-r-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${props.keyPrefix}-r-${rowIndex}-c-${cellIndex}`}>
                  {renderInlineTokens(cell, `${props.keyPrefix}-r-${rowIndex}-c-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 块级渲染：标题/段落/代码/列表/引用/分隔线/表格，全部是 React 元素，无 HTML 注入。 */
function renderBlocks(blocks: readonly MessageBlock[], keyPrefix: string): ReactNode[] {
  return blocks.map((block, index) => {
    const key = `${keyPrefix}-${index}`;
    if (block.kind === 'code') return <MessageCodeBlock key={key} block={block} />;
    if (block.kind === 'heading') {
      const Heading = `h${block.level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      return (
        <Heading key={key} className={`chat-message-heading chat-message-heading-${block.level}`}>
          {renderInlineTokens(block.tokens, key)}
        </Heading>
      );
    }
    if (block.kind === 'list') {
      const List = block.ordered ? 'ol' : 'ul';
      return (
        <List key={key} className="chat-message-markdown-list">
          {block.items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>{renderListItem(item, `${key}-${itemIndex}`)}</li>
          ))}
        </List>
      );
    }
    if (block.kind === 'quote') {
      return (
        <blockquote key={key} className="chat-message-quote">
          {renderBlocks(block.blocks, key)}
        </blockquote>
      );
    }
    if (block.kind === 'hr') return <hr key={key} className="chat-message-hr" />;
    if (block.kind === 'table') {
      return <MessageTable key={key} block={block} keyPrefix={key} />;
    }
    return (
      <p key={key} className="chat-message-paragraph">
        {renderInlineTokens(block.tokens, key)}
      </p>
    );
  });
}

/** 消息正文：只渲染白名单块；不做 HTML/MDX 求值，不写 `dangerouslySetInnerHTML`。 */
export function MessageContent(props: { text: string }) {
  return <div className="chat-message-content">{renderBlocks(parseMessageBlocks(props.text), 'block')}</div>;
}


interface MessageAttachmentCardProps {
  attachment: WebchatAttachment;
  onDownload(attachment: WebchatAttachment): void;
}

/** 附件图标：按真实种类选图标；未知种类不伪造具体类型。 */
function attachmentIcon(kind: AttachmentKind): ReactNode {
  if (kind === 'IMAGE') return <IconImage />;
  if (kind === 'DOCUMENT') return <IconFile />;
  return <IconPaperclip />;
}

/** 附件卡片：文件图标 + 名称/类型/大小 + 授权下载；失效附件如实标注。 */
function MessageAttachmentCard(props: MessageAttachmentCardProps) {
  const { t } = useTranslation();
  const { attachment } = props;
  const filename = attachment.filename?.trim() ?? '';
  const name = filename === '' ? t(MESSAGE_ATTACHMENT_KIND_KEYS[attachment.kind]) : filename;
  return (
    <div
      className="chat-message-attachment"
      data-testid="chat-message-attachment"
      data-kind={attachment.kind}
      data-available={attachment.available ? 'true' : 'false'}
    >
      <span className="chat-message-attachment-icon" aria-hidden="true">
        {attachmentIcon(attachment.kind)}
      </span>
      <span className="chat-message-attachment-info">
        <span className="chat-message-attachment-name" data-testid="chat-message-attachment-name">
          {name}
        </span>
        <span className="chat-message-attachment-meta">
          {attachment.media_type} · {formatAttachmentSize(attachment.size)}
        </span>
      </span>
      {attachment.available ? (
        <Button
          size="small"
          theme="borderless"
          className="chat-message-attachment-download"
          data-testid="chat-message-attachment-download"
          icon={<IconDownload />}
          aria-label={t('chat.thread.download')}
          onClick={() => props.onDownload(attachment)}
        />
      ) : (
        <span
          className="chat-message-attachment-unavailable"
          data-testid="chat-message-attachment-unavailable"
        >
          {t('chat.thread.attachmentUnavailable')}
        </span>
      )}
    </div>
  );
}

interface MessageItemProps {
  message: WebchatHistoryMessage;
  onDownload(attachment: WebchatAttachment): void;
}

/** 消息时钟：完整展示服务端落库时间（年月日 + 时分），不可解析不显示（不猜时间）。 */
export function messageClock(value: string | undefined): string | null {
  if (value === undefined) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return formatDateTime(date).slice(0, 16);
}

/** 单条消息：头像 + 角色/时间 + 安全正文 + 附件；身份（`event_id`/`seq`）落到 DOM 供锚点定位。 */
function MessageItem(props: MessageItemProps) {
  const { t } = useTranslation();
  const { message } = props;
  const clock = messageClock(message.created_at);
  return (
    <li
      className={`chat-message-item chat-message-item-${message.role.toLowerCase()}`}
      data-testid="chat-message-item"
      data-role={message.role}
      data-event-id={message.event_id}
      data-seq={message.seq}
    >
      <Avatar size="small" className="chat-message-avatar" aria-hidden="true">
        {message.role === 'USER' ? <IconUser /> : <IconCustomerSupport />}
      </Avatar>
      <div className="chat-message-body">
        <div className="chat-message-meta">
          <span className="chat-message-role" data-testid="chat-message-role">
            {t(MESSAGE_ROLE_KEYS[message.role])}
          </span>
          {clock === null ? null : (
            <time
              className="chat-message-time"
              data-testid="chat-message-time"
              dateTime={message.created_at}
            >
              {clock}
            </time>
          )}
        </div>
        <MessageContent text={message.text} />
        {message.attachments.length === 0 ? null : (
          <div className="chat-message-attachments">
            {message.attachments.map((attachment) => (
              <MessageAttachmentCard
                key={attachment.artifact_id}
                attachment={attachment}
                onDownload={props.onDownload}
              />
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

interface MessageThreadHeaderProps {
  loadingOlder: boolean;
  hasOlder: boolean;
  onLoadOlder(): void;
}

/** 顶部上翻动作：只在服务端还有更早页时出现；加载中显示进度并禁用重复触发。 */
function MessageThreadHeader(props: MessageThreadHeaderProps) {
  const { t } = useTranslation();
  if (!props.hasOlder) return null;
  return (
    <div className="chat-message-thread-header">
      <Button
        size="small"
        theme="borderless"
        loading={props.loadingOlder}
        disabled={props.loadingOlder}
        data-testid="chat-message-load-older"
        onClick={props.onLoadOlder}
      >
        {t('chat.thread.loadOlder')}
      </Button>
    </div>
  );
}

interface MessageThreadFailureProps {
  errorCode: string | null;
  failureCode: string | null;
  onRetry(): void;
}

/** 失败面：本地失败与服务端失败分开表达（本地失败没有可重试的服务端请求）。 */
function MessageThreadFailure(props: MessageThreadFailureProps) {
  const { t } = useTranslation();
  if (props.failureCode !== null) {
    return (
      <div className="chat-message-local-failure" data-testid="chat-message-local-failure">
        <Banner type="warning" description={t('chat.thread.localFailed')} />
      </div>
    );
  }
  if (props.errorCode === null) return null;
  return (
    <div className="chat-message-retry" data-testid="chat-message-retry">
      <Banner type="danger" description={t('chat.thread.failed')} />
      <Button size="small" data-testid="chat-message-retry-action" onClick={props.onRetry}>
        {t('common.retry')}
      </Button>
    </div>
  );
}

/** 单会话消息线程：顶部上翻 + 失败面 + 骨架/空态 + 消息列表（滚动视口由调用方持有）。 */
export function MessageThread(props: MessageThreadProps) {
  const { t } = useTranslation();
  const empty = !props.loading && props.messages.length === 0;
  return (
    <div
      className="chat-message-thread"
      data-testid="chat-message-thread"
      role="log"
      aria-label={t('chat.thread.label')}
    >
      <MessageThreadHeader
        loadingOlder={props.loadingOlder}
        hasOlder={props.hasOlder}
        onLoadOlder={props.onLoadOlder}
      />
      <MessageThreadFailure
        errorCode={props.errorCode}
        failureCode={props.failureCode}
        onRetry={props.onRetry}
      />
      {props.loading ? (
        <div className="chat-message-loading" data-testid="chat-message-loading">
          <Spin />
          <Typography.Text>{t('common.loading')}</Typography.Text>
        </div>
      ) : null}
      {empty && props.errorCode === null && props.failureCode === null ? (
        <div className="chat-message-empty" data-testid="chat-message-empty">
          <Empty description={t('chat.thread.empty')} />
        </div>
      ) : null}
      <ol className="chat-message-list">
        {props.messages.map((message) => (
          <MessageItem key={message.event_id} message={message} onDownload={props.onDownloadAttachment} />
        ))}
      </ol>
    </div>
  );
}
