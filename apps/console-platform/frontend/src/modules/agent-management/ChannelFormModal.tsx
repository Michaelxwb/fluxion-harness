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

/** 外部断言平台的一个可建入口通道（来自 `/external-platforms`，配置驱动）。 */
export interface ExternalChannelOption {
  /** 通道名（平台 `channel` 字段，如 `MSSWWEB`）。 */
  channel: string;
  /** 展示名（平台 `display_name`）。 */
  label: string;
}

export interface ChannelFormModalProps {
  visible: boolean;
  agentId: string;
  channel: AgentChannelItem | null;
  /** 外部断言平台通道（配置驱动）；缺省等价于只有内置三通道。 */
  externalChannels?: ExternalChannelOption[];
  onCancel(): void;
  onSaved(): void;
}

// 通道名不再是封闭字面量：外部平台可以配出任意新通道（情况 A：与 MSSWWEB 同形态）。
type ChannelKind = string;

const DEFAULT_CHANNEL = 'WECOM';
const WEBCHAT_CHANNEL = 'WEBCHAT';
const MSSWWEB_CHANNEL = 'MSSWWEB';
const WECOM_CHANNEL = 'WECOM';

/**
 * 内置的无 Bot Secret 通道：WEBCHAT（稳定入口标识）与 MSSWWEB（外部断言）。
 * MSSWWEB **必须常驻**这里——它虽是外部断言平台，但也是内置通道，`externalChannels`
 * 只承载「新增的外部平台」，不保证包含 MSSWWEB（调用方通常按内置项去重）。
 * 其它无 secret 通道（新增外部平台）由 `isSecretless` 结合 `externalChannels` 判定。
 * 后端 `secretless_channels()` 是同一条界线，两边必须一致——给这些通道提交 secret 会被拒。
 */
const BUILTIN_SECRETLESS_CHANNELS: ReadonlySet<string> = new Set([
  WEBCHAT_CHANNEL,
  MSSWWEB_CHANNEL
]);

/** `bot_id` 在无 secret 的通道里是「入口标识」，有 secret 的通道里是平台侧的 Bot ID。 */
function isSecretless(kind: ChannelKind, externalChannels: ExternalChannelOption[]): boolean {
  if (BUILTIN_SECRETLESS_CHANNELS.has(kind)) {
    return true;
  }
  return externalChannels.some((item) => item.channel === kind);
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
 * 把服务端返回的 channel 归一到可选值。
 *
 * 已存在入口的通道名原样保留（可能是外部平台通道）；只有空值时回退默认。
 * 不能用「非 WEBCHAT 即 WECOM」的兜底：那样编辑一个已存在的 MSSWWEB/外部通道入口时，
 * 会被当成 WECOM 而多出一个 Secret 输入框，提交上去还会被后端拒绝。
 */
function channelKindOf(channel: AgentChannelItem | null): ChannelKind {
  const raw = channel?.channel;
  return raw && raw.trim() ? raw : DEFAULT_CHANNEL;
}

export function ChannelFormModal(props: ChannelFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [channelKind, setChannelKind] = useState<ChannelKind>(DEFAULT_CHANNEL);
  const formApi = useRef<FormApi | null>(null);
  const externalChannels = props.externalChannels ?? [];
  const secretless = isSecretless(channelKind, externalChannels);

  // 通道下拉：内置三通道 + 外部平台通道。外部通道若与内置同名（MSSWWEB）只保留内置项，
  // 避免重复；展示名优先用平台 `display_name`（去重时内置 label 已覆盖）。
  const builtinOptions: { value: string; label: string }[] = [
    { value: WECOM_CHANNEL, label: t('agent.channel.channelWecom') },
    { value: WEBCHAT_CHANNEL, label: t('agent.channel.channelWebchat') },
    { value: MSSWWEB_CHANNEL, label: t('agent.channel.channelMsswweb') }
  ];
  const builtinValues = new Set(builtinOptions.map((o) => o.value));
  const channelOptions = [
    ...builtinOptions,
    ...externalChannels
      .filter((item) => !builtinValues.has(item.channel))
      .map((item) => ({ value: item.channel, label: item.label || item.channel }))
  ];

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
        // 无 secret 的通道（网页入口、外部断言平台通道）不提交 secret——后端会显式拒绝。
        if (!isSecretless(channel, externalChannels) && values.secret) {
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
        if (!isSecretless(channel, externalChannels)) {
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
          optionList={channelOptions}
          onChange={(value) => setChannelKind(value as ChannelKind)}
        />
        <Form.Input
          field="name"
          label={t('agent.channel.name')}
          rules={[{ required: true, message: t('agent.channel.name') }]}
        />
        <Form.Input
          field="bot_id"
          label={secretless ? t('agent.channel.entryKey') : t('agent.channel.botId')}
          extraText={secretless ? t('agent.channel.entryKeyHint') : undefined}
          rules={[
            {
              required: true,
              message: secretless ? t('agent.channel.entryKey') : t('agent.channel.botId')
            }
          ]}
        />
        {!secretless ? (
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
