import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { Tips } from '../../components/common/Tips';
import { reviewSkill, type SkillArtifactDetail, type SkillDetail } from './services/skills';
import { SkillFileList } from './SkillFileList';
import { SkillReviewActions } from './SkillReviewActions';
import { SkillReviewStatusTag } from './SkillReviewStatusTag';
import { SkillSourceTag } from './SkillSourceTag';

export interface SkillReviewPanelProps {
  skill: SkillDetail;
  artifact: SkillArtifactDetail;
  canReview: boolean;
  onReviewed(action: 'APPROVE' | 'REJECT'): void;
}

function sizeLabel(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

/** 审批面板：包信息、校验、文件清单、提交者/Agent 与批准/驳回操作。 */
export function SkillReviewPanel(props: SkillReviewPanelProps) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);
  const [submittingAction, setSubmittingAction] = useState<'APPROVE' | 'REJECT' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { artifact } = props;

  const submit = async (action: 'APPROVE' | 'REJECT', note: string): Promise<void> => {
    setSubmitting(true);
    setSubmittingAction(action);
    setError(null);
    try {
      await reviewSkill(props.skill.id, {
        artifact_id: artifact.artifact_id,
        action,
        review_note: note || undefined
      });
      Tips.success(
        action === 'APPROVE' ? t('skill.review.approveSuccess') : t('skill.review.rejectSuccess')
      );
      props.onReviewed(action);
    } catch (failure) {
      const body = apiErrorBody(failure);
      setError(body?.msg ?? t('skill.review.actionFailed'));
    } finally {
      setSubmitting(false);
      setSubmittingAction(null);
    }
  };

  return (
    <div data-testid="skill-review-panel">
      <div className="detail-section-title">{t('skill.review.artifactSummary')}</div>
      <DetailGrid
        items={[
          { label: t('skill.columns.source'), value: <SkillSourceTag source={artifact.source} /> },
          { label: t('skill.columns.author'), value: props.skill.author ?? '-' },
          { label: t('skill.columns.currentVersion'), value: artifact.version },
          { label: t('skill.artifact.checksum'), value: artifact.checksum, fullWidth: true },
          { label: t('skill.artifact.packageSize'), value: sizeLabel(artifact.package_size) },
          {
            label: t('skill.review.validation'),
            value: artifact.validation_message
              ? `${artifact.validation_status} · ${artifact.validation_message}`
              : artifact.validation_status
          },
          {
            label: t('skill.review.fileCountLabel'),
            value: artifact.manifest.file_count ?? artifact.manifest.files?.length ?? 0
          },
          {
            label: t('skill.columns.reviewStatus'),
            value: <SkillReviewStatusTag status={artifact.review_status} />
          },
          { label: t('skill.review.submitter'), value: artifact.submitted_by_user_id ?? '-' },
          {
            label: t('skill.review.submittedChannel'),
            value: artifact.submitted_channel ?? '-'
          },
          {
            label: t('skill.review.submittedAgent'),
            value: artifact.submitted_agent_id ?? t('skill.review.submittedAgentEmpty')
          },
          {
            label: t('skill.review.existingNote'),
            value: artifact.review_note ?? '-',
            fullWidth: true
          },
          { label: t('skill.review.reviewedBy'), value: artifact.reviewed_by ?? '-' },
          {
            label: t('skill.review.reviewedAt'),
            value: artifact.reviewed_at ? <DateTimeText value={artifact.reviewed_at} /> : '-'
          }
        ]}
      />
      <SkillFileList manifest={artifact.manifest} />
      <SkillReviewActions
        artifact={artifact}
        canReview={props.canReview}
        submitting={submitting}
        submittingAction={submittingAction}
        error={error}
        onApprove={(note) => void submit('APPROVE', note)}
        onReject={(note) => void submit('REJECT', note)}
      />
    </div>
  );
}
