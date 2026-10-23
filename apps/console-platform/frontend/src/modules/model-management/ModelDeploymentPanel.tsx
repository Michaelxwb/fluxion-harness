import { Banner, Button, Form } from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../auth/AuthContext';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DataTable } from '../../components/common/DataTable';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { StatusTag } from '../../components/common/StatusTag';
import { actionColumn } from '../../components/common/tableLayout';
import { TableText } from '../../components/common/TableText';
import { enabledOptions } from './statusOptions';
import {
  listCredentials,
  type ModelCredentialItem
} from './services/modelCredentials';
import {
  MEMBER_WEIGHT_MAX,
  MEMBER_WEIGHT_MIN,
  ModelDeploymentInputError,
  buildDeploymentCreateInput,
  buildDeploymentUpdateInput,
  createDeployment,
  deleteDeployment,
  deploymentSaveErrorKey,
  listDeployments,
  suggestMemberKey,
  updateDeployment,
  type ModelDeploymentFormValues,
  type ModelDeploymentInputReason,
  type ModelDeploymentItem
} from './services/modelDeployments';

/**
 * 模型成员维护面（design frontend §3.2/§3.4；backend API-01）。
 *
 * - **仅 ADMIN**：非 ADMIN 不渲染维护面（「前端隐藏 + 后端 403 兜底」的前端一半）；
 * - **单关系操作**：绑定/改权重/解绑各是一条关系（POST/PUT/DELETE），没有集合整体写入口；
 * - **不重复填写密钥**：只从已配置凭据里选（同供方、启用、未绑定），密钥/端点不在本面出现；
 * - **保存后影响后续新 Run**：说明文案常驻；权重由服务端分配请求时消费；
 * - 供方未声明（`provider_key` 为空）时后端必然拒绝成员，这里显式说明并禁用绑定入口。
 */

const MEMBER_PAGE_SIZE = 100;
const CREDENTIAL_PAGE_SIZE = 100;

const INPUT_ERROR_KEYS: Record<ModelDeploymentInputReason, string> = {
  WEIGHT_INVALID: 'model.member.form.weightInvalid',
  CREDENTIAL_REQUIRED: 'model.member.form.credentialRequired'
};

interface MemberFormValues {
  key: string;
  credential_id?: string;
  weight: number;
  enabled: boolean;
}

function toServiceValues(values: MemberFormValues): ModelDeploymentFormValues {
  return {
    key: values.key ?? '',
    credential_id: values.credential_id ?? '',
    weight: Number(values.weight),
    enabled: values.enabled ?? true
  };
}

