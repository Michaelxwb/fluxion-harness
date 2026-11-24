/**
 * 外部 IM 平台身份治理：录入/改删外部身份、按入口授权（entry）、
 * 平台未配置时给出 Banner 提示。数据归属为用户，入口只作授权维度（设计 §外部身份）。
 *
 * 录入方式：选平台后粘贴上游签发的 JWT claims JSON，前端只做「字段交集」校验——
 * 平台 `identity_keys` 里的每个键都必须在 JSON 中存在且为字符串，其它键忽略。
 * 这与服务端 `_validate_identity` 的松紧度一致：服务端要求字段集合**恰好**等于
 * `identity_keys`，所以前端先把 JSON 裁剪成只剩这些键再提交。
 */

import { Banner, Button, Form, Tag } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { IconPlus } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import i18n from '../../i18n';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DataTable } from '../../components/common/DataTable';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { TableText } from '../../components/common/TableText';
import { actionColumn } from '../../components/common/tableLayout';
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
/** 录入表单里「粘贴 JSON」字段的 field 名，同样避开 identity_keys。 */
const CLAIMS_FIELD = '_claims_json';

/** 卡片上展示平台名：优先 display_name，取不到回落到 key。 */
function platformName(platformKey: string, platforms: ExternalPlatformItem[]): string {
  const found = platforms.find((item) => item.platform_key === platformKey);
  return found?.display_name || platformKey;
}

/**
 * 把粘贴的 JWT claims 裁剪成平台要求的身份字段。
 *
 * 返回 `{ identity }` 或 `{ error }`：缺任何一个 `identity_keys` 里的键、
 * 或对应值不是字符串，都算不合法（与服务端 `_validate_identity` 对齐）。
 */
