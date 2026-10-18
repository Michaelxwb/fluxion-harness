/**
 * 未认证的 Token 入口（design frontend §3.2/§3.3/§3.6；TASK-075）。
 *
 * 一个页面只有一个未认证视图：**单个 Token 输入框 + 进入聊天**。这里没有注册、密码或管理账号，
 * 也不接 Console 登录守卫——用户拿管理员签发的 Webchat Token 换独立 CHAT 会话（TASK-074 的认证
 * 状态机由页面编排调用，本组件只持有一次交换的入参）。
 *
 * 1. **凭据零留存**：Token 原文只在受控输入与一次 `onSubmit` 调用之间短暂存在，提交完成（成功或
 *    失败）即清空；不进 URL/持久存储/日志，提示文案只由稳定错误码映射得到（永不回显原文）。
 * 2. **提交防重复**：`runTokenAccessSubmit` 用同步单飞闸门 + 父级在飞标记挡住重复提交与空白输入，
 *    一次用户动作最多一次交换；失败会释放闸门以便用户重试。
 * 3. **过期重新进入**：`expired`/`revoked` 等状态由认证快照的 `errorCode` 原样传入（props.errorCode），
 *    本组件按 catalog 同名码给出本地化提示：到期/上下文变化引导重新输入有效 Token，
 *    无效/过期/撤销 Token 指向管理员；认证成功（`errorCode === null`）不显示任何提示。
 *
 * 展示组件只收 typed props 与事件；HTTP、Cookie、传输头与身份状态都在 `modules/webchat/services/`
 * 与 `api/` 里，本文件不碰。
 */

import { Banner, Button, Card, Input, Typography } from '@douyinfe/semi-ui';
import { useReducer, useState } from 'react';
import { useTranslation } from 'react-i18next';

/** Token 输入的临时状态：原文只在这里短暂停留，提交完成即清空。 */
export interface TokenAccessState {
  token: string;
  pending: boolean;
  failed: boolean;
}

export const INITIAL_TOKEN_ACCESS_STATE: TokenAccessState = {
  token: '',
  pending: false,
  failed: false
};

export type TokenAccessAction =
  | { type: 'change'; value: string }
  | { type: 'submit' }
  | { type: 'settle'; ok: boolean }
  | { type: 'clear' };

/** 输入状态机：`change` 记原文、`submit` 置在飞（幂等）、`settle` 完成清空、`clear` 复位。 */
export function tokenAccessReducer(
  state: TokenAccessState,
  action: TokenAccessAction
): TokenAccessState {
  switch (action.type) {
    case 'change':
      return { token: action.value, pending: state.pending, failed: false };
    case 'submit':
      return state.pending ? state : { token: state.token, pending: true, failed: false };
    case 'settle':
      return { token: '', pending: false, failed: !action.ok };
    case 'clear':
      return INITIAL_TOKEN_ACCESS_STATE;
  }
}

/** 稳定目录码 → 提示词条：键名与 `config/api-messages.yaml` 的 code 同名（RULE-i18n-001）。 */
export const TOKEN_ACCESS_ERROR_KEYS: Record<string, string> = {
  UNAUTHORIZED: 'chat.tokenAccess.error.UNAUTHORIZED',
  CHAT_SESSION_EXPIRED: 'chat.tokenAccess.error.CHAT_SESSION_EXPIRED',
  CHAT_SESSION_REVOKED: 'chat.tokenAccess.error.CHAT_SESSION_REVOKED',
  CHAT_TOKEN_INVALID: 'chat.tokenAccess.error.CHAT_TOKEN_INVALID',
  CHAT_TOKEN_EXPIRED: 'chat.tokenAccess.error.CHAT_TOKEN_EXPIRED',
  CHAT_TOKEN_REVOKED: 'chat.tokenAccess.error.CHAT_TOKEN_REVOKED',
  AUTH_CONTEXT_CHANGED: 'chat.tokenAccess.error.AUTH_CONTEXT_CHANGED',
  CHAT_AUTH_RATE_LIMITED: 'chat.tokenAccess.error.CHAT_AUTH_RATE_LIMITED',
  NETWORK_ERROR: 'chat.tokenAccess.error.NETWORK_ERROR'
};

export const TOKEN_ACCESS_FALLBACK_KEY = 'chat.tokenAccess.errorFallback';

/**
 * 提示词条：只吃稳定错误码（永不接收 Token 原文）。认证成功返回 `null`；未知码与父级未收敛的
 * 本地提交失败都落兜底词条，绝不让失败静默。
 */
