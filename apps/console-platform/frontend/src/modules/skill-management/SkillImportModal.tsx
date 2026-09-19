import { Button, Form, Toast, Upload } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import { importArtifact, importSkill, type SkillImportResult } from './services/skills';

export const SKILL_ZIP_LIMIT_BYTES = 50 * 1024 * 1024;

export interface SkillImportModalProps {
  visible: boolean;
  /** 传入则为已有 Skill 导入新版本（隐藏 key/范围，走 /artifacts） */
  skillId?: string;
  currentVersion?: string | null;
  onCancel(): void;
  onSaved(result: SkillImportResult): void;
}

interface FormValues {
  file?: File;
  version: string;
  key?: string;
  default_script?: string;
  user_scope: 'ALL' | 'SELECTED';
}

export function SkillImportModal(props: SkillImportModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    if (!props.skillId) {
      formApi.current?.setValues({ user_scope: 'SELECTED' });
    }
  }, [props.visible]);

  const submit = async (values: FormValues): Promise<void> => {
    if (!values.file) {
      Toast.error(t('skill.import.fileRequired'));
      return;
    }
    if (!values.file.name.toLowerCase().endsWith('.zip')) {
      Toast.error(t('skill.import.zipOnly'));
      return;
    }
    if (values.file.size > SKILL_ZIP_LIMIT_BYTES) {
      Toast.error(t('skill.import.tooLarge', { limit: '50MiB' }));
      return;
    }
    setSaving(true);
    try {
      const saved = props.skillId
        ? await importArtifact(props.skillId, values.file, values.version)
        : await importSkill({
            file: values.file,
            version: values.version,
            key: values.key || undefined,
            default_script: values.default_script || undefined,
            user_scope: values.user_scope
          });
      props.onSaved(saved);
    } catch {
      // 校验/重复版本错误由 ApiClient 展示本地化 Toast，Modal 保留供修正重试
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={520}
      title={props.skillId ? t('skill.artifact.importNew') : t('skill.import.title')}
      okText={t('skill.import.submit')}
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
      <Upload
        data-testid="skill-zip-upload"
        limit={1}
        accept=".zip"
        draggable
        dragMainText={t('skill.import.dragMain')}
        dragSubText={t('skill.import.dragSub', { limit: '50MiB' })}
        onFileChange={(files) => {
          const item = (files as Array<{ fileInstance?: File }>)[0];
          formApi.current?.setValue('file', item?.fileInstance ?? undefined);
        }}
        customRequest={() => undefined}
      />
      <Form.Input
        field="version"
        label={t('skill.columns.currentVersion')}
        rules={[{ required: true, message: t('skill.columns.currentVersion') }]}
      />
      {props.skillId ? null : (
        <>
          <Form.Input field="key" label={t('skill.form.key')} extraText={t('skill.form.keyHint')} />
          <Form.Input field="default_script" label={t('skill.form.defaultScript')} placeholder="scripts/main.py" />
          <Form.Select
            field="user_scope"
            label={t('skill.columns.userScope')}
            initValue="SELECTED"
            extraText={t('skill.form.scopeHint')}
            optionList={[
              { value: 'SELECTED', label: t('skill.scope.selected') },
              { value: 'ALL', label: t('skill.scope.all') }
            ]}
          />
        </>
      )}
    </FormModal>
  );
}
