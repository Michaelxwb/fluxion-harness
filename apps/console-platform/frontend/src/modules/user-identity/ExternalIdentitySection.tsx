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

const DEFAULT_PLATFORM = 'mssw';

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
  const [identityModal, setIdentityModal] = useState<{ visible: boolean; platformKey: string }>({
    visible: false,
    platformKey: DEFAULT_PLATFORM
  });
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

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    const key = platforms[0]?.platform_key ?? DEFAULT_PLATFORM;
    setIdentityModal({ visible: true, platformKey: key });
  };

  const openEdit = (item: ExternalIdentityItem) => {
    setEditing(item);
    setFormError(null);
    setIdentityModal({ visible: true, platformKey: item.platform_key });
  };

  const submitIdentity = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    setFormError(null);
    try {
      const keys = platform?.identity_keys ?? [];
      const identity: Record<string, string> = {};
      for (const key of keys) {
        identity[key] = String(values[key] ?? '').trim();
      }
      if (editing) {
        await updateExternalIdentity(props.userId, editing.identity_id, { identity });
      } else {
        await upsertExternalIdentity(props.userId, {
          platform_key: identityModal.platformKey,
          identity
        });
      }
      setIdentityModal({ visible: false, platformKey: identityModal.platformKey });
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
          <Button icon={<IconPlus />} theme="solid" onClick={openCreate}>
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
                <strong>{item.platform_key}</strong>
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
        initValues={editing?.identity ?? {}}
        onOk={() => identityFormApi.current?.submitForm()}
        onCancel={() => setIdentityModal({ visible: false, platformKey: identityModal.platformKey })}
        onSubmit={(values) => void submitIdentity(values)}
      >
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
        platformKey={grantModal.identity?.platform_key ?? DEFAULT_PLATFORM}
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
