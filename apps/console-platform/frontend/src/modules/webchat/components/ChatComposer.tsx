/**
 * 输入区（design frontend §3.3 `ChatComposer`、§3.4 组件接口契约、§3.6 发送/回复/附件、
 * §3.7 样式方案、§3.8 可访问性；TASK-081）。
 *
 * 底部输入区：文本 + 附件选择 + 发送，按真实阶段展示「上传中 / 待受理 / 失败可重试」，附件逐件给出
 * 状态与移除动作。文本与附件由 `usePendingSend` 持有的状态提供（附件用 state，不塞未注册 Form 字段、
 * 不用 `FormData` 收集输入），本组件只收 typed props 与事件。
 *
 * 1. **限额只来自投影**：数量/单件大小/文本字节三个上限都来自 API-19 投影（`attachmentPolicy`），
 *    未就绪时 `null`（不显示猜测值、输入禁用），组件不持有任何默认常量。
 * 2. **阶段显式**：上传中/待受理是独立阶段（不显示伪成功、成功前不清输入），失败给出稳定码文案与
 *    重试/放弃动作；结果未知时明示「重试沿用同一条消息，不会重复创建」。
 * 3. **文件用 state**：附件是真实 `<input type="file">` 的选择结果，经 `onAddFiles` 进入状态；
 *    逐件状态（待上传/上传中/已上传/失败/暂存过期）与文件名/大小只展示，不拼路径、不直出存储键。
 * 4. **可访问性**：输入区/输入框/附件选择/发送/取消/重试/放弃/移除都有标签；Enter 发送、
 *    Shift+Enter 换行；在途与失败状态可键盘操作。
 *
 * 展示组件只收 typed props 与事件；不发 HTTP、不读 Cookie、不落持久存储、不自带定时器、不持有状态、
 * 不引用会话状态层（唯一来源是调用方传入的快照与动作）。
 */

import { Button } from '@douyinfe/semi-ui';
import type { ChangeEvent, KeyboardEvent, RefObject } from 'react';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { formatAttachmentSize } from './MessageThread';
import type {
  ComposerAttachment,
  ComposerAttachmentPolicy,
  ComposerFileStatus,
  PendingSendPhase
} from '../hooks/usePendingSend';
import { pendingSendErrorKey } from '../hooks/usePendingSend';

/** 附件展示状态：暂存过期是本地判定（服务端仍权威），与上传状态分开表达。 */
export type ComposerAttachmentDisplayStatus = ComposerFileStatus | 'EXPIRED';

/** 逐件状态词条：静态映射，词条键在源码里可被 i18n 检查直接看到。 */
export const COMPOSER_ATTACHMENT_STATUS_KEYS: Record<ComposerAttachmentDisplayStatus, string> = {
  READY: 'chat.composer.attachment.ready',
  UPLOADING: 'chat.composer.attachment.uploading',
  UPLOADED: 'chat.composer.attachment.uploaded',
  FAILED: 'chat.composer.attachment.failed',
  EXPIRED: 'chat.composer.attachment.expired'
};

/** 阶段词条：只有上传中与待受理需要显式提示（空闲/失败各有自己的呈现）。 */
export const COMPOSER_PHASE_KEYS: Partial<Record<PendingSendPhase, string>> = {
  STAGING: 'chat.composer.staging',
  PENDING: 'chat.composer.pending'
};

/** 展示状态：本地判定过期的暂存身份如实标注（不改写身份、不重传）。 */
export function composerAttachmentStatus(
  attachment: ComposerAttachment,
  expiredUploadIds: readonly string[]
): ComposerAttachmentDisplayStatus {
  if (attachment.uploadId !== null && expiredUploadIds.includes(attachment.uploadId)) {
    return 'EXPIRED';
  }
  return attachment.status;
}

interface ComposerAttachmentChipProps {
  attachment: ComposerAttachment;
  expiredUploadIds: readonly string[];
  disabled: boolean;
  onRemove(localId: string): void;
}