/** 成员列表状态：请求竞态守卫 + 单页上限（后端 page_size≤100），超出时由面板显式提示截断。 */
function useMemberList(modelId: string, isAdmin: boolean) {
  const [items, setItems] = useState<ModelDeploymentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    if (!isAdmin) {
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const result = await listDeployments(modelId, { page: 1, page_size: MEMBER_PAGE_SIZE });
      if (seq !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
    } catch {
      if (seq !== requestSeq.current) {
        return;
      }
      setFailed(true);
      setItems([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [isAdmin, modelId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { items, total, loading, failed, reload };
}

/** 已配置凭据索引：供「可绑定集合」与成员表的凭据标签使用（读取只 `configured`）。 */
function useCredentialIndex(isAdmin: boolean) {
  const [items, setItems] = useState<ModelCredentialItem[]>([]);
  const [total, setTotal] = useState(0);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    if (!isAdmin) {
      return;
    }
    const seq = ++requestSeq.current;
    setFailed(false);
    try {
      const result = await listCredentials({ page: 1, page_size: CREDENTIAL_PAGE_SIZE });
      if (seq !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
    } catch {
      if (seq !== requestSeq.current) {
        return;
      }
      setFailed(true);
      setItems([]);
      setTotal(0);
    }
  }, [isAdmin]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { items, total, failed, truncated: total > items.length, reload };
}

export interface ModelDeploymentFormProps {
  visible: boolean;
  modelId: string;
  member: ModelDeploymentItem | null;
  /** 可绑定的已配置凭据（同供方、启用、未绑定）；编辑时凭据不可更换（重绑 = 解绑 + 绑定）。 */
  eligibleCredentials: ModelCredentialItem[];
  /** 当前模型已有的成员标识：用于把“选中 Key 后连带成员名”的去重建议。 */
  existingMemberKeys: string[];
  /** 编辑时的当前凭据标签（可能已不在可绑定集合里）。 */
  currentCredentialLabel: string;
  onCancel(): void;
  onSaved(): void;
}

export function ModelDeploymentForm(props: ModelDeploymentFormProps) {
  const { t } = useTranslation();
  const { member } = props;
  const [saving, setSaving] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    setErrorKey(null);
    formApi.current?.reset();
    formApi.current?.setValues({
      key: member?.key ?? '',
      credential_id: member?.credential_id ?? undefined,
      weight: member?.weight ?? MEMBER_WEIGHT_MIN,
      enabled: member?.enabled ?? true
    });
  }, [props.visible, member]);

  const submit = async (raw: MemberFormValues): Promise<void> => {
    setSaving(true);
    setErrorKey(null);
    const selected =
      member === null
        ? props.eligibleCredentials.find((item) => item.id === raw.credential_id)
        : undefined;
    if (member === null && selected === undefined) {
      setSaving(false);
      setErrorKey(INPUT_ERROR_KEYS.CREDENTIAL_REQUIRED);
      return;
    }
    // 成员标识是派生展示值：创建时取所选物理 Key 的标识（重名加后缀），编辑时保持原值。
    let key = member?.key ?? '';
    if (member === null && selected !== undefined) {
      key = suggestMemberKey(selected.key, props.existingMemberKeys);
    }
    const values = toServiceValues({ ...raw, key });
    try {
      if (member === null) {
        await createDeployment(props.modelId, buildDeploymentCreateInput(values));
      } else {
        await updateDeployment(props.modelId, member.id, buildDeploymentUpdateInput(member, values));
      }
      props.onSaved();
    } catch (error) {
      setErrorKey(
        error instanceof ModelDeploymentInputError
          ? INPUT_ERROR_KEYS[error.reason]
          : deploymentSaveErrorKey(error)
      );
    } finally {
      setSaving(false);
    }
  };

  const noEligible = member === null && props.eligibleCredentials.length === 0;
  const credentialOptions =
    member === null
      ? props.eligibleCredentials.map((credential) => ({
          value: credential.id,
          label: `${credential.key} · ${credential.provider_key}`
        }))
      : [{ value: member.credential_id, label: props.currentCredentialLabel }];

  return (
    <FormModal
      visible={props.visible}
      width={560}
      title={member === null ? t('model.member.form.createTitle') : t('model.member.form.editTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      okButtonProps={{ disabled: noEligible }}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as MemberFormValues);
      }}
    >
      <div className="form-grid">
        <Form.Input
          field="key"
          label={t('model.member.form.key')}
          disabled
          extraText={t('model.member.form.keyHint')}
        />
        <Form.Select
          field="credential_id"
          label={t('model.member.form.credential')}
          disabled={member !== null}
          extraText={t('model.member.form.credentialHint')}
          optionList={credentialOptions}
          rules={member === null ? [{ required: true, message: t('model.member.form.credentialRequired') }] : []}
          onChange={(value) => {
            if (member !== null) {
              return;
            }
            const selected = props.eligibleCredentials.find((item) => item.id === String(value));
            if (selected !== undefined) {
              // 成员名默认取所选物理 Key 的标识：只选一个下拉，不再手填第二遍。
              formApi.current?.setValue(
                'key',
                suggestMemberKey(selected.key, props.existingMemberKeys)
              );
            }
          }}
        />
        <Form.InputNumber
          field="weight"
          label={t('model.member.form.weight')}
          min={MEMBER_WEIGHT_MIN}
          max={MEMBER_WEIGHT_MAX}
          precision={0}
          style={{ width: '100%' }}
          extraText={t('model.member.form.weightHint')}
          rules={[{ required: true, message: t('model.member.form.weightInvalid') }]}
        />
        <Form.Switch field="enabled" label={t('model.member.form.enabled')} initValue />
      </div>
      <div className="detail-hint">{t('model.member.form.capacityHint')}</div>
      {noEligible ? <Banner type="info" closeIcon={null} description={t('model.member.noEligible')} /> : null}
      {errorKey === null ? null : <Banner type="danger" closeIcon={null} description={t(errorKey)} />}
    </FormModal>
  );
}

interface MemberTableProps {
  items: ModelDeploymentItem[];
  loading: boolean;
  credentialLabel(credentialId: string): string;
  onEdit(member: ModelDeploymentItem): void;
  onUnbind(member: ModelDeploymentItem): void;
}

function MemberTable(props: MemberTableProps) {
  const { t } = useTranslation();
  return (
    <DataTable<ModelDeploymentItem>
      className="app-table-fit app-table-nowrap"
      scroll={{ x: '100%' }}
      loading={props.loading}
      dataSource={props.items}
      rowKey="id"
      pagination={false}
      empty={<EmptyState title={t('model.member.empty')} description={t('common.emptyHint')} />}
      columns={[
        {
          title: t('model.member.columns.key'),
          dataIndex: 'key',
          width: 'auto',
          ellipsis: true,
          render: (value: string) => <TableText>{value}</TableText>
        },
        {
          title: t('model.member.columns.credential'),
          dataIndex: 'credential_id',
          width: 'auto',
          ellipsis: true,
          render: (value: string) => <TableText>{props.credentialLabel(value)}</TableText>
        },
        { title: t('model.member.columns.weight'), dataIndex: 'weight', width: 96 },
        {
          title: t('model.member.columns.enabled'),
          dataIndex: 'enabled',
          width: 96,
          render: (value: boolean) => <StatusTag status={value} options={enabledOptions(t)} />
        },
        {
          title: t('model.member.columns.revision'),
          dataIndex: 'revision',
          width: 88,
          render: (value: number) => `r${value}`
        },
        {
          ...actionColumn(168),
          title: t('model.member.columns.actions'),
          render: (_: unknown, member: ModelDeploymentItem) => (
            <div className="model-table-actions">
              <Button
                theme="borderless"
                data-testid={`member-edit-${member.key}`}
                onClick={() => props.onEdit(member)}
              >
                {t('model.member.actions.edit')}
              </Button>
              <ConfirmAction
                title={t('model.member.confirmUnbind')}
                testId={`member-unbind-${member.key}`}
                onConfirm={() => props.onUnbind(member)}
              >
                {t('model.member.actions.unbind')}
              </ConfirmAction>
            </div>
          )
        }
      ]}
    />
  );
}

export interface ModelDeploymentPanelProps {
  modelId: string;
  /** 逻辑模型供方（`provider_key`）；空串 = 未声明 —— 后端会拒绝任何成员绑定（同供方核对）。 */
  providerKey: string;
  /** 成员变更后的通知（详情/列表按需刷新）；缺省无操作。 */
  onChanged?(): void;
}

export function ModelDeploymentPanel(props: ModelDeploymentPanelProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const list = useMemberList(props.modelId, isAdmin);
  const credentials = useCredentialIndex(isAdmin);
  const [formVisible, setFormVisible] = useState(false);
  const [editing, setEditing] = useState<ModelDeploymentItem | null>(null);
  const [actionErrorKey, setActionErrorKey] = useState<string | null>(null);

  const boundIds = new Set(list.items.map((member) => member.credential_id));
  const eligibleCredentials = credentials.items.filter(
    (credential) =>
      credential.enabled &&
      credential.provider_key === props.providerKey &&
      !boundIds.has(credential.id)
  );
  const credentialLabel = (credentialId: string): string =>
    credentials.items.find((credential) => credential.id === credentialId)?.key ?? credentialId;

  const afterMutation = (): void => {
    setActionErrorKey(null);
    void list.reload();
    void credentials.reload();
    props.onChanged?.();
  };

  const unbind = async (member: ModelDeploymentItem): Promise<void> => {
    setActionErrorKey(null);
    try {
      await deleteDeployment(props.modelId, member.id);
    } catch (error) {
      // 解绑冲突（版本/归属变化）：共享传输已提示后端文案，这里保留面板内定位
      setActionErrorKey(deploymentSaveErrorKey(error));
      return;
    }
    afterMutation();
  };

  if (!isAdmin) {
    return null;
  }

  return (
    <div data-testid="model-member-panel">
      <div className="detail-toolbar">
        <Button
          theme="solid"
          type="primary"
          icon={<IconPlus />}
          data-testid="bind-member"
          disabled={props.providerKey === '' || credentials.failed}
          onClick={() => {
            setEditing(null);
            setFormVisible(true);
          }}
        >
          {t('model.member.add')}
        </Button>
      </div>
      <div className="detail-section-title">{t('model.member.title')}</div>
      {props.providerKey === '' ? (
        <Banner type="warning" closeIcon={null} description={t('model.member.providerUndeclared')} />
      ) : null}
      {actionErrorKey === null ? null : (
        <Banner type="danger" closeIcon={null} description={t(actionErrorKey)} />
      )}
      {credentials.failed ? (
        <div className="detail-hint" data-testid="member-credentials-unavailable">
          {t('model.member.credentialsUnavailable')}
        </div>
      ) : null}
      {credentials.truncated ? (
        <div className="detail-hint" data-testid="member-credentials-truncated">
          {t('model.member.credentialsTruncated', {
            shown: credentials.items.length,
            total: credentials.total
          })}
        </div>
      ) : null}
      {list.failed ? (
        <ErrorState onRetry={() => void list.reload()} />
      ) : (
        <MemberTable
          items={list.items}
          loading={list.loading}
          credentialLabel={credentialLabel}
          onEdit={(member) => {
            setEditing(member);
            setFormVisible(true);
          }}
          onUnbind={(member) => void unbind(member)}
        />
      )}
      {list.total > list.items.length ? (
        <div className="detail-hint" data-testid="member-truncated">
          {t('model.member.truncated', { shown: list.items.length, total: list.total })}
        </div>
      ) : null}
      <div className="detail-hint">{t('model.member.effectHint')}</div>
      <ModelDeploymentForm
        visible={formVisible}
        modelId={props.modelId}
        member={editing}
        eligibleCredentials={eligibleCredentials}
        existingMemberKeys={list.items.map((item) => item.key)}
        currentCredentialLabel={editing === null ? '' : credentialLabel(editing.credential_id)}
        onCancel={() => {
          setFormVisible(false);
          setEditing(null);
        }}
        onSaved={() => {
          setFormVisible(false);
          setEditing(null);
          afterMutation();
        }}
      />
    </div>
  );
}
