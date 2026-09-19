import { Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import { setUserScope } from './services/skills';

export interface SkillScopeModalProps {
  visible: boolean;
  skillId: string | null;
  currentScope: 'ALL' | 'SELECTED';
  onCancel(): void;
  onSaved(): void;
}

export function SkillScopeModal(props: SkillScopeModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    formApi.current?.setValues({ user_scope: props.currentScope });
  }, [props.visible, props.currentScope]);

  const submit = async (values: { user_scope: 'ALL' | 'SELECTED' }): Promise<void> => {
    if (!props.skillId) {
      return;
    }
    setSaving(true);
    try {
      await setUserScope(props.skillId, values.user_scope);
      props.onSaved();
    } catch {
      // 错误由 ApiClient 展示
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={480}
      title={t('skill.scope.changeTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as { user_scope: 'ALL' | 'SELECTED' });
      }}
    >
      <Form.Select
        field="user_scope"
        label={t('skill.columns.userScope')}
        extraText={t('skill.scope.changeHint')}
        optionList={[
          { value: 'SELECTED', label: t('skill.scope.selected') },
          { value: 'ALL', label: t('skill.scope.all') }
        ]}
      />
    </FormModal>
  );
}
