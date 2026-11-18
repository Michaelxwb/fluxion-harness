/**
 * 外部 IM 平台身份治理：录入/改删外部身份（platform_key 驱动动态字段）、按入口授权（entry）、
 * 平台未配置时给出 Banner 提示。数据归属为用户，入口只作授权维度（设计 §外部身份）。
 */

import { Banner, Button, Form, Input, Spin, Tag } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { IconPlus } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import i18n from '../../i18n';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import type {
  ExternalEntryItem,
  ExternalIdentityItem,
  ExternalPlatformItem
} from './services/users';
import {
  deleteExternalIdentity,
  grantExternalEntry,
  listExternalIdentities,
  listExternalPlatforms,
  listPlatformEntries,
  revokeExternalEntry,
  updateExternalIdentity,
  upsertExternalIdentity
} from './services/users';

/** 录入表单里「平台」字段的 field 名。与 identity_keys 用一个下划线前缀隔开，避免撞名。 */
const PLATFORM_FIELD = '_platform_key';

/** 卡片上展示平台名：优先 display_name，取不到回落到 key。 */
function platformName(platformKey: string, platforms: ExternalPlatformItem[]): string {
  const found = platforms.find((item) => item.platform_key === platformKey);
  return found?.display_name || platformKey;
}

export interface ExternalIdentitySectionProps {
  userId: string;
  canManage: boolean;
  onMutated?(): void;
}

