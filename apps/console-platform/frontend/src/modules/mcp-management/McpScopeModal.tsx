import { Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import { setUserScope } from './services/mcpServers';

export interface McpScopeModalProps {
  visible: boolean;
  serverId: string | null;
  currentScope: 'ALL' | 'SELECTED';
  onCancel(): void;
  onSaved(): void;
}

export function McpScopeModal(props: McpScopeModalProps) {
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
    if (!props.serverId) {
      return;
    }
    setSaving(true);
    try {
      await setUserScope(props.serverId, values.user_scope);
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
      title={t('mcp.scope.changeTitle')}
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
        label={t('mcp.columns.userScope')}
        extraText={t('mcp.scope.changeHint')}
        optionList={[
          { value: 'SELECTED', label: t('mcp.scope.selected') },
          { value: 'ALL', label: t('mcp.scope.all') }
        ]}
      />
    </FormModal>
  );
}
