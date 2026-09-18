import { Banner, Button, Form, Select, Spin, Table, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { FormModal } from '../../components/common/FormModal';
import {
  getAdapter,
  getSharedCredential,
  getUserCredential,
  listUsers,
  saveSharedCredential,
  saveUserCredential,
  type AdapterMetadata,
  type CredentialState,
  type PlatformItem
} from './services/platforms';

export interface PlatformCredentialTabProps {
  platform: PlatformItem;
}

interface UserOption {
  id: string;
  display_name: string;
  user_code: string;
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

export function PlatformCredentialTab(props: PlatformCredentialTabProps) {
  const { t } = useTranslation();
  const [adapter, setAdapter] = useState<AdapterMetadata | null>(null);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [selectedUser, setSelectedUser] = useState('');
  const [userCredential, setUserCredential] = useState<CredentialState | null>(null);
  const [sharedCredential, setSharedCredential] = useState<CredentialState | null>(null);
  const [loading, setLoading] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});

  const fields = useMemo(() => credentialFields(adapter), [adapter]);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [adapterMetadata, userPage, shared] = await Promise.all([
        getAdapter(props.platform.adapter_key),
        listUsers({ page: 1, page_size: 100 }),
        getSharedCredential(props.platform.platform_id)
      ]);
      setAdapter(adapterMetadata);
      setUsers(userPage.items);
      setSharedCredential(shared);
      if (selectedUser) {
        setUserCredential(await getUserCredential(props.platform.platform_id, selectedUser));
      }
    } finally {
      setLoading(false);
    }
  }, [props.platform.platform_id, props.platform.adapter_key, selectedUser]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const save = async (): Promise<void> => {
    setSaving(true);
    try {
      if (selectedUser) {
        await saveUserCredential(props.platform.platform_id, selectedUser, values);
      } else {
        await saveSharedCredential(props.platform.platform_id, values);
      }
      setFormVisible(false);
      setValues({});
      await reload();
    } finally {
      setSaving(false);
    }
  };

  const configured = selectedUser ? userCredential?.configured === true : false;

  return (
    <div>
      <div className="detail-section-title">{t('platform.credentials.userSection')}</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Select
          value={selectedUser || undefined}
          style={{ width: 260 }}
          placeholder={t('platform.credentials.selectUser')}
          optionList={users.map((user) => ({
            value: user.id,
            label: `${user.display_name} · ${user.user_code}`
          }))}
          onChange={(value) => setSelectedUser(String(value))}
        />
        <Button theme="solid" disabled={!selectedUser} onClick={() => setFormVisible(true)}>
          {configured ? t('platform.credentials.update') : t('platform.credentials.configure')}
        </Button>
      </div>
      {loading ? (
        <Spin style={{ display: 'block', margin: '16px auto' }} />
      ) : selectedUser ? (
        <Table
          dataSource={userCredential ? [userCredential] : []}
          rowKey="platform"
          pagination={false}
          columns={[
            {
              title: t('platform.credentials.status'),
              dataIndex: 'configured',
              render: (value: boolean) =>
                value ? (
                  <Tag color="green">{t('platform.credentials.configured')}</Tag>
                ) : (
                  <Tag color="orange">{t('platform.credentials.notConfigured')}</Tag>
                )
            },
            { title: t('platform.credentials.schemaVersion'), dataIndex: 'credential_schema_version', render: (value?: string) => value ?? '-' },
            { title: t('platform.credentials.updatedAt'), dataIndex: 'last_verified_at', render: (value?: string) => (value ? <DateTimeText value={value} /> : '-') }
          ]}
        />
      ) : null}
      <div className="detail-hint">{t('platform.credentials.userHint')}</div>

      <div className="detail-section-title">{t('platform.credentials.sharedSection')}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <Tag color={sharedCredential?.configured ? 'green' : 'orange'}>
          {sharedCredential?.configured
            ? t('platform.credentials.configured')
            : t('platform.credentials.notConfigured')}
        </Tag>
        <Button onClick={() => setFormVisible(true)}>{t('platform.credentials.update')}</Button>
      </div>
      <div className="detail-hint">{t('platform.credentials.sharedHint')}</div>

      <FormModal
        visible={formVisible}
        width={520}
        title={t('platform.credentials.formTitle')}
        okText={t('common.save')}
        confirmLoading={saving}
        onOk={() => void save()}
        onCancel={() => {
          setFormVisible(false);
          setValues({});
        }}
      >
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
