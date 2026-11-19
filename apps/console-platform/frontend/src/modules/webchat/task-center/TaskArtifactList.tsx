/**
 * 任务产物列表（design frontend §3.3 CMP-20、§3.6、§3.9 API-25；TASK-027）。
 *
 * 展示产物不可变版本引用（name/media_type/size/version/checksum），下载按钮只把 artifact
 * 原样交给注入的控制器——本组件不拼 URL、不发请求、不读 Cookie。每次下载都带
 * `artifact_id + version + checksum`（校验值展示给用户核对）。
 *
 * 失败与成功是两个显式状态：失败显示稳定错误码对应的文案（未知码追加原始 code），
 * 不留「看起来像保存成功」的模糊态。
 */

import { Button, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type { TaskArtifact } from '../types';
import {
  artifactErrorKey,
  artifactErrorNeedsCode,
  IDLE_ARTIFACT_DOWNLOAD,
  type ArtifactDownloadState
} from './artifactDownload';
import { DeliveryBadge, DeliveryNone } from './backgroundTaskLabels';

/** 字节数只做二进制单位的可读化；未知/零大小显示 0 B，不猜内容。 */
export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = unit === 0 ? String(Math.trunc(value)) : value.toFixed(1);
  return `${rounded} ${units[unit]}`;
}

export interface TaskArtifactListProps {
  artifacts: readonly TaskArtifact[];
  state: ArtifactDownloadState;
  onDownload(artifact: TaskArtifact): void;
}

export function TaskArtifactList(props: TaskArtifactListProps) {
  const { t } = useTranslation();
  if (props.artifacts.length === 0) {
    return (
      <p className="chat-task-artifacts-empty" data-testid="chat-task-artifacts-empty">
        {t('chat.taskCenter.artifact.empty')}
      </p>
    );
  }
  return (
    <ul className="chat-task-artifacts" data-testid="chat-task-artifacts">
      {props.artifacts.map((artifact) => {
        const entry = props.state.entries[artifact.artifact_id] ?? IDLE_ARTIFACT_DOWNLOAD;
        return (
          <li
            key={`${artifact.artifact_id}:${artifact.version}:${artifact.checksum}`}
            className="chat-task-artifact"
            data-testid="chat-task-artifact"
            data-artifact-id={artifact.artifact_id}
            data-artifact-version={artifact.version}
            data-artifact-checksum={artifact.checksum}
            data-download-phase={entry.phase}
          >
            <div className="chat-task-artifact-main">
              <Typography.Text strong>{artifact.name}</Typography.Text>
              <span className="chat-task-artifact-meta">
                {artifact.media_type} · {formatBytes(artifact.size)}
              </span>
            </div>
            <dl className="chat-task-artifact-facts">
              <div>
                <dt>{t('chat.taskCenter.artifact.version')}</dt>
                <dd data-testid="chat-task-artifact-version">{artifact.version}</dd>
              </div>
              <div>
                <dt>{t('chat.taskCenter.artifact.checksum')}</dt>
                <dd>
                  <code data-testid="chat-task-artifact-checksum">{artifact.checksum}</code>
                </dd>
              </div>
            </dl>
            {artifact.deliveries === undefined ? null : (
              <div
                className="chat-task-artifact-deliveries"
                data-testid="chat-task-artifact-deliveries"
              >
                {artifact.deliveries.length === 0 ? (
                  <DeliveryNone />
                ) : (
                  artifact.deliveries.map((delivery, index) => (
                    <DeliveryBadge
                      key={`${delivery.target_key}:${index}`}
                      delivery={delivery}
                    />
                  ))
                )}
              </div>
            )}
            <div className="chat-task-artifact-actions">
              <Button
                size="small"
                theme="light"
                data-testid="chat-task-artifact-download"
                aria-label={t('chat.taskCenter.artifact.download', { name: artifact.name })}
                loading={entry.phase === 'DOWNLOADING'}
                onClick={() => props.onDownload(artifact)}
              >
                {entry.phase === 'DOWNLOADING'
                  ? t('chat.taskCenter.artifact.downloading')
                  : t('chat.taskCenter.artifact.downloadShort')}
              </Button>
              {entry.phase === 'SAVED' ? (
                <span
                  className="chat-task-artifact-saved"
                  data-testid="chat-task-artifact-saved"
                >
                  {t('chat.taskCenter.artifact.saved')}
                </span>
              ) : null}
              {entry.phase === 'FAILED' ? (
                <span
                  className="chat-task-artifact-error"
                  data-testid="chat-task-artifact-error"
                  role="alert"
                >
                  {t(artifactErrorKey(entry.errorCode))}
                  {artifactErrorNeedsCode(entry.errorCode)
                    ? t('chat.taskCenter.error.codeSuffix', { code: entry.errorCode })
                    : null}
                </span>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
