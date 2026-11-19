/**
 * 任务名旁的技术信息入口与弹层（design frontend §3.3 CMP-05）。
 *
 * 默认关闭；点击外部、按 Esc 或点关闭按钮都会收起，同一时间只打开一个。
 * 打开后焦点进入弹层，关闭后回到入口按钮，所有用户可见。
 */

import { IconClose, IconInfoCircle } from '@douyinfe/semi-icons';
import { useEffect, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface TaskInfoButtonProps {
  open: boolean;
  label: string;
  onToggle(): void;
}

export function TaskInfoButton(props: TaskInfoButtonProps) {
  return (
    <button
      type="button"
      className="chat-task-tech-button"
      aria-label={props.label}
      aria-haspopup="dialog"
      aria-expanded={props.open}
      onClick={props.onToggle}
    >
      <IconInfoCircle size="small" />
    </button>
  );
}

export interface TaskInfoPopoverProps {
  open: boolean;
  title: string;
  hint: string;
  testId?: string;
  className?: string;
  onClose(): void;
  children: ReactNode;
}

export function TaskInfoPopover(props: TaskInfoPopoverProps) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(props.onClose);
  closeRef.current = props.onClose;
  const { open } = props;

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const restoreTo = typeof document === 'undefined' ? null : document.activeElement;
    panel?.focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      closeRef.current();
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (panel?.contains(target) === true) return;
      const element = target instanceof Element ? target : target.parentElement;
      if (element?.closest('.chat-task-tech-button') != null) return;
      closeRef.current();
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('mousedown', onPointerDown);
      if (restoreTo instanceof HTMLElement) restoreTo.focus({ preventScroll: true });
    };
  }, [open]);

  return (
    <div
      ref={panelRef}
      className={
        props.className === undefined
          ? 'chat-task-tech-popover'
          : `chat-task-tech-popover ${props.className}`
      }
      data-testid={props.testId}
      hidden={!open}
      role="dialog"
      aria-label={props.title}
      tabIndex={-1}
    >
      <div className="chat-task-tech-popover-head">
        <div>
          <strong>{props.title}</strong>
          <span>{props.hint}</span>
        </div>
        <button
          type="button"
          className="chat-task-tech-close"
          aria-label={t('common.close')}
          onClick={() => closeRef.current()}
        >
          <IconClose size="small" />
        </button>
      </div>
      {props.children}
    </div>
  );
}
