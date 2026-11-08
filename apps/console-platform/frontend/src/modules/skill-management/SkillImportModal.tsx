import { Banner, Form, Upload } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import type { TFunction } from 'i18next';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { FormModal } from '../../components/common/FormModal';
import { Tips } from '../../components/common/Tips';
import {
  importArtifact,
  importSkill,
  skillImportErrorDetail,
  type SkillArtifactDetail,
  type SkillImportErrorDetail,
  type SkillImportResult
} from './services/skills';

export const SKILL_ZIP_LIMIT_BYTES = 50 * 1024 * 1024;

export interface SkillImportModalProps {
  visible: boolean;
  /** 传入则为已有 Skill 导入新版本（隐藏 key/范围，走 /artifacts） */
  skillId?: string;
  /** 只有管理员可以显式选择 SYSTEM/ADMIN 来源；普通用户保持服务端默认。 */
  canManageSource?: boolean;
  onCancel(): void;
  onSaved(result: SkillImportResult | SkillArtifactDetail): void;
}

interface FormValues {
  user_scope: 'ALL' | 'SELECTED';
  source?: 'SYSTEM' | 'ADMIN';
}

/** Map the backend's machine-readable package reason to a precise bilingual message. */
function importErrorMessage(detail: SkillImportErrorDetail | null, t: TFunction): string | null {
  const path = detail?.path ?? '-';
  switch (detail?.reason) {
    case 'INVALID_ZIP':
      return t('skill.import.error.invalidZip');
    case 'PACKAGE_TOO_LARGE':
      return t('skill.import.error.packageTooLarge', { limit: detail.limit ?? '-' });
    case 'UNPACKED_TOO_LARGE':
      return t('skill.import.error.unpackedTooLarge', { limit: detail.limit ?? '-' });
    case 'TOO_MANY_FILES':
      return t('skill.import.error.tooManyFiles', { limit: detail.limit ?? '-' });
    case 'PATH_INVALID':
      return t('skill.import.error.pathInvalid', { path });
    case 'FILE_TYPE_UNSUPPORTED':
      return t('skill.import.error.fileTypeUnsupported', { path });
    case 'NESTED_ARCHIVE':
      return t('skill.import.error.nestedArchive', { path });
    case 'SECRET_DETECTED':
      return t('skill.import.error.secretDetected', { path });
    case 'SKILL_MD_MISSING':
      return t('skill.import.error.skillMdMissing');
    case 'SKILL_KEY_MISSING':
      return t('skill.import.error.skillKeyMissing');
    case 'DEFAULT_SCRIPT_INVALID':
      return t('skill.import.error.defaultScriptInvalid', { path });
    case 'MANIFEST_INVALID':
      return t('skill.import.error.manifestInvalid', { detail: detail.detail ?? '-' });
    default:
      return null;
  }
}

export function SkillImportModal(props: SkillImportModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [source, setSource] = useState<'SYSTEM' | 'ADMIN'>('ADMIN');
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    setFile(null);
    setFormError(null);
    setSource('ADMIN');
    formApi.current?.reset();
    if (!props.skillId) {
      formApi.current?.setValues({ user_scope: 'SELECTED', source: 'ADMIN' });
    }
  }, [props.visible, props.skillId]);

  const submit = async (values: FormValues): Promise<void> => {
    if (!file) {
      Tips.error(t('skill.import.fileRequired'));
      return;
    }
    if (!file.name.toLowerCase().endsWith('.zip')) {
      Tips.error(t('skill.import.zipOnly'));
      return;
    }
    if (file.size > SKILL_ZIP_LIMIT_BYTES) {
      Tips.error(t('skill.import.tooLarge', { limit: '50MiB' }));
      return;
    }
    setFormError(null);
    setSaving(true);
    try {
      const saved = props.skillId
        ? await importArtifact(props.skillId, file, { errorTip: 'never' })
        : await importSkill(
            {
              file,
              user_scope: source === 'SYSTEM' ? 'ALL' : values.user_scope,
              source: props.canManageSource ? (values.source ?? source) : undefined
            },
            { errorTip: 'never' }
          );
      props.onSaved(saved);
    } catch (error) {
      // 校验错误由 Modal 内 Banner 持续展示；全局 Toast 关闭，避免重复且过短的提示。
      setFormError(
        importErrorMessage(skillImportErrorDetail(error), t) ??
          apiErrorBody(error)?.msg ??
          t('skill.import.failed')
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={520}
      title={props.skillId ? t('skill.artifact.importTitle') : t('skill.import.title')}
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
      {formError ? (
        <Banner
          className="form-field-banner"
          type="danger"
          closeIcon={null}
          description={formError}
        />
      ) : null}
      <Upload
        data-testid="skill-zip-upload"
        className="skill-import-upload"
        uploadTrigger="custom"
        limit={1}
        accept=".zip"
        draggable
        dragMainText={t('skill.import.dragMain')}
        dragSubText={t('skill.import.dragSub', { limit: '50MiB' })}
        onRemove={() => {
          setFile(null);
          setFormError(null);
        }}
        onFileChange={(files) => {
          setFile(files[0] ?? null);
          setFormError(null);
        }}
      />
      {props.skillId || !props.canManageSource ? null : (
        <>
          <Form.Select
            field="source"
            label={t('skill.import.source.label')}
            initValue="ADMIN"
            optionList={[
              { value: 'ADMIN', label: t('skill.source.admin') },
              { value: 'SYSTEM', label: t('skill.source.system') }
            ]}
            // 说明并进字段本身：表单字段之间必须保持紧凑间距（form-layout 机检），
            // 独立的说明块会把相邻字段撑出 50px 的缝。
            extraText={
              source === 'SYSTEM'
                ? t('skill.import.source.systemHint')
                : t('skill.import.source.adminHint')
            }
            onChange={(value) => setSource(value as 'SYSTEM' | 'ADMIN')}
          />
        </>
      )}
      {props.skillId || source === 'SYSTEM' ? null : (
        <Form.Select
          field="user_scope"
          label={t('skill.columns.userScope')}
          initValue="SELECTED"
          extraText={t('skill.form.scopeHint')}
          optionList={[
            { value: 'ALL', label: t('skill.scope.all') },
            { value: 'SELECTED', label: t('skill.scope.selected') }
          ]}
        />
      )}
    </FormModal>
  );
}
