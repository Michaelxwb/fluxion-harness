import { Banner, Button, Form, Select, Tag } from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { RemoteTable } from '../../components/common/RemoteTable';
import {
  CredentialSchemaFields,
  credentialFields,
  validateCredentialValues
} from './CredentialSchemaFields';
import {
  deleteSharedCredential,
  getAdapter,
  getSharedCredential,
  listPlatformCredentials,
  listUsers,
  saveSharedCredential,
  saveUserCredential,
  type AdapterMetadata,
  type CredentialState,
  type PlatformCredentialRow,
  type PlatformItem
} from './services/platforms';

export interface PlatformCredentialTabProps {
  platform: PlatformItem;
  onChanged?(): void;
}

function statusTag(status: string): { color: 'green' | 'red' | 'grey'; key: string } {
  if (status === 'ACTIVE') {
    return { color: 'green', key: 'platform.credentials.configured' };
  }
  if (status === 'INVALID') {
    return { color: 'red', key: 'platform.credentials.invalid' };
  }
  return { color: 'grey', key: 'platform.credentials.notConfigured' };
}

const DEFAULT_PAGE_SIZE = 10;

export function PlatformCredentialTab(props: PlatformCredentialTabProps) {
  const { t } = useTranslation();
  const [adapter, setAdapter] = useState<AdapterMetadata | null>(null);
  const [rows, setRows] = useState<PlatformCredentialRow[]>([]);
  const [users, setUsers] = useState<{ id: string; display_name: string; user_code: string }[]>([]);
  const [userSearchInput, setUserSearchInput] = useState('');
  const [userSearch, setUserSearch] = useState('');
  const [sharedCredential, setSharedCredential] = useState<CredentialState | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [targetUser, setTargetUser] = useState<string>('');
  const [formMode, setFormMode] = useState<'user' | 'shared' | null>(null);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    const [adapterResult, credentialsResult, sharedResult] = await Promise.allSettled([
      getAdapter(props.platform.adapter_key),
      listPlatformCredentials(props.platform.platform_id, { page, page_size: pageSize }),
      getSharedCredential(props.platform.platform_id)
    ]);
    if (current !== requestSeq.current) {
      return;
    }
    if (adapterResult.status === 'fulfilled') {
      setAdapter(adapterResult.value);
    }
    if (sharedResult.status === 'fulfilled') {
      setSharedCredential(sharedResult.value);
    }
    if (credentialsResult.status === 'fulfilled') {
      setRows(credentialsResult.value.items);
      setTotal(credentialsResult.value.total);
      setFailed(false);
    } else {
      setFailed(true);
    }
    setLoading(false);
  }, [page, pageSize, props.platform.adapter_key, props.platform.platform_id]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const timer = setTimeout(() => setUserSearch(userSearchInput), 300);
    return () => clearTimeout(timer);
  }, [userSearchInput]);

  useEffect(() => {
    if (formMode === null) {
      return;
    }
    let cancelled = false;
    listUsers({ page: 1, page_size: 20, keyword: userSearch || undefined })
      .then((pageResult) => {
        if (!cancelled) {
          setUsers(pageResult.items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUsers([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [formMode, userSearch]);

  const fields = credentialFields(adapter);

  const openUserForm = (userId = ''): void => {
    setTargetUser(userId);
    setValues({});
    setFormError(null);
    setFormMode('user');
  };

  const openSharedForm = (): void => {
    setTargetUser('');
    setValues({});
    setFormError(null);
    setFormMode('shared');
  };

  const closeForm = (): void => {
    setFormMode(null);
    setTargetUser('');
    setValues({});
    setFormError(null);
  };

  const save = async (): Promise<void> => {
    const invalid = validateCredentialValues(fields, values);
    if (invalid) {
      if (invalid.field) {
        formApi.current?.setError(invalid.field, t(invalid.key));
      } else {
        setFormError(t(invalid.key));
      }
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      if (formMode === 'user' && targetUser) {
        await saveUserCredential(props.platform.platform_id, targetUser, values);
      } else if (formMode === 'shared') {
        await saveSharedCredential(props.platform.platform_id, values);
      }
      closeForm();
      await reload();
      props.onChanged?.();
    } catch (error) {
      const body = apiErrorBody(error);
      if (body?.code === 'COMMON_VALIDATION_ERROR' && fields.length > 0) {
        formApi.current?.setError(fields[0].name, body.msg);
      } else if (body?.msg) {
        setFormError(body.msg);
      } else {
        setFormError(t('common.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  const removeShared = async (): Promise<void> => {
    await deleteSharedCredential(props.platform.platform_id);
    await reload();
    props.onChanged?.();
  };

  return (
    <div>
      <Button
        theme="solid"
        icon={<IconPlus />}
        style={{ marginBottom: 12 }}
        onClick={() => openUserForm()}
      >
        {t('platform.credentials.configureUser')}
      </Button>
      <div className="detail-section-title">{t('platform.credentials.userSection')}</div>
      {failed ? (
        <ErrorState onRetry={() => void reload()} />
      ) : (
        <RemoteTable<PlatformCredentialRow>
          rowKey="user_id"
          loading={loading}
          dataSource={rows}
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          empty={t('platform.credentials.empty')}
          columns={[
            { title: t('platform.credentials.user'), dataIndex: 'display_name' },
            { title: t('platform.credentials.account'), dataIndex: 'user_code' },
            {
              title: t('platform.credentials.status'),
              dataIndex: 'credential_status',
              render: (value: string) => {
                const tag = statusTag(value);
                return <Tag color={tag.color}>{t(tag.key)}</Tag>;
              }
            },
            {
              title: t('platform.credentials.updatedTime'),
              dataIndex: 'updated_time',
              render: (value: string | null) => (value ? <DateTimeText value={value} /> : '-')
            },
            {
              title: t('platform.columns.action'),
              render: (_: unknown, entry: PlatformCredentialRow) => (
                <Button theme="borderless" onClick={() => openUserForm(entry.user_id)}>
                  {entry.credential_status === 'NONE'
                    ? t('platform.credentials.configure')
                    : t('platform.credentials.update')}
                </Button>
              )
            }
          ]}
        />
      )}

      <div className="detail-section-title" style={{ marginTop: 20 }}>
        {t('platform.credentials.sharedSection')}
      </div>
      <div className="credential-card">
        <div>
          <div className="credential-card-title">{t('platform.credentials.defaultShared')}</div>
          <div className="credential-card-hint">{t('platform.credentials.defaultSharedHint')}</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Tag color={sharedCredential?.configured ? 'green' : 'grey'}>
            {sharedCredential?.configured
              ? t('platform.credentials.configured')
              : t('platform.credentials.notConfigured')}
          </Tag>
          <Button theme="borderless" onClick={openSharedForm}>
            {t('platform.credentials.update')}
          </Button>
          {sharedCredential?.configured ? (
            <ConfirmAction
              danger
              title={t('platform.credentials.confirmDeleteShared')}
              onConfirm={() => void removeShared()}
            >
              {t('platform.credentials.deleteShared')}
            </ConfirmAction>
          ) : null}
        </div>
      </div>
      <div className="detail-hint">{t('platform.credentials.hint')}</div>

      <FormModal
        key={`${formMode ?? 'closed'}-${targetUser}`}
        visible={formMode !== null}
        width={520}
        title={
          formMode === 'shared'
            ? t('platform.credentials.sharedFormTitle')
            : t('platform.credentials.userFormTitle')
        }
        okText={t('common.save')}
        confirmLoading={saving}
        okButtonProps={formMode === 'user' ? { disabled: !targetUser } : undefined}
        getFormApi={(api) => {
          formApi.current = api;
        }}
        onOk={() => void save()}
        onCancel={closeForm}
      >
        {formError ? <Banner type="danger" closeIcon={null} description={formError} /> : null}
        {formMode === 'user' ? (
          <Form.Select
            field="user_id"
            label={t('platform.credentials.user')}
            initValue={targetUser || undefined}
            remote
            filter
            onSearch={setUserSearchInput}
            optionList={users.map((user) => ({
              value: user.id,
              label: `${user.display_name} · ${user.user_code}`
            }))}
            onChange={(value) => setTargetUser(String(value))}
            rules={[{ required: true, message: t('platform.credentials.user') }]}
          />
        ) : null}
        <CredentialSchemaFields
          adapter={adapter}
          values={values}
          onChange={(name, value) => setValues((prev) => ({ ...prev, [name]: value }))}
        />
        <div className="detail-hint">{t('platform.credentials.notEchoed')}</div>
      </FormModal>
    </div>
  );
}
