import { Banner, Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { FormModal } from '../../components/common/FormModal';
import {
  addAgentChannel,
  updateAgentChannel,
  type AgentChannelItem,
  type ChannelSaveInput
} from './services/agents';

export interface ChannelFormModalProps {
  visible: boolean;
  agentId: string;
  channel: AgentChannelItem | null;
  onCancel(): void;
  onSaved(): void;
}

type ChannelKind = 'WECOM' | 'WEBCHAT' | 'MSSWWEB';

const DEFAULT_CHANNEL: ChannelKind = 'WECOM';
const WEBCHAT_CHANNEL: ChannelKind = 'WEBCHAT';
const MSSWWEB_CHANNEL: ChannelKind = 'MSSWWEB';

/**
 * 无 Bot Secret 的通道：入口靠平台侧身份断言（MSSWWEB）或稳定入口标识（WEBCHAT）。
 * 后端 `SECRETLESS_CHANNELS` 是同一条界线，两边必须一致——给这些通道提交 secret 会被拒。
 */
const SECRETLESS_CHANNELS: ReadonlySet<ChannelKind> = new Set([WEBCHAT_CHANNEL, MSSWWEB_CHANNEL]);

/** `bot_id` 在无 secret 的通道里是「入口标识」，有 secret 的通道里是平台侧的 Bot ID。 */
function isSecretless(kind: ChannelKind): boolean {
  return SECRETLESS_CHANNELS.has(kind);
}

interface ChannelFormValues {
  channel: ChannelKind;
  name: string;
  bot_id: string;
  secret?: string;
  enabled: 'true' | 'false';
  config?: string;
}

/**
 * 把服务端返回的 channel 归一到已知类型。
 *
 * 不能让未知值落到「非 WEBCHAT 即 WECOM」的兜底里：编辑一个已存在的 MSSWWEB 入口时，
 * 那样就会被当成 WECOM 而多出一个 Secret 输入框，提交上去还会被后端拒绝。
 */
function channelKindOf(channel: AgentChannelItem | null): ChannelKind {
  if (channel?.channel === WEBCHAT_CHANNEL) {
    return WEBCHAT_CHANNEL;
  }
  if (channel?.channel === MSSWWEB_CHANNEL) {
    return MSSWWEB_CHANNEL;
  }
  return DEFAULT_CHANNEL;
}

export function ChannelFormModal(props: ChannelFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [channelKind, setChannelKind] = useState<ChannelKind>(DEFAULT_CHANNEL);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    setFormError(null);
    const kind = channelKindOf(props.channel);
    setChannelKind(kind);
    if (props.channel) {
      formApi.current?.setValues({
        channel: kind,
        name: props.channel.name,
        bot_id: props.channel.bot_id,
        enabled: props.channel.enabled ? 'true' : 'false',
        config: JSON.stringify(props.channel.config ?? {}, null, 2)
      });
    } else {
      formApi.current?.setValues({ channel: DEFAULT_CHANNEL, enabled: 'true', config: '{}' });
    }
  }, [props.visible, props.channel]);

  const submit = async (values: ChannelFormValues): Promise<void> => {
    let config: Record<string, unknown> = {};
    const rawConfig = (values.config ?? '').trim();
    if (rawConfig) {
      try {
        const parsed: unknown = JSON.parse(rawConfig);
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('not an object');
        }
        config = parsed as Record<string, unknown>;
      } catch {
        formApi.current?.setError('config', t('agent.channel.configInvalid'));
        return;
      }
    }
    // 直接取用户选中值，不再做「非 WEBCHAT 即 WECOM」的归一：那会把 MSSWWEB 改写成 WECOM。
    const channel = values.channel;
    setSaving(true);
    setFormError(null);
    try {
      if (props.channel) {
        const input: Partial<ChannelSaveInput> = {
          name: values.name,
          bot_id: values.bot_id,
          enabled: values.enabled === 'true',
          config
        };
        // 无 secret 的通道（网页入口、MSSW 网页入口）不提交 secret——后端会显式拒绝。
        if (!isSecretless(channel) && values.secret) {
          input.secret = values.secret;
        }
        await updateAgentChannel(props.agentId, props.channel.channel_account_id, input, {
          errorTip: 'never'
        });
      } else {
        const input: ChannelSaveInput = {
          channel,
          name: values.name,
          bot_id: values.bot_id,
          enabled: values.enabled === 'true',
          config
        };
        if (!isSecretless(channel)) {
          input.secret = values.secret;
        }
        await addAgentChannel(props.agentId, input, { errorTip: 'never' });
      }
      props.onSaved();
    } catch (error) {
      const body = apiErrorBody(error);
      if (body?.code === 'BOT_ID_EXISTS') {
        formApi.current?.setError('bot_id', body.msg);
      } else if (body?.msg) {
        setFormError(body.msg);
      } else {
        setFormError(t('common.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={720}
      title={props.channel ? t('agent.channel.editTitle') : t('agent.channel.createTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as ChannelFormValues);
      }}
    >
      {formError ? <Banner type="danger" closeIcon={null} description={formError} /> : null}
      <div className="form-grid">
        <Form.Select
          field="channel"
          label={t('agent.channel.channel')}
          disabled={props.channel !== null}
          optionList={[
            { value: 'WECOM', label: t('agent.channel.channelWecom') },
            { value: 'WEBCHAT', label: t('agent.channel.channelWebchat') },
            { value: 'MSSWWEB', label: t('agent.channel.channelMsswweb') }
          ]}
          onChange={(value) => setChannelKind(value as ChannelKind)}
        />
        <Form.Input
          field="name"
          label={t('agent.channel.name')}
          rules={[{ required: true, message: t('agent.channel.name') }]}
        />
        <Form.Input
          field="bot_id"
          label={isSecretless(channelKind) ? t('agent.channel.entryKey') : t('agent.channel.botId')}
          extraText={isSecretless(channelKind) ? t('agent.channel.entryKeyHint') : undefined}
          rules={[
            {
              required: true,
              message: isSecretless(channelKind)
                ? t('agent.channel.entryKey')
                : t('agent.channel.botId')
            }
          ]}
        />
        {!isSecretless(channelKind) ? (
          <Form.Input
            field="secret"
            label={t('agent.channel.secret')}
            mode="password"
            rules={
              props.channel ? [] : [{ required: true, message: t('agent.channel.secret') }]
            }
            extraText={
              props.channel ? t('agent.channel.secretKeep') : t('agent.channel.secretHint')
            }
          />
        ) : null}
        <Form.Select
          field="enabled"
          label={t('agent.columns.enabled')}
          initValue="true"
          optionList={[
            { value: 'true', label: t('common.status.enabled') },
            { value: 'false', label: t('common.status.disabled') }
          ]}
        />
        <div className="form-field-full">
          <Form.Input
            field="config"
            label={t('agent.channel.config')}
            placeholder="{}"
            extraText={t('agent.channel.configHint')}
          />
        </div>
      </div>
      <div className="detail-hint">{t('agent.channel.notEchoed')}</div>
    </FormModal>
  );
}