export function extractIdentityFromClaims(
  raw: string,
  identityKeys: string[]
):
  | { identity: Record<string, string> }
  | { error: 'invalidJson' | 'missingKeys'; missing: string[] } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { error: 'invalidJson', missing: [] };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { error: 'invalidJson', missing: [] };
  }
  const claims = parsed as Record<string, unknown>;
  const identity: Record<string, string> = {};
  const missing: string[] = [];
  for (const key of identityKeys) {
    const value = claims[key];
    if (typeof value !== 'string' || !value.trim()) {
      missing.push(key);
      continue;
    }
    identity[key] = value;
  }
  if (missing.length > 0) {
    return { error: 'missingKeys', missing };
  }
  return { identity };
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
      let identity: Record<string, string>;
      if (editing) {
        // 编辑时沿用同一套粘贴 JSON 流程，避免两套输入方式并存。
        const result = extractIdentityFromClaims(
          String(values[CLAIMS_FIELD] ?? ''),
          selected.identity_keys
        );
        if ('error' in result) {
          setFormError(claimsErrorText(result));
          setSubmitting(false);
          return;
        }
        identity = result.identity;
        await updateExternalIdentity(props.userId, editing.identity_id, { identity });
      } else {
        const result = extractIdentityFromClaims(
          String(values[CLAIMS_FIELD] ?? ''),
          selected.identity_keys
        );
        if ('error' in result) {
          setFormError(claimsErrorText(result));
          setSubmitting(false);
          return;
        }
        identity = result.identity;
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
    return null;
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

      <div className="detail-section-title">{t('user.tabs.external')}</div>
      {props.canManage && (
        <Button
          icon={<IconPlus />}
          theme="solid"
          style={{ marginBottom: 12 }}
          disabled={configuredPlatforms.length === 0}
          onClick={openCreate}
        >
          {t('user.external.add')}
        </Button>
      )}

      {identities.length === 0 ? (
        <EmptyState title={t('user.external.empty')} />
      ) : (
        <DataTable
          className="app-table-fit user-external-identity-table"
          scroll={{ x: '100%' }}
          dataSource={identities}
          rowKey="identity_id"
          pagination={false}
          columns={[
            {
              title: t('user.external.platform'),
              dataIndex: 'platform_key',
              width: 140,
              ellipsis: true,
              render: (value: string) => <TableText>{platformName(value, platforms)}</TableText>
            },
            {
              title: t('user.external.identityFields'),
              dataIndex: 'identity',
              width: 'auto',
              render: (value: Record<string, string>) => (
                <TableText>
                  {Object.entries(value)
                    .map(([key, val]) => `${key}: ${val}`)
                    .join('  ')}
                </TableText>
              )
            },
            {
              title: t('user.external.grantedEntries'),
              dataIndex: 'grants',
              width: 220,
              render: (grants: ExternalIdentityItem['grants'], item: ExternalIdentityItem) =>
                grants.length === 0 ? (
                  <span className="detail-hint">{t('user.external.noGrants')}</span>
                ) : (
                  grants.map((grant) => (
                    <Tag
                      key={grant.entry_id}
                      closable={props.canManage}
                      // Semi 的 onClose 首参是 Tag 内容（ReactNode），不是事件对象，
                      // 这里无需阻止默认行为，直接撤销即可。
                      onClose={() => void revokeGrant(item, grant.entry_id)}
                    >
                      {grant.name || grant.bot_id}
                    </Tag>
                  ))
                )
            },
            {
              title: t('user.columns.status'),
              dataIndex: 'status',
              width: 96,
              render: (value: string) => (
                <Tag color={value === 'ACTIVE' ? 'green' : 'grey'}>{value}</Tag>
              )
            },
            ...(props.canManage
              ? [
                  {
                    ...actionColumn(220),
                    title: t('user.columns.action'),
                    render: (_: unknown, item: ExternalIdentityItem) => (
                      <>
                        <Button
                          size="small"
                          onClick={() => setGrantModal({ visible: true, identity: item })}
                        >
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
                      </>
                    )
                  }
                ]
              : [])
          ]}
        />
      )}

      <FormModal
        key={`identity-${editing?.identity_id ?? 'new'}`}
        visible={identityModal.visible}
        title={editing ? t('user.external.editTitle') : t('user.external.addTitle')}
        confirmLoading={submitting}
        okButtonProps={{ disabled: !platform?.configured }}
        getFormApi={(api) => {
          identityFormApi.current = api;
        }}
        initValues={
          editing
            ? {
                [PLATFORM_FIELD]: editing.platform_key,
                // 编辑时预填裁剪后的 JSON，用户可在其上改动粘贴的内容。
                [CLAIMS_FIELD]: JSON.stringify(editing.identity, null, 2)
              }
            : { [PLATFORM_FIELD]: identityModal.platformKey, [CLAIMS_FIELD]: '' }
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
            const nextKey = String(value ?? '');
            // 换平台后字段集合不同，清空已粘贴的 claims 避免上一个平台的残留值；
            // 用 FormApi 重置而不是让 Modal 的 key 变化重挂——重挂会让弹窗闪一下。
            setIdentityModal({ visible: true, platformKey: nextKey });
            identityFormApi.current?.setValues({
              [PLATFORM_FIELD]: nextKey,
              [CLAIMS_FIELD]: ''
            });
            setFormError(null);
          }}
        />
        <div className="detail-hint" style={{ marginBottom: 8 }}>
          {t('user.external.claimsHint', {
            keys: (platform?.identity_keys ?? []).join(', ') || '-'
          })}
        </div>
        <Form.TextArea
          field={CLAIMS_FIELD}
          label={t('user.external.claimsJson')}
          placeholder={t('user.external.claimsPlaceholder')}
          autosize={{ minRows: 6, maxRows: 14 }}
          rules={[{ required: true, message: t('user.external.fieldRequired') }]}
        />
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
        identity={grantModal.identity}
        onCancel={() => setGrantModal({ visible: false, identity: grantModal.identity })}
        onChanged={async () => {
          await load();
          props.onMutated?.();
        }}
      />
    </div>
  );
}