export function ExternalIdentitySection(props: ExternalIdentitySectionProps) {
  const { t } = useTranslation();
  const [platforms, setPlatforms] = useState<ExternalPlatformItem[]>([]);
  const [identities, setIdentities] = useState<ExternalIdentityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [editing, setEditing] = useState<ExternalIdentityItem | null>(null);
  const [identityModal, setIdentityModal] = useState<{
    visible: boolean;
    /** 录入时由表单选择（初始为第一个已配置平台）；编辑时为该身份所属平台，不可改。 */
    platformKey: string;
  }>({ visible: false, platformKey: '' });
  const [grantModal, setGrantModal] = useState<{ visible: boolean; identity: ExternalIdentityItem | null }>({
    visible: false,
    identity: null
  });
  const [submitting, setSubmitting] = useState(false);
  const identityFormApi = useRef<FormApi | null>(null);

  const platform = useMemo(
    () => platforms.find((item) => item.platform_key === identityModal.platformKey),
    [platforms, identityModal.platformKey]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const [platformList, identityList] = await Promise.all([
        listExternalPlatforms(),
        listExternalIdentities(props.userId)
      ]);
      setPlatforms(platformList);
      setIdentities(identityList);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [props.userId]);

  useEffect(() => {
    void load();
  }, [load]);

  /** 可录入的平台：只列已配置的（未配置的平台没有验签因子，录了也用不了）。 */
  const configuredPlatforms = useMemo(
    () => platforms.filter((item) => item.configured),
    [platforms]
  );

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    // 无兜底：没有已配置平台就不该打开录入框（按钮此时已禁用）。
    setIdentityModal({ visible: true, platformKey: configuredPlatforms[0]?.platform_key ?? '' });
  };

  const openEdit = (item: ExternalIdentityItem) => {
    setEditing(item);
    setFormError(null);
    setIdentityModal({ visible: true, platformKey: item.platform_key });
  };

  const submitIdentity = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    setFormError(null);
    // 以表单里选中的平台为准：platformKey 在录入时随选择变化，不能只认 state。
    const selectedKey = String(values[PLATFORM_FIELD] ?? identityModal.platformKey);
    const selected = platforms.find((item) => item.platform_key === selectedKey);
    if (!selected) {
      setFormError(i18n.t('user.external.platformRequired'));
      setSubmitting(false);
      return;
    }
    try {
      const identity: Record<string, string> = {};
      for (const key of selected.identity_keys) {
        identity[key] = String(values[key] ?? '').trim();
      }
      if (editing) {
        await updateExternalIdentity(props.userId, editing.identity_id, { identity });
      } else {
        await upsertExternalIdentity(props.userId, {
          platform_key: selected.platform_key,
          identity
        });
      }
      setIdentityModal({ visible: false, platformKey: selected.platform_key });
      await load();
      props.onMutated?.();
    } catch (error) {
      const body = apiErrorBody(error);
      setFormError(body?.msg ?? i18n.t('user.external.submitFailed'));
    } finally {
      setSubmitting(false);
    }
  };

  const removeIdentity = async (item: ExternalIdentityItem) => {
    await deleteExternalIdentity(props.userId, item.identity_id);
    await load();
    props.onMutated?.();
  };

  const revokeGrant = async (item: ExternalIdentityItem, entryId: string) => {
    await revokeExternalEntry(props.userId, item.identity_id, entryId);
    await load();
    props.onMutated?.();
  };

  if (loading && identities.length === 0 && platforms.length === 0) {
    return <Spin />;
  }
  if (failed) {
    return <ErrorState onRetry={() => void load()} />;
  }

  const unconfigured = platforms.filter((item) => !item.configured);

  return (
    <div className="user-external-identity">
      {unconfigured.map((item) => (
        <Banner
          key={item.platform_key}
          type="warning"
          closeIcon={null}
          description={t('user.external.platformUnconfigured', {
            platform: item.display_name || item.platform_key
          })}
        />
      ))}

      {props.canManage && (
        <div className="user-external-identity__toolbar">
          <Button
            icon={<IconPlus />}
            theme="solid"
            disabled={configuredPlatforms.length === 0}
            onClick={openCreate}
          >
            {t('user.external.add')}
          </Button>
        </div>
      )}

      {identities.length === 0 ? (
        <EmptyState title={t('user.external.empty')} />
      ) : (
        <div className="user-external-identity__list">
          {identities.map((item) => (
            <div key={item.identity_id} className="user-external-identity__card">
              <div className="user-external-identity__head">
                <strong>{platformName(item.platform_key, platforms)}</strong>
                <Tag color={item.status === 'ACTIVE' ? 'green' : 'grey'}>{item.status}</Tag>
              </div>
              <div className="user-external-identity__fields">
                {Object.entries(item.identity).map(([key, value]) => (
                  <span key={key} className="user-external-identity__field">
                    {key}: {value}
                  </span>
                ))}
              </div>
              <div className="user-external-identity__grants">
                {item.grants.length === 0 ? (
                  <span className="detail-hint">{t('user.external.noGrants')}</span>
                ) : (
                  item.grants.map((grant) => (
                    <Tag
                      key={grant.entry_id}
                      closable={props.canManage}
                      onClose={() => void revokeGrant(item, grant.entry_id)}
                    >
                      {grant.name || grant.bot_id}
                    </Tag>
                  ))
                )}
              </div>
              {props.canManage && (
                <div className="user-external-identity__actions">
                  <Button size="small" onClick={() => setGrantModal({ visible: true, identity: item })}>
                    {t('user.external.grantEntry')}
                  </Button>
                  <Button size="small" onClick={() => openEdit(item)}>
                    {t('common.edit')}
                  </Button>
                  <ConfirmAction
                    type="danger"
                    title={t('user.external.confirmDelete')}
                    onConfirm={() => void removeIdentity(item)}
                  >
                    <Button size="small" type="danger">
                      {t('common.delete')}
                    </Button>
                  </ConfirmAction>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <FormModal
        key={`identity-${editing?.identity_id ?? 'new'}-${identityModal.platformKey}`}
        visible={identityModal.visible}
        title={editing ? t('user.external.editTitle') : t('user.external.addTitle')}
        confirmLoading={submitting}
        okButtonProps={{ disabled: !platform?.configured }}
        getFormApi={(api) => {
          identityFormApi.current = api;
        }}
        initValues={
          editing
            ? { ...editing.identity, [PLATFORM_FIELD]: editing.platform_key }
            : { [PLATFORM_FIELD]: identityModal.platformKey }
        }
        onOk={() => identityFormApi.current?.submitForm()}
        onCancel={() => setIdentityModal({ visible: false, platformKey: identityModal.platformKey })}
        onSubmit={(values) => void submitIdentity(values)}
      >
        {/* 平台必须先选：多平台并存时，身份字段随平台变化，不能隐式取第一个。 */}
        <Form.Select
          field={PLATFORM_FIELD}
          label={t('user.external.platform')}
          disabled={Boolean(editing)}
          rules={[{ required: true, message: t('user.external.platformRequired') }]}
          optionList={configuredPlatforms.map((item) => ({
            value: item.platform_key,
            label: item.display_name || item.platform_key
          }))}
          onChange={(value) => {
            // 换平台后字段集合不同，重置表单并重新挂载，避免上一个平台的字段残留。
            setIdentityModal({
              visible: true,
              platformKey: String(value ?? '')
            });
          }}
        />
        {(platform?.identity_keys ?? []).map((key) => (
          <Form.Input
            key={key}
            field={key}
            label={platform?.identity_field_labels[key] ?? key}
            rules={[{ required: true, message: t('user.external.fieldRequired') }]}
          />
        ))}
        {formError && (
          <div className="detail-hint" data-testid="external-identity-form-error">
            {formError}
          </div>
        )}
      </FormModal>

      <GrantEntryModal
        visible={grantModal.visible}
        platformKey={grantModal.identity?.platform_key ?? ''}
        userId={props.userId}
        granted={new Set((grantModal.identity?.grants ?? []).map((grant) => grant.entry_id))}
        onCancel={() => setGrantModal({ visible: false, identity: grantModal.identity })}
        onGrant={async (entryId) => {
          if (!grantModal.identity) return;
          await grantExternalEntry(props.userId, grantModal.identity.identity_id, entryId);
          await load();
          props.onMutated?.();
        }}
      />
    </div>
  );
}

interface GrantEntryModalProps {
  visible: boolean;
  platformKey: string;
  userId: string;
  granted: Set<string>;
  onCancel(): void;
  onGrant(entryId: string): Promise<void>;
}

function GrantEntryModal(props: GrantEntryModalProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [entries, setEntries] = useState<ExternalEntryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 服务端分页/搜索：前端不做本地过滤。
      const result = await listPlatformEntries(props.platformKey, {
        user_id: props.userId,
        query,
        page: 1,
        page_size: 20
      });
      setEntries(result);
    } catch (cause) {
      setError(apiErrorBody(cause)?.msg ?? i18n.t('user.external.entriesFailed'));
    } finally {
      setLoading(false);
    }
  }, [props.platformKey, props.userId, query]);

  useEffect(() => {
    if (props.visible) {
      void search();
    }
  }, [props.visible, search]);

  return (
    <FormModal
      visible={props.visible}
      title={t('user.external.grantEntry')}
      width={640}
      okText={t('common.close')}
      onOk={props.onCancel}
      onCancel={props.onCancel}
      getFormApi={() => undefined}
    >
      <Input
        value={query}
        placeholder={t('user.external.searchEntry')}
        onChange={setQuery}
        onEnterPress={() => void search()}
      />
      {error && <div className="detail-hint">{error}</div>}
      {loading ? (
        <Spin />
      ) : entries.length === 0 ? (
        <EmptyState title={t('user.external.noEntries')} />
      ) : (
        <ul className="user-external-identity__entries">
          {entries.map((entry) => (
            <li key={entry.entry_id}>
              <span>
                {entry.name || entry.bot_id} ({entry.channel})
              </span>
              {props.granted.has(entry.entry_id) ? (
                <Tag color="green">{t('user.external.granted')}</Tag>
              ) : (
                <Button size="small" onClick={() => void props.onGrant(entry.entry_id)}>
                  {t('user.external.grant')}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </FormModal>
  );
}