/** 附件条目：只展示文件名/大小与逐件状态；移除动作不触碰服务端暂存身份。 */
function ComposerAttachmentChip(props: ComposerAttachmentChipProps) {
  const { t } = useTranslation();
  const { attachment } = props;
  const status = composerAttachmentStatus(attachment, props.expiredUploadIds);
  return (
    <li
      className="chat-composer-attachment"
      data-testid="chat-composer-attachment"
      data-status={status}
    >
      <span className="chat-composer-attachment-name" data-testid="chat-composer-attachment-name">
        {attachment.filename}
      </span>
      <span className="chat-composer-attachment-meta">
        {attachment.mediaType} · {formatAttachmentSize(attachment.size)}
      </span>
      <span className="chat-composer-attachment-status" data-testid="chat-composer-attachment-status">
        {t(COMPOSER_ATTACHMENT_STATUS_KEYS[status])}
      </span>
      <Button
        size="small"
        theme="borderless"
        className="chat-composer-attachment-remove"
        data-testid="chat-composer-attachment-remove"
        disabled={props.disabled}
        onClick={() => props.onRemove(attachment.localId)}
      >
        {t('chat.composer.attachment.remove')}
      </Button>
    </li>
  );
}

interface ComposerAttachmentsProps {
  attachments: readonly ComposerAttachment[];
  expiredUploadIds: readonly string[];
  disabled: boolean;
  onRemove(localId: string): void;
}

/** 附件列表：无附件时不留空容器。 */
function ComposerAttachments(props: ComposerAttachmentsProps) {
  if (props.attachments.length === 0) return null;
  return (
    <ul className="chat-composer-attachments" data-testid="chat-composer-attachments">
      {props.attachments.map((attachment) => (
        <ComposerAttachmentChip
          key={attachment.localId}
          attachment={attachment}
          expiredUploadIds={props.expiredUploadIds}
          disabled={props.disabled}
          onRemove={props.onRemove}
        />
      ))}
    </ul>
  );
}

/** 限额提示：三个上限都来自 API-19 投影（未就绪时调用方不渲染，不显示猜测值）。 */
function ComposerHints(props: { policy: ComposerAttachmentPolicy }) {
  const { t } = useTranslation();
  return (
    <p className="chat-composer-hints" data-testid="chat-composer-hints">
      <span data-testid="chat-composer-hint-files">
        {t('chat.composer.hint.files', { max: props.policy.upload_max_per_message })}
      </span>
      <span data-testid="chat-composer-hint-size">
        {t('chat.composer.hint.size', { size: formatAttachmentSize(props.policy.upload_max_bytes) })}
      </span>
      <span data-testid="chat-composer-hint-text">
        {t('chat.composer.hint.text', { max: props.policy.text_max_bytes })}
      </span>
    </p>
  );
}

/** 阶段提示：上传中/待受理显式可见（其余阶段不占位）。 */
function ComposerPhaseNote(props: { phase: PendingSendPhase }) {
  const { t } = useTranslation();
  const key = COMPOSER_PHASE_KEYS[props.phase];
  if (key === undefined) return null;
  return (
    <p className="chat-composer-phase" data-testid="chat-composer-phase" data-phase={props.phase}>
      {t(key)}
    </p>
  );
}

/** 输入侧预检失败：就地提示（与发送失败分开呈现）。 */
function ComposerInputError(props: { code: string }) {
  const { t } = useTranslation();
  return (
    <div
      className="chat-composer-input-error"
      data-testid="chat-composer-input-error"
      data-error-code={props.code}
    >
      {t(pendingSendErrorKey(props.code))}
    </div>
  );
}

interface ComposerSendErrorProps {
  phase: PendingSendPhase;
  code: string;
  outcomeUnknown: boolean;
  onDiscard(): void;
}

