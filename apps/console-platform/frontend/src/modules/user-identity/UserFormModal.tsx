import { Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import { createUser, updateUser, type UserDetail } from './services/users';

export interface UserFormModalProps {
  visible: boolean;
  user: UserDetail | null;
  onCancel(): void;
  onSaved(user: UserDetail): void;
}

interface FormValues {
  user_code: string;
  display_name: string;
  status: string;
}

export function UserFormModal(props: UserFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    formApi.current?.setValues({ status: 'ACTIVE' });
    if (props.user) {
      formApi.current?.setValues({
        user_code: props.user.user_code,
        display_name: props.user.display_name,
        status: props.user.status
      });
    }
  }, [props.visible, props.user]);

  const submit = async (values: FormValues): Promise<void> => {
    setSaving(true);
    try {
      const saved = props.user
        ? await updateUser(props.user.id, { display_name: values.display_name, status: values.status })
        : await createUser(values);
      props.onSaved(saved);
    } catch (error) {
      const envelope =
        (error as { response?: { data?: { code?: string; msg?: string } } }).response?.data ??
        (error as { code?: string; msg?: string });
      if (envelope.code === 'COMMON_CONFLICT') {
        formApi.current?.setError('user_code', envelope.msg ?? t('user.form.userCode'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={520}
      title={props.user ? t('user.form.editTitle') : t('user.form.createTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as FormValues);
      }}
    >
      <Form.Input
        field="display_name"
        label={t('user.form.displayName')}
        rules={[{ required: true, message: t('user.form.displayName') }]}
      />
      <Form.Input
        field="user_code"
        label={t('user.form.userCode')}
        disabled={props.user !== null}
        extraText={props.user ? t('user.form.userCodeImmutable') : undefined}
        rules={props.user ? [] : [{ required: true, message: t('user.form.userCode') }]}
      />
      <Form.Select
        field="status"
        label={t('user.form.status')}
        initValue="ACTIVE"
        optionList={[
          { value: 'ACTIVE', label: t('common.status.enabled') },
          { value: 'DISABLED', label: t('common.status.disabled') }
        ]}
      />
    </FormModal>
  );
}
