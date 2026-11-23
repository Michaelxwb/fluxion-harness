import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { CopyableText } from '../../components/common/CopyableText';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EntityLink } from '../../components/common/EntityLink';
import { StatusTag } from '../../components/common/StatusTag';
import { CANCELLABLE_TASK_STATUSES, taskStatusFallback, taskStatusOptions, taskTypeLabel, waitKindLabel } from './scheduleCatalog';
import type { TaskDetail, TaskSnapshotSummary } from './services/tasks';
import './taskDetail.css';

type Field = readonly [label: string, value: ReactNode];

export interface TaskBasicDetailsProps {
  detail: TaskDetail;
  exportAction?: ReactNode;
  onOpenSchedule?(id: string): void;
  onOpenRun?(id: string): void;
}

function Fields({ fields }: { fields: Field[] }) {
  return (
    <dl className="task-detail-fields">
      {fields.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function Snapshot({ snapshot }: { snapshot: TaskSnapshotSummary }) {
  const { t } = useTranslation();
  const fields: Field[] = [];
  if (snapshot.schema_version != null) fields.push([t('task.detail.snapshotSchema'), snapshot.schema_version]);
  if (snapshot.mode) fields.push([t('task.detail.snapshotMode'), taskTypeLabel(t, snapshot.mode)]);
  if (snapshot.memory_mode) {
    fields.push([t('task.detail.memoryMode'), t(`task.memory.${snapshot.memory_mode}`, { defaultValue: snapshot.memory_mode })]);
  }
  if (snapshot.allowlist.length) fields.push([t('task.detail.allowlist'), snapshot.allowlist.join(', ')]);
  if (!fields.length && !snapshot.budget) return null;
  return (
    <details className="task-detail-disclosure" data-testid="task-detail-snapshot">
      <summary>{t('task.detail.snapshot')}</summary>
      {fields.length ? <Fields fields={fields} /> : null}
      {snapshot.budget ? (
        <div className="task-detail-technical">
          <span>{t('task.detail.budget')}</span>
          <pre>{JSON.stringify(snapshot.budget, null, 2)}</pre>
        </div>
      ) : null}
    </details>
  );
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function SourceFields({ detail, onOpenSchedule, onOpenRun }: TaskBasicDetailsProps) {
  const { t } = useTranslation();
  const fields: Field[] = [[t('task.columns.triggerType'), t(`task.trigger.${detail.trigger_type}`)]];
  if (detail.agent_id) fields.push([t('task.columns.agent'), (
    <CopyableText display={detail.agent_name || shortId(detail.agent_id)} full={detail.agent_id} />
  )]);
  if (detail.actor_user_id) fields.push([t('task.columns.actorUser'), (
    <CopyableText display={detail.actor_name || shortId(detail.actor_user_id)} full={detail.actor_user_id} />
  )]);
  if (detail.skill_id) fields.push([t('task.columns.skill'), (
    <CopyableText display={detail.skill_name || detail.skill_key || shortId(detail.skill_id)} full={detail.skill_id} />
  )]);
  if (detail.trigger_type === 'SCHEDULED' && detail.schedule_id) {
    const id = detail.schedule_id;
    fields.push([t('task.columns.scheduleId'), onOpenSchedule ? (
      <EntityLink testId="task-detail-schedule" onClick={() => onOpenSchedule(id)}>{id}</EntityLink>
    ) : id]);
  }
  if (detail.source_run_id) {
    const id = detail.source_run_id;
    fields.push([t('task.columns.sourceRun'), onOpenRun ? (
      <EntityLink testId="task-detail-run" onClick={() => onOpenRun(id)}>{shortId(id)}</EntityLink>
    ) : shortId(id)]);
  }
  if (detail.scheduled_at) fields.push([t('task.detail.scheduledAt'), <DateTimeText value={detail.scheduled_at} />]);
  return <Fields fields={fields} />;
}

function TaskTimes({ detail }: Pick<TaskBasicDetailsProps, 'detail'>) {
  const { t } = useTranslation();
  const fields: Field[] = [
    [t('task.columns.createTime'), <DateTimeText value={detail.create_time} />],
    [t('task.columns.deadlineAt'), <span data-testid="task-detail-deadline"><DateTimeText value={detail.deadline_at} /></span>]
  ];
  if (detail.started_at) fields.push([t('task.columns.startedAt'), <DateTimeText value={detail.started_at} />]);
  if (detail.finished_at) fields.push([t('task.columns.finishedAt'), <DateTimeText value={detail.finished_at} />]);
  fields.push([t('task.columns.updateTime'), <DateTimeText value={detail.update_time} />]);
  return <Fields fields={fields} />;
}

function TaskNotices({ detail }: Pick<TaskBasicDetailsProps, 'detail'>) {
  const { t } = useTranslation();
  return (
    <>
      {detail.error_code || detail.error_message ? (
        <div className="task-detail-failure" data-testid="task-detail-error">
          <strong>{t('task.detail.error')}</strong>
          {detail.error_message ? <span>{detail.error_message}</span> : null}
          {detail.error_code ? <code>{detail.error_code}</code> : null}
        </div>
      ) : null}
      {detail.status === 'WAITING_EXTERNAL' ? (
        <div className="task-detail-notice" data-testid="task-detail-wait-kind">
          {t('task.detail.waitKind')} · {waitKindLabel(t, detail.wait_kind)}
        </div>
      ) : null}
      {detail.human ? (
        <div className="task-detail-notice" data-testid="task-detail-human">
          <strong>{t('task.detail.human')} · {t(detail.human.open ? 'task.detail.humanOpen' : 'task.detail.humanClosed')}</strong>
          {detail.human.question ? <span>{detail.human.question}</span> : null}
          {detail.human.deadline_at ? (
            <span>{t('task.detail.humanDeadline')} · <DateTimeText value={detail.human.deadline_at} /></span>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function TaskBasicDetails(props: TaskBasicDetailsProps) {
  const { detail } = props;
  const { t } = useTranslation();
  return (
    <div className="task-detail-overview">
      <div className="task-detail-identity">
        <div>
          <span className="task-detail-muted">{t('task.columns.taskId')}</span>
          <CopyableText display={detail.task_id} full={detail.task_id} />
        </div>
        <div className="task-detail-status">
          {taskTypeLabel(t, detail.task_type)}
          <StatusTag status={detail.status} options={taskStatusOptions(t)} fallback={taskStatusFallback(t, detail.status)} />
          {detail.cancel_requested && CANCELLABLE_TASK_STATUSES.includes(detail.status) ? (
            <span data-testid="task-cancel-requested">{t('task.cancel.requested')}</span>
          ) : null}
          {props.exportAction}
        </div>
      </div>
      <TaskNotices detail={detail} />
      <section className="task-detail-section">
        <h3>{t('task.detail.source')}</h3>
        <SourceFields {...props} />
      </section>
      <section className="task-detail-section">
        <h3>{t('task.detail.times')}</h3>
        <TaskTimes detail={detail} />
      </section>
      <Snapshot snapshot={detail.snapshot} />
    </div>
  );
}