/** 发送失败面：稳定码文案 + 结果未知明示 + 显式放弃（重试是主操作）。 */
function ComposerSendError(props: ComposerSendErrorProps) {
  const { t } = useTranslation();
  return (
    <div
      className="chat-composer-error"
      data-testid="chat-composer-error"
      data-error-code={props.code}
    >
      <span className="chat-composer-error-message" data-testid="chat-composer-error-message">
        {t(pendingSendErrorKey(props.code))}
      </span>
      {props.outcomeUnknown ? (
        <span className="chat-composer-unknown-outcome" data-testid="chat-composer-unknown-outcome">
          {t('chat.composer.unknownOutcome')}
        </span>
      ) : null}
      {props.phase === 'FAILED' ? (
        <Button
          size="small"
          theme="borderless"
          className="chat-composer-discard"
          data-testid="chat-composer-discard"
          onClick={props.onDiscard}
        >
          {t('chat.composer.discard')}
        </Button>
      ) : null}
    </div>
  );
}

interface ComposerActionsProps {
  busy: boolean;
  disabled: boolean;
  canSend: boolean;
  /** 主操作文案词条（与 `onPrimary` 同一处推导，标签与行为不会分叉）。 */
  primaryLabel: string;
  fileInputRef: RefObject<HTMLInputElement>;
  onAddFiles(files: readonly File[]): void;
  onPrimary(): void;
  onCancel(): void;
}

/** 动作行：真实文件选择（多选）+ 在途取消 + 发送/重试主操作。 */
function ComposerActions(props: ComposerActionsProps) {
  const { t } = useTranslation();
  const handleFiles = (event: ChangeEvent<HTMLInputElement>): void => {
    const selected = event.target.files;
    if (selected !== null && selected.length > 0) props.onAddFiles(Array.from(selected));
    event.target.value = '';
  };
  return (
    <div className="chat-composer-actions">
      <input
        ref={props.fileInputRef}
        className="chat-composer-file"
        data-testid="chat-composer-file-input"
        type="file"
        multiple
        aria-label={t('chat.composer.attach')}
        disabled={props.disabled}
        onChange={handleFiles}
      />
      {props.busy ? (
        <Button
          size="small"
          theme="borderless"
          className="chat-composer-cancel"
          data-testid="chat-composer-cancel"
          onClick={props.onCancel}
        >
          {t('common.cancel')}
        </Button>
      ) : null}
      <Button
        className="chat-composer-send"
        data-testid="chat-composer-send"
        disabled={!props.canSend}
        onClick={props.onPrimary}
      >
        {t(props.primaryLabel)}
      </Button>
    </div>
  );
}

interface ComposerTextAreaProps {
  text: string;
  disabled: boolean;
  onTextChange(value: string): void;
  onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void;
}

/** 文本输入：Enter 发送 / Shift+Enter 换行（键处理按阶段由调用方决定）。 */
function ComposerTextArea(props: ComposerTextAreaProps) {
  const { t } = useTranslation();
  return (
    <textarea
      className="chat-composer-input"
      data-testid="chat-composer-input"
      aria-label={t('chat.composer.inputLabel')}
      placeholder={t('chat.composer.placeholder')}
      rows={3}
      value={props.text}
      disabled={props.disabled}
      onChange={(event) => props.onTextChange(event.target.value)}
      onKeyDown={props.onKeyDown}
    />
  );
}

interface ComposerNoticesProps {
  phase: PendingSendPhase;
  policy: ComposerAttachmentPolicy | null;
  inputErrorCode: string | null;
  errorCode: string | null;
  outcomeUnknown: boolean;
  onDiscard(): void;
}

/** 提示区：限额、阶段、预检失败与发送失败各自显式呈现（互不伪装）。 */
function ComposerNotices(props: ComposerNoticesProps) {
  return (
    <>
      {props.policy === null ? null : <ComposerHints policy={props.policy} />}
      <ComposerPhaseNote phase={props.phase} />
      {props.inputErrorCode === null ? null : <ComposerInputError code={props.inputErrorCode} />}
      {props.errorCode === null ? null : (
        <ComposerSendError
          phase={props.phase}
          code={props.errorCode}
          outcomeUnknown={props.outcomeUnknown}
          onDiscard={props.onDiscard}
        />
      )}
    </>
  );
}

interface ComposerFlags {
  busy: boolean;
  frozenRetry: boolean;
  inputDisabled: boolean;
  canSubmit: boolean;
  primaryAction(): void;
  /** 主操作文案词条：与 `primaryAction` 同一处推导。 */
  primaryLabel: string;
}

