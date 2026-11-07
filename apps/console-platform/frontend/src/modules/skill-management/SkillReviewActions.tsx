import { Button, TextArea } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { SkillArtifactDetail } from './services/skills';

export interface SkillReviewActionsProps {
  artifact: SkillArtifactDetail;
  canReview: boolean;
  submitting: boolean;
  submittingAction?: 'APPROVE' | 'REJECT' | null;
  error: string | null;
  onApprove(note: string): void;
  onReject(note: string): void;
}

/**
 * 批准/驳回按钮：只有管理员、PENDING 且未提交时可操作；驳回必须填写原因。
 * 审批意见同时用于批准备注与驳回原因，失败信息常驻展示，可再次提交重试。
 */
export function SkillReviewActions(props: SkillReviewActionsProps) {
  const { t } = useTranslation();
  const [note, setNote] = useState('');
  const [rejecting, setRejecting] = useState(false);
  const [noteRequired, setNoteRequired] = useState(false);
  const canAct =
    props.canReview && props.artifact.review_status === 'PENDING' && !props.submitting;
  const approving = props.submitting && props.submittingAction === 'APPROVE';
  const rejectingNow = props.submitting && props.submittingAction === 'REJECT';

  const approve = (): void => {
    if (!canAct) {
      return;
    }
    setRejecting(false);
    setNoteRequired(false);
    props.onApprove(note.trim());
  };

  const reject = (): void => {
    if (!canAct) {
      return;
    }
    if (!note.trim()) {
      setRejecting(true);
      setNoteRequired(true);
      return;
    }
    setNoteRequired(false);
    props.onReject(note.trim());
  };

  return (
    <div className="skill-review-actions" data-testid="skill-review-actions">
      <TextArea
        aria-label={t('skill.review.note')}
        placeholder={t('skill.review.notePlaceholder')}
        value={note}
        disabled={!canAct}
        data-testid="review-note"
        aria-required={rejecting}
        onChange={(value) => {
          setNote(value);
          if (value.trim()) {
            setNoteRequired(false);
          }
        }}
      />
      {noteRequired ? (
        <div className="skill-action-error" role="alert" data-testid="review-note-required">
          {t('skill.review.noteRequired')}
        </div>
      ) : null}
      {props.error ? (
        <div className="skill-action-error" role="alert" data-testid="review-error">
          {props.error}
        </div>
      ) : null}
      {!props.canReview ? (
        <div className="detail-hint" data-testid="review-no-permission">
          {t('skill.review.permissionHint')}
        </div>
      ) : null}
      <div className="skill-review-buttons">
        <Button
          theme="solid"
          data-testid="review-approve"
          disabled={!canAct}
          loading={approving}
          onClick={approve}
          aria-label={t('skill.review.approve')}
        >
          {approving ? t('skill.review.approving') : t('skill.review.approve')}
        </Button>
        <Button
          type="danger"
          theme="light"
          data-testid="review-reject"
          disabled={!canAct}
          loading={rejectingNow}
          onClick={reject}
          aria-label={t('skill.review.reject')}
        >
          {rejectingNow ? t('skill.review.rejecting') : t('skill.review.reject')}
        </Button>
      </div>
    </div>
  );
}
