import { Button, Form, Select, Table, Tag } from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { FormModal } from '../../components/common/FormModal';
import {
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
}

interface CredentialField {
  name: string;
  label: string;
  secret: boolean;
}

function credentialFields(adapter: AdapterMetadata | null): CredentialField[] {
  const schema = adapter?.credential_schema as
    | { properties?: Record<string, { title?: string; 'x-secret'?: boolean }> }
    | undefined;
  return Object.entries(schema?.properties ?? {}).map(([name, definition]) => ({
    name,
    label: definition.title ?? name,
    secret: definition['x-secret'] === true
  }));
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

export function PlatformCredentialTab(props: PlatformCredentialTabProps) {
  const { t } = useTranslation();
  const [adapter, setAdapter] = useState<AdapterMetadata | null>(null);
  const [rows, setRows] = useState<PlatformCredentialRow[]>([]);
  const [users, setUsers] = useState<{ id: string; display_name: string; user_code: string }[]>([]);
  const [userSearch, setUserSearch] = useState('');
  const [sharedCredential, setSharedCredential] = useState<CredentialState | null>(null);
  const [loading, setLoading] = useState(false);
  const [targetUser, setTargetUser] = useState<string>('');
  const [formMode, setFormMode] = useState<'user' | 'shared' | null>(null);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [adapterMetadata, credentialPage, shared] = await Promise.all([
        getAdapter(props.platform.adapter_key),
        listPlatformCredentials(props.platform.platform_id, { page: 1, page_size: 100 }),
        getSharedCredential(props.platform.platform_id)
      ]);
      setAdapter(adapterMetadata);
      setRows(credentialPage.items);
      setSharedCredential(shared);
    } finally {
      setLoading(false);
    }
  }, [props.platform.platform_id, props.platform.adapter_key]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (formMode === null) {
      return;
    }
    listUsers({ page: 1, page_size: 20, keyword: userSearch || undefined })
      .then((page) => setUsers(page.items))
      .catch(() => setUsers([]));
  }, [formMode, userSearch]);

  const fields = credentialFields(adapter);

  const openUserForm = (userId = ''): void => {
    setTargetUser(userId);
    setValues({});
    setFormMode('user');
  };

  const openSharedForm = (): void => {
    setTargetUser('');
    setValues({});
    setFormMode('shared');
  };

  const closeForm = (): void => {
    setFormMode(null);
    setTargetUser('');
    setValues({});
  };

  const save = async (): Promise<void> => {
    setSaving(true);
    try {
      if (formMode === 'user' && targetUser) {
        await saveUserCredential(props.platform.platform_id, targetUser, values);
      } else if (formMode === 'shared') {
        await saveSharedCredential(props.platform.platform_id, values);
      }
      closeForm();
      await reload();
    } finally {
      setSaving(false);
    }
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
      <Table
        dataSource={rows}
        rowKey="user_id"
        loading={loading}
        pagination={false}
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
        okButtonProps={
          formMode === 'user' ? { disabled: !targetUser } : undefined
        }
        onOk={() => void save()}
        onCancel={closeForm}
      >
        {formMode === 'user' ? (
          <Form.Select
            field="user_id"
            label={t('platform.credentials.user')}
            initValue={targetUser || undefined}
            remote
            filter
            onSearch={setUserSearch}
            optionList={users.map((user) => ({
              value: user.id,
              label: `${user.display_name} · ${user.user_code}`
            }))}
            onChange={(value) => setTargetUser(String(value))}
            rules={[{ required: true, message: t('platform.credentials.user') }]}
          />
        ) : null}
        {fields.map((field) => (
          <Form.Input
            key={field.name}
            field={field.name}
            label={field.label}
            mode={field.secret ? 'password' : undefined}
            onChange={(value: string) => setValues((prev) => ({ ...prev, [field.name]: value }))}
          />
        ))}
        <div className="detail-hint">{t('platform.credentials.notEchoed')}</div>
      </FormModal>
    </div>
  );
}
