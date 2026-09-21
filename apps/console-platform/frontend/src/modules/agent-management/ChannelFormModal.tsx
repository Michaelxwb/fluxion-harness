import { Banner, Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { FormModal } from '../../components/common/FormModal';
import {
  addAgentChannel,
  updateAgentChannel,
  type AgentChannelItem
} from './services/agents';

export interface ChannelFormModalProps {
  visible: boolean;
  agentId: string;
  channel: AgentChannelItem | null;
  onCancel(): void;
  onSaved(): void;
}

interface ChannelFormValues {
  name: string;
  bot_id: string;
  secret?: string;
  enabled: 'true' | 'false';
  config?: string;
}

export function ChannelFormModal(props: ChannelFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    setFormError(null);
    if (props.channel) {
      formApi.current?.setValues({
        name: props.channel.name,
        bot_id: props.channel.bot_id,
        enabled: props.channel.enabled ? 'true' : 'false',
        config: JSON.stringify(props.channel.config ?? {}, null, 2)
      });
    } else {
      formApi.current?.setValues({ enabled: 'true', config: '{}' });
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
    setSaving(true);
    setFormError(null);
    try {
      if (props.channel) {
        const input: Record<string, unknown> = {
          name: values.name,
          bot_id: values.bot_id,
          enabled: values.enabled === 'true',
          config
        };
        if (values.secret) {
          input.secret = values.secret;
        }
        await updateAgentChannel(props.agentId, props.channel.channel_account_id, input);
      } else {
        await addAgentChannel(props.agentId, {
          channel: 'WECOM',
          name: values.name,
          bot_id: values.bot_id,
          secret: values.secret,
          enabled: values.enabled === 'true',
          config
        });
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
      width={560}
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
        <Form.Input
          field="name"
          label={t('agent.channel.name')}
          rules={[{ required: true, message: t('agent.channel.name') }]}
        />
        <Form.Input
          field="bot_id"
          label={t('agent.channel.botId')}
          rules={[{ required: true, message: t('agent.channel.botId') }]}
        />
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
