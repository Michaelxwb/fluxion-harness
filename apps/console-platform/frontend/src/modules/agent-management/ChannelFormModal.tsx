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

type ChannelKind = 'WECOM' | 'WEBCHAT';

const DEFAULT_CHANNEL: ChannelKind = 'WECOM';
const WEBCHAT_CHANNEL: ChannelKind = 'WEBCHAT';

interface ChannelFormValues {
  channel: ChannelKind;
  name: string;
  bot_id: string;
  secret?: string;
  enabled: 'true' | 'false';
  config?: string;
}

function channelKindOf(channel: AgentChannelItem | null): ChannelKind {
  return channel?.channel === WEBCHAT_CHANNEL ? WEBCHAT_CHANNEL : DEFAULT_CHANNEL;
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
    const channel = values.channel === WEBCHAT_CHANNEL ? WEBCHAT_CHANNEL : DEFAULT_CHANNEL;
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
        // 网页入口没有 Bot Secret，且后端显式拒绝为 WEBCHAT 提交 secret。
        if (channel === DEFAULT_CHANNEL && values.secret) {
          input.secret = values.secret;
        }
        await updateAgentChannel(props.agentId, props.channel.channel_account_id, input);
      } else {
        const input: ChannelSaveInput = {
          channel,
          name: values.name,
          bot_id: values.bot_id,
          enabled: values.enabled === 'true',
          config
        };
        if (channel === DEFAULT_CHANNEL) {
          input.secret = values.secret;
        }
        await addAgentChannel(props.agentId, input);
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
            { value: 'WEBCHAT', label: t('agent.channel.channelWebchat') }
          ]}
          onChange={(value) =>
            setChannelKind(value === WEBCHAT_CHANNEL ? WEBCHAT_CHANNEL : DEFAULT_CHANNEL)
          }
        />
        <Form.Input
          field="name"
          label={t('agent.channel.name')}
          rules={[{ required: true, message: t('agent.channel.name') }]}
        />
        <Form.Input
          field="bot_id"
          label={
            channelKind === WEBCHAT_CHANNEL ? t('agent.channel.entryKey') : t('agent.channel.botId')
          }
          extraText={
            channelKind === WEBCHAT_CHANNEL ? t('agent.channel.entryKeyHint') : undefined
          }
          rules={[
            {
              required: true,
              message:
                channelKind === WEBCHAT_CHANNEL
                  ? t('agent.channel.entryKey')
                  : t('agent.channel.botId')
            }
          ]}
        />
        {channelKind === DEFAULT_CHANNEL ? (
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
