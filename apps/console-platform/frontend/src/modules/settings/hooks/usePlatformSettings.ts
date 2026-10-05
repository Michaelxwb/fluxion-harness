/**
 * 设置页数据状态机（前端设计 §3.5 状态划分、§3.6 UI 状态）。
 *
 * 取数与保存只经 `../services/settingsApi`（组件不裸用 HTTP 客户端）。持有 `meta`（分组/字段/
 * 默认/覆盖数）、`values`（编辑中的扁平值）、`baseValues`（基准、脏判断与重置）、`fieldErrors`、
 * `saving`/`conflict`/`failed`。读取失败置 `failed`（E-13 由页面渲染错误态、**不渲染任何值**）；
 * 保存失败按 `details[].path` 落到 `fieldErrors`（E-11）；版本冲突置 `conflict`（E-12，不自动重试）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Toast } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { buildDocument, initialValues, isDirty } from '../formValues';
import {
  errorCodeOf,
  fieldErrorsFrom,
  getPlatformSettings,
  savePlatformSettings
} from '../services/settingsApi';
import type {
  PlatformSettingsSnapshot,
  ReadonlyNote,
  SettingsFieldMeta,
  SettingsGroupMeta,
  SettingsValues
} from '../types';

const VERSION_CONFLICT = 'PLATFORM_SETTINGS_VERSION_CONFLICT';

export interface PlatformSettingsState {
  groups: SettingsGroupMeta[];
  readonlyNotes: ReadonlyNote[];
  revision: number;
  updatedAt: string | null;
  updatedBy: string | null;
  values: SettingsValues;
  errors: Record<string, string>;
  loading: boolean;
  failed: boolean;
  saving: boolean;
  conflict: boolean;
  dirty: boolean;
  setField(path: string, value: unknown): void;
  reset(): void;
  save(): Promise<void>;
  reload(): void;
}

function valuesFromGroups(groups: SettingsGroupMeta[]): SettingsValues {
  return initialValues(groups);
}

export function usePlatformSettings(): PlatformSettingsState {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<PlatformSettingsSnapshot | null>(null);
  const [values, setValues] = useState<SettingsValues>({});
  const [baseValues, setBaseValues] = useState<SettingsValues>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(() => {
    const current = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    setConflict(false);
    setErrors({});
    void (async () => {
      try {
        const data = await getPlatformSettings();
        if (current !== requestSeq.current) {
          return;
        }
        const nextValues = valuesFromGroups(data.groups);
        setSnapshot(data);
        setValues(nextValues);
        setBaseValues(nextValues);
      } catch {
        if (current === requestSeq.current) {
          // E-13：只置失败，不清空也不填充任何值——错误态由页面渲染，绝不把"没读到"显示成"当前值"。
          setFailed(true);
          setSnapshot(null);
          setValues({});
          setBaseValues({});
        }
      } finally {
        if (current === requestSeq.current) {
          setLoading(false);
        }
      }
    })();
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const setField = useCallback((path: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [path]: value }));
    setErrors((prev) => {
      if (!(path in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[path];
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    setValues(baseValues);
    setErrors({});
  }, [baseValues]);

  const dirty = isDirty(baseValues, values);

  const save = useCallback(async () => {
    if (snapshot === null || saving || !dirty) {
      return;
    }
    setSaving(true);
    setErrors({});
    setConflict(false);
    try {
      const result = await savePlatformSettings({
        revision: snapshot.revision,
        settings: buildDocument(snapshot.groups, values)
      });
      setSnapshot((prev) => (prev === null ? prev : { ...prev, revision: result.revision }));
      setBaseValues(values);
      Toast.success({ content: t('settings.save.success') });
    } catch (error) {
      const code = errorCodeOf(error);
      if (code === VERSION_CONFLICT) {
        // E-12：冲突即失败并提示重新加载，绝不静默重试或覆盖他人改动。
        setConflict(true);
      } else {
        const fieldErrors = fieldErrorsFrom(error);
        if (fieldErrors.length > 0) {
          setErrors(
            Object.fromEntries(fieldErrors.map((item) => [item.path, item.message]))
          );
        }
      }
    } finally {
      setSaving(false);
    }
  }, [snapshot, saving, dirty, values, t]);

  return {
    groups: snapshot?.groups ?? [],
    readonlyNotes: snapshot?.readonlyNotes ?? [],
    revision: snapshot?.revision ?? 0,
    updatedAt: snapshot?.updatedAt ?? null,
    updatedBy: snapshot?.updatedBy ?? null,
    values,
    errors,
    loading,
    failed,
    saving,
    conflict,
    dirty,
    setField,
    reset,
    save,
    reload
  };
}

/** 供 CMP-05 判断控件形态：枚举/布尔/数值/字符串（范围与默认值全部来自 `meta`）。 */
export function controlKind(meta: SettingsFieldMeta): 'switch' | 'select' | 'number' | 'text' {
  if (meta.type === 'bool') {
    return 'switch';
  }
  if (meta.enum && meta.enum.length > 0) {
    return 'select';
  }
  if (meta.type === 'int' || meta.type === 'float') {
    return 'number';
  }
  return 'text';
}