/** 阶段派生：冻结、可提交性、主操作与文案只由 typed props 推导（组件不持有第二份状态）。 */
function composerFlags(props: ChatComposerProps): ComposerFlags {
  const busy = props.phase === 'STAGING' || props.phase === 'PENDING';
  const frozenRetry = props.phase === 'FAILED' && props.inputFrozen;
  const inputDisabled = props.disabled || props.inputFrozen;
  const empty = props.text.trim() === '' && props.attachments.length === 0;
  const canSubmit = !props.disabled && !busy && (frozenRetry || (!inputDisabled && !empty));
  return {
    busy,
    frozenRetry,
    inputDisabled,
    canSubmit,
    primaryAction: frozenRetry ? props.onRetry : props.onSend,
    primaryLabel: frozenRetry ? 'common.retry' : 'chat.composer.send'
  };
}

/** Enter 发送 / Shift+Enter 换行；冻结的失败尝试按主操作（重试）。 */
function composerKeyHandler(flags: ComposerFlags): (event: KeyboardEvent<HTMLTextAreaElement>) => void {
  return (event) => {
    if (event.key !== 'Enter' || event.shiftKey || !flags.canSubmit) return;
    event.preventDefault();
    flags.primaryAction();
  };
}

export interface ChatComposerProps {
  /** 配置/身份未就绪（API-19 首次成功前）：不开放发送/上传/附件选择。 */
  disabled: boolean;
  phase: PendingSendPhase;
  text: string;
  onTextChange(value: string): void;
  attachments: readonly ComposerAttachment[];
  /** 动态限额（API-19 投影）；未就绪为 null。 */
  attachmentPolicy: ComposerAttachmentPolicy | null;
  /** 已暂存但本地判定过期的上传身份（只提示，发送时按新消息重新上传）。 */
  expiredUploadIds: readonly string[];
  /** 发请求前的本地预检失败（输入侧错误）。 */
  inputErrorCode: string | null;
  /** 发送/暂存失败（本地稳定码或服务端目录码）。 */
  errorCode: string | null;
  /** 响应未知：重试沿用同一消息号。 */
  outcomeUnknown: boolean;
  /** 输入冻结：在途发送或已提交待重试。 */
  inputFrozen: boolean;
  onAddFiles(files: readonly File[]): void;
  onRemoveAttachment(localId: string): void;
  onSend(): void;
  onRetry(): void;
  onDiscard(): void;
  onCancel(): void;
}

/**
 * 输入区：文本 + 附件选择 + 发送/取消/重试。
 *
 * 主操作按阶段收敛：失败且已提交时是「重试」（原样重放同一消息号），其余情况是「发送」；
 * 上传中/待受理时禁止重复发送并给出取消；预检失败与发送失败分开呈现。
 */
export function ChatComposer(props: ChatComposerProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const flags = composerFlags(props);
  const handleKeyDown = composerKeyHandler(flags);

  return (
    <section
      className="chat-composer"
      data-testid="chat-composer"
      data-phase={props.phase}
      aria-label={t('chat.composer.label')}
    >
      <ComposerAttachments
        attachments={props.attachments}
        expiredUploadIds={props.expiredUploadIds}
        disabled={flags.inputDisabled}
        onRemove={props.onRemoveAttachment}
      />
      <ComposerNotices
        phase={props.phase}
        policy={props.attachmentPolicy}
        inputErrorCode={props.inputErrorCode}
        errorCode={props.errorCode}
        outcomeUnknown={props.outcomeUnknown}
        onDiscard={props.onDiscard}
      />
      <ComposerTextArea
        text={props.text}
        disabled={flags.inputDisabled}
        onTextChange={props.onTextChange}
        onKeyDown={handleKeyDown}
      />
      <ComposerActions
        busy={flags.busy}
        disabled={flags.inputDisabled}
        canSend={flags.canSubmit}
        primaryLabel={flags.primaryLabel}
        fileInputRef={fileInputRef}
        onAddFiles={props.onAddFiles}
        onPrimary={flags.primaryAction}
        onCancel={props.onCancel}
      />
    </section>
  );
}