/** 把 `extractIdentityFromClaims` 的错误映射成给用户看的中文提示。 */
function claimsErrorText(
  result: { error: 'invalidJson' | 'missingKeys'; missing: string[] }
): string {
  if (result.error === 'invalidJson') {
    return i18n.t('user.external.claimsInvalidJson');
  }
  return i18n.t('user.external.claimsMissingKeys', { keys: result.missing.join(', ') });
}

interface GrantEntryModalProps {
  visible: boolean;
  platformKey: string;
  userId: string;
  identity: ExternalIdentityItem | null;
  onCancel(): void;
  onChanged(): Promise<void>;
}

function GrantEntryModal(props: GrantEntryModalProps) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<ExternalEntryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyEntryId, setBusyEntryId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 不带搜索词：授权弹窗直接列出该平台全部入口，便于一眼看到已授权/未授权。
      // identity_id 决定 granted 的归属口径——撤销按身份精确删 grant，不传会跨身份串味。
      const result = await listPlatformEntries(props.platformKey, {
        user_id: props.userId,
        identity_id: props.identity?.identity_id,
        page: 1,
        page_size: 100
      });
      setEntries(result);
    } catch (cause) {
      setError(apiErrorBody(cause)?.msg ?? i18n.t('user.external.entriesFailed'));
    } finally {
      setLoading(false);
    }
  }, [props.platformKey, props.userId, props.identity?.identity_id]);

  useEffect(() => {
    if (props.visible) {
      void load();
    }
  }, [props.visible, load]);

  const toggle = async (entry: ExternalEntryItem) => {
    if (!props.identity) return;
    setBusyEntryId(entry.entry_id);
    setError(null);
    try {
      if (entry.granted) {
        await revokeExternalEntry(props.userId, props.identity.identity_id, entry.entry_id);
      } else {
        await grantExternalEntry(props.userId, props.identity.identity_id, entry.entry_id);
      }
      await load();
      await props.onChanged();
    } catch (cause) {
      setError(apiErrorBody(cause)?.msg ?? i18n.t('user.external.submitFailed'));
    } finally {
      setBusyEntryId(null);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      title={t('user.external.grantEntry')}
      width={680}
      okText={t('common.close')}
      onOk={props.onCancel}
      onCancel={props.onCancel}
      getFormApi={() => undefined}
    >
      {error && <div className="detail-hint">{error}</div>}
      {loading ? null : entries.length === 0 ? (
        <EmptyState title={t('user.external.noEntries')} />
      ) : (
        <DataTable
          className="app-table-fit"
          scroll={{ x: '100%' }}
          dataSource={entries}
          rowKey="entry_id"
          pagination={false}
          columns={[
            {
              title: t('user.external.entryName'),
              dataIndex: 'name',
              width: 'auto',
              ellipsis: true,
              render: (value: string, entry: ExternalEntryItem) => (
                <TableText>{value || entry.bot_id}</TableText>
              )
            },
            {
              title: t('user.identities.channel'),
              dataIndex: 'channel',
              width: 120,
              render: (value: string) => <TableText>{value}</TableText>
            },
            {
              title: t('user.external.agent'),
              dataIndex: 'agent_name',
              width: 'auto',
              ellipsis: true,
              render: (value: string) => <TableText>{value || '-'}</TableText>
            },
            {
              ...actionColumn(110),
              title: t('user.columns.action'),
              render: (_: unknown, entry: ExternalEntryItem) => (
                <Button
                  size="small"
                  type={entry.granted ? 'danger' : 'primary'}
                  loading={busyEntryId === entry.entry_id}
                  disabled={busyEntryId !== null && busyEntryId !== entry.entry_id}
                  onClick={() => void toggle(entry)}
                >
                  {entry.granted ? t('user.external.revoke') : t('user.external.grant')}
                </Button>
              )
            }
          ]}
        />
      )}
    </FormModal>
  );
}