export function tokenAccessNoticeKey(
  errorCode: string | null,
  submitFailed = false
): string | null {
  if (errorCode !== null) {
    return TOKEN_ACCESS_ERROR_KEYS[errorCode] ?? TOKEN_ACCESS_FALLBACK_KEY;
  }
  return submitFailed ? TOKEN_ACCESS_FALLBACK_KEY : null;
}

/** 同步单飞闸门：不受 React 渲染批处理影响，同一时刻只允许一次交换在飞。 */
export interface TokenAccessGate {
  tryEnter(): boolean;
  release(): void;
}

export function createTokenAccessGate(): TokenAccessGate {
  let held = false;
  return {
    tryEnter: () => {
      if (held) return false;
      held = true;
      return true;
    },
    release: () => {
      held = false;
    }
  };
}

export interface TokenAccessSubmitPorts {
  gate: TokenAccessGate;
  /** 父级的认证动作（页面编排接认证状态机的 `exchangeToken`）。 */
  submit(token: string): Promise<void> | void;
  /** 提交完成回调：组件据此清空输入原文并结束在飞态。 */
  settle(ok: boolean): void;
}

export interface TokenAccessSubmitInput {
  token: string;
  /** 父级在飞（props.submitting）或本地已在飞：都不再发起提交。 */
  busy: boolean;
}

/**
 * 提交编排（与渲染无关，可在 node 下真实执行）：父级在飞、空白输入或闸门已占用时不提交；
 * 被接受的提交恰调用一次 `submit`，无论成功失败都在完成时释放闸门并回调 `settle`。
 * 返回 `true` 表示本次提交已被接受。
 */
export function runTokenAccessSubmit(
  ports: TokenAccessSubmitPorts,
  input: TokenAccessSubmitInput
): boolean {
  const token = input.token.trim();
  if (input.busy || token === '' || !ports.gate.tryEnter()) return false;
  const settle = (ok: boolean): void => {
    ports.gate.release();
    ports.settle(ok);
  };
  Promise.resolve(ports.submit(token)).then(
    () => settle(true),
    () => settle(false)
  );
  return true;
}

export interface TokenAccessProps {
  submitting: boolean;
  errorCode: string | null;
  onSubmit(token: string): Promise<void> | void;
  /** 用户主动清空输入时通知父级（父级据此丢弃页面本地的临时提示）。 */
  onClear(): void;
}

const TOKEN_INPUT_ID = 'webchat-token-input';

/**
 * 未认证视图：粘贴管理员交付的 Token 进入聊天。提交成功/失败都由页面编排决定后续（认证成功即
 * 卸载本组件；失败时 `props.errorCode` 给出本地化提示），本组件不自动重试、不自动交换。
 */
export function TokenAccess(props: TokenAccessProps) {
  const { t } = useTranslation();
  const [state, dispatch] = useReducer(tokenAccessReducer, INITIAL_TOKEN_ACCESS_STATE);
  const [gate] = useState(createTokenAccessGate);
  const busy = state.pending || props.submitting;
  const noticeKey = tokenAccessNoticeKey(props.errorCode, state.failed);

  const change = (value: string): void => {
    dispatch({ type: 'change', value });
    if (value.trim() === '') props.onClear();
  };

  const submit = (): void => {
    const accepted = runTokenAccessSubmit(
      {
        gate,
        submit: (token) => props.onSubmit(token),
        settle: (ok) => dispatch({ type: 'settle', ok })
      },
      { token: state.token, busy }
    );
    if (accepted) dispatch({ type: 'submit' });
  };

  return (
    <div data-testid="chat-token-access">
      <Card bordered={false}>
        <Typography.Title heading={4}>{t('chat.tokenAccess.title')}</Typography.Title>
        <Typography.Paragraph>{t('chat.tokenAccess.subtitle')}</Typography.Paragraph>
        <label htmlFor={TOKEN_INPUT_ID}>{t('chat.tokenAccess.tokenLabel')}</label>
        <Input
          id={TOKEN_INPUT_ID}
          data-testid="chat-token-access-input"
          type="password"
          autoComplete="off"
          value={state.token}
          disabled={busy}
          placeholder={t('chat.tokenAccess.tokenPlaceholder')}
          onChange={change}
        />
        <Button
          htmlType="submit"
          type="primary"
          theme="solid"
          loading={busy}
          disabled={busy || state.token.trim() === ''}
          data-testid="chat-token-access-submit"
          onClick={submit}
        >
          {busy ? t('chat.tokenAccess.submitting') : t('chat.tokenAccess.submit')}
        </Button>
        {noticeKey ? (
          <div data-testid="chat-token-access-notice">
            <Banner type="warning" description={t(noticeKey)} />
          </div>
        ) : null}
      </Card>
    </div>
  );
}
