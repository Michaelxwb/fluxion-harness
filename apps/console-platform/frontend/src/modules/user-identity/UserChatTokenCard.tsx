import { Button, Typography } from '@douyinfe/semi-ui';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../auth/AuthContext';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { CopyButton } from '../../components/common/CopyButton';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { StatusTag, type StatusTagOption } from '../../components/common/StatusTag';
import {
  issueWebchatToken,
  revokeWebchatToken,
  webchatTokenErrorKey,
  webchatTokenExpectedRevision,
  type WebchatTokenIssued,
  type WebchatTokenMetadata
} from './services/users';

/**
 * 用户详情 Webchat Token 单例卡片（design frontend §3.2/§3.4/§3.7；backend API-03）。
 *
 * - **仅 ADMIN**：非 ADMIN 直接不渲染（「前端隐藏 + 后端 403 兜底」的前端一半）；
 * - **原文只在本次结果框**：签发/重置响应里的 `token` 进结果框，经公共 `CopyButton` 复制，
 *   关闭即清；读取面（元信息）不回明文，卡片也不自行读取，更不从任何响应里再取一次；
 * - **版本 CAS**：`expected_revision` 由 `webchatTokenExpectedRevision(metadata)` 从父级元信息
 *   派生（未配置=0 首签、已配置=最新签发行版本），生成/重置/撤销都携该版本；
 * - **重置/撤销走 ConfirmAction**：两者都会让该用户已签发的聊天登录会话失效，必须显式确认；
 * - **结果不明只重读元信息**：失败/冲突后只通知父级重新读取版本与状态，绝不自动再签一次
 *   （design §3.5「响应丢失后只 GET 刷新版本/状态」）。
 */

export interface UserChatTokenCardProps {
  userId: string;
  /** 父级读取的元信息（GET 只有状态/版本/时间，永不携带原文）。 */
  metadata: WebchatTokenMetadata;
  /** 变更后通知父级重新读取元信息：版本与状态一律以父级的读取结果为准。 */
  onChanged(): void;
}

type Translate = (key: string) => string;

/** 状态枚举 → StatusTag 的唯一映射处（本模块只有这一张 Token 卡片消费它）。 */
export function webchatTokenStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return {
    NOT_CONFIGURED: { color: 'grey', label: t('user.webchatToken.status.NOT_CONFIGURED') },
    ACTIVE: { color: 'green', label: t('user.webchatToken.status.ACTIVE') },
    EXPIRED: { color: 'amber', label: t('user.webchatToken.status.EXPIRED') },
    REVOKED: { color: 'red', label: t('user.webchatToken.status.REVOKED') }
  };
}

/**
 * 单飞写入：一次用户动作只发一次请求；失败/结果不明时只映射文案并让父级重读元信息，
 * 不在这里重发请求——重签必须由用户再次显式点击（design §3.5）。
 */
function useTokenMutation(onChanged: () => void) {
  const [pending, setPending] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const inFlight = useRef(false);

  const run = async (mutate: () => Promise<void>): Promise<void> => {
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    setPending(true);
    setErrorKey(null);
    try {
      await mutate();
    } catch (error) {
      setErrorKey(webchatTokenErrorKey(error));
      onChanged();
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  return { pending, errorKey, run };
}

/** 元信息只读投影：状态/版本与后端返回的真实签发、到期时间。 */
function WebchatTokenMeta(props: { metadata: WebchatTokenMetadata }) {
  const { t } = useTranslation();
  return (
    <DetailGrid
      items={[
        {
          label: t('user.webchatToken.statusLabel'),
          value: (
            <StatusTag status={props.metadata.status} options={webchatTokenStatusOptions(t)} />
          )
        },
        { label: t('user.webchatToken.revision'), value: `r${props.metadata.revision}` },
        {
          label: t('user.webchatToken.issuedAt'),
          value: props.metadata.issued_at ? <DateTimeText value={props.metadata.issued_at} /> : '-'
        },
        {
          label: t('user.webchatToken.expiresAt'),
          value: props.metadata.expires_at ? (
            <DateTimeText value={props.metadata.expires_at} />
          ) : (
            '-'
          )
        }
      ]}
    />
  );
}

/** 本次签发结果：原文只在这里出现一次，经公共 CopyButton 复制，关闭即清。 */
function WebchatTokenResult(props: { issued: WebchatTokenIssued; onClose(): void }) {
  const { t } = useTranslation();
  return (
    <>
      <div className="detail-toolbar" data-testid="webchat-token-issued">
        <Typography.Text code>{props.issued.token}</Typography.Text>
        <CopyButton value={props.issued.token} label={t('user.webchatToken.copy')} />
        <Button theme="borderless" onClick={props.onClose}>
          {t('user.webchatToken.closeResult')}
        </Button>
      </div>
      <div className="detail-hint">{t('user.webchatToken.onceHint')}</div>
    </>
  );
}

export function UserChatTokenCard(props: UserChatTokenCardProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const [issued, setIssued] = useState<WebchatTokenIssued | null>(null);
  const { pending, errorKey, run } = useTokenMutation(props.onChanged);
  const expectedRevision = webchatTokenExpectedRevision(props.metadata);

  if (!isAdmin) {
    return null;
  }

  const issue = (): void => {
    void run(async () => {
      setIssued(await issueWebchatToken(props.userId, expectedRevision));
      props.onChanged();
    });
  };

  const revoke = (): void => {
    void run(async () => {
      await revokeWebchatToken(props.userId, expectedRevision);
      // 撤销后原文立即不可见：已展示的凭据不再可用，结果框同步清空
      setIssued(null);
      props.onChanged();
    });
  };

  return (
    <div data-testid="webchat-token-card">
      <div className="detail-hint">{t('user.webchatToken.subtitle')}</div>
      <WebchatTokenMeta metadata={props.metadata} />
      <div className="detail-toolbar">
        {props.metadata.configured ? (
          <>
            <ConfirmAction
              title={t('user.webchatToken.confirmReset')}
              testId="webchat-token-reset"
              theme="light"
              onConfirm={issue}
            >
              {t('user.webchatToken.reset')}
            </ConfirmAction>
            <ConfirmAction
              title={t('user.webchatToken.confirmRevoke')}
              testId="webchat-token-revoke"
              onConfirm={revoke}
            >
              {t('user.webchatToken.revoke')}
            </ConfirmAction>
          </>
        ) : (
          <Button
            theme="solid"
            type="primary"
            disabled={pending}
            data-testid="webchat-token-issue"
            onClick={issue}
          >
            {t('user.webchatToken.issue')}
          </Button>
        )}
      </div>
      {props.metadata.configured ? null : (
        <div className="detail-hint">{t('user.webchatToken.notConfigured')}</div>
      )}
      {errorKey ? (
        <div className="detail-hint" data-testid="webchat-token-error">
          {t(errorKey)}
        </div>
      ) : null}
      {issued ? <WebchatTokenResult issued={issued} onClose={() => setIssued(null)} /> : null}
      <div className="detail-hint">{t('user.webchatToken.hint')}</div>
    </div>
  );
}
