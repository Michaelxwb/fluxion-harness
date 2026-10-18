import { Banner, Button, Form } from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DataTable } from '../../components/common/DataTable';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { actionColumn } from '../../components/common/tableLayout';
import { TableText } from '../../components/common/TableText';
import {
  ModelQuotaGroupInputError,
  buildQuotaGroupCreateInput,
  buildQuotaGroupUpdateInput,
  createQuotaGroup,
  deleteQuotaGroup,
  describeQuotaGroup,
  listQuotaGroups,
  quotaGroupSaveErrorKey,
  updateQuotaGroup,
  type ModelQuotaGroupFormValues,
  type ModelQuotaGroupInputReason,
  type ModelQuotaGroupItem,
  type QuotaLimitDescriptor
} from './services/modelQuotas';

/**
 * 共享账号额度组维护面（design frontend §3.2/§3.4；backend API-02）。
 *
 * - **仅 ADMIN**：非 ADMIN 不渲染维护面（「前端隐藏 + 后端 403 兜底」的前端一半）；
 * - **共享预算**：一个额度组是一份容量声明，多个物理 Key/模型成员引用同一组时共享它，
 *   不按 Key 数复制额度；组外 Key 独占自身容量（说明文案常驻）；
 * - **`null` = 不设应用内限流**：RPM/TPM 留空提交 `null`，展示为「不设限」而不是 `0`；
 * - **降额/新开限流先排空**：改小在飞上限或任一限流、或从「不设限」新开限流，后端会要求排空，
 *   面板把该冲突落成明确文案；放宽/调大不受影响；
 * - **删除不误清引用**：仍被物理 Key 引用时删除是显式冲突，列表保持原样，不替后端清掉引用方容量。
 */

const DEFAULT_PAGE_SIZE = 15;

const INPUT_ERROR_KEYS: Record<ModelQuotaGroupInputReason, string> = {
  KEY_REQUIRED: 'model.quota.form.keyRequired',
  MAX_INFLIGHT_INVALID: 'model.quota.form.maxInflightInvalid',
  RPM_INVALID: 'model.quota.form.rpmInvalid',
  TPM_INVALID: 'model.quota.form.tpmInvalid'
};

interface QuotaGroupFormValues {
  key: string;
  max_inflight: number;
  rpm?: number | null;
  tpm?: number | null;
}

/** 留空 = 不设该维度限流：表单的空值显式落成 `null`，不落成 `0`、也不省略键。 */
function optionalLimit(value: number | null | undefined): number | null {
  return value === null || value === undefined ? null : value;
}

function toServiceValues(values: QuotaGroupFormValues): ModelQuotaGroupFormValues {
  return {
    key: values.key ?? '',
    max_inflight: Number(values.max_inflight),
    rpm: optionalLimit(values.rpm),
    tpm: optionalLimit(values.tpm)
  };
}

/** 单维度展示：`UNLIMITED` 只由 `null` 得到，与 `0` 严格区分。 */
function QuotaLimitCell(props: { descriptor: QuotaLimitDescriptor }) {
  const { t } = useTranslation();
  const { descriptor } = props;
  if (descriptor.state === 'UNLIMITED') {
    return (
      <TableText title={t('model.quota.limit.unlimitedHint')}>
        {t('model.quota.limit.unlimited')}
      </TableText>
    );
  }
  return (
    <TableText>
      {t('model.quota.limit.value', { value: descriptor.value, unit: t(descriptor.unitKey) })}
    </TableText>
  );
}

export interface ModelQuotaGroupFormProps {
  visible: boolean;
  group: ModelQuotaGroupItem | null;
  onCancel(): void;
  onSaved(): void;
}

export function ModelQuotaGroupForm(props: ModelQuotaGroupFormProps) {
  const { t } = useTranslation();
  const { group } = props;
  const [saving, setSaving] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);
  // 一次用户提交 = 一个幂等键：结果不明时重试沿用同一个键（RULE-api-002）
  const idempotencyKey = useRef<string>('');

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    setErrorKey(null);
    idempotencyKey.current = newRequestId();
    formApi.current?.reset();
    formApi.current?.setValues({
      key: group?.key ?? '',
      max_inflight: group?.max_inflight ?? 1,
      // 留空表示该维度不设应用内限流（null），编辑时按原值回显
      rpm: group?.rpm ?? undefined,
      tpm: group?.tpm ?? undefined
    });
  }, [props.visible, group]);

  const submit = async (raw: QuotaGroupFormValues): Promise<void> => {
    setSaving(true);
    setErrorKey(null);
    const values = toServiceValues(raw);
    try {
      if (group === null) {
        await createQuotaGroup(buildQuotaGroupCreateInput(values), idempotencyKey.current);
      } else {
        await updateQuotaGroup(group.id, buildQuotaGroupUpdateInput(group, values));
      }
      props.onSaved();
    } catch (error) {
      setErrorKey(
        error instanceof ModelQuotaGroupInputError
          ? INPUT_ERROR_KEYS[error.reason]
          : quotaGroupSaveErrorKey(error)
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={560}
      title={group === null ? t('model.quota.form.createTitle') : t('model.quota.form.editTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as QuotaGroupFormValues);
      }}
    >
      <div className="form-grid">
        <Form.Input
          field="key"
          label={t('model.quota.form.key')}
          extraText={t('model.quota.form.keyHint')}
          rules={[{ required: true, message: t('model.quota.form.keyRequired') }]}
        />
        <Form.InputNumber
          field="max_inflight"
          label={t('model.quota.form.maxInflight')}
          min={1}
          precision={0}
          style={{ width: '100%' }}
          extraText={t('model.quota.form.maxInflightHint')}
          rules={[{ required: true, message: t('model.quota.form.maxInflightInvalid') }]}
        />
        <Form.InputNumber
          field="rpm"
          label={t('model.quota.form.rpm')}
          min={1}
          precision={0}
          style={{ width: '100%' }}
          placeholder={t('model.quota.limit.unlimited')}
          extraText={t('model.quota.form.rpmHint')}
        />
        <Form.InputNumber
          field="tpm"
          label={t('model.quota.form.tpm')}
          min={1}
          precision={0}
          style={{ width: '100%' }}
          placeholder={t('model.quota.limit.unlimited')}
          extraText={t('model.quota.form.tpmHint')}
        />
      </div>
      <div className="detail-hint">{t('model.quota.drainHint')}</div>
      {errorKey === null ? null : <Banner type="danger" closeIcon={null} description={t(errorKey)} />}
    </FormModal>
  );
}

interface QuotaGroupTableProps {
  items: ModelQuotaGroupItem[];
  loading: boolean;
  onEdit(group: ModelQuotaGroupItem): void;
  onDelete(group: ModelQuotaGroupItem): void;
}

function QuotaGroupTable(props: QuotaGroupTableProps) {
  const { t } = useTranslation();
  return (
    <DataTable<ModelQuotaGroupItem>
      className="app-table-fit app-table-nowrap"
      scroll={{ x: '100%' }}
      loading={props.loading}
      dataSource={props.items}
      rowKey="id"
      pagination={false}
      empty={<EmptyState title={t('model.quota.empty')} description={t('common.emptyHint')} />}
      columns={[
        {
          title: t('model.quota.columns.key'),
          dataIndex: 'key',
          width: 'auto',
          ellipsis: true,
          render: (value: string) => <TableText>{value}</TableText>
        },
        {
          title: t('model.quota.columns.maxInflight'),
          dataIndex: 'max_inflight',
          width: 140,
          render: (_: unknown, group: ModelQuotaGroupItem) => (
            <QuotaLimitCell descriptor={describeQuotaGroup(group).maxInflight} />
          )
        },
        {
          title: t('model.quota.columns.rpm'),
          dataIndex: 'rpm',
          width: 140,
          render: (_: unknown, group: ModelQuotaGroupItem) => (
            <QuotaLimitCell descriptor={describeQuotaGroup(group).rpm} />
          )
        },
        {
          title: t('model.quota.columns.tpm'),
          dataIndex: 'tpm',
          width: 160,
          render: (_: unknown, group: ModelQuotaGroupItem) => (
            <QuotaLimitCell descriptor={describeQuotaGroup(group).tpm} />
          )
        },
        {
          title: t('model.quota.columns.revision'),
          dataIndex: 'revision',
          width: 88,
          render: (value: number) => `r${value}`
        },
        {
          ...actionColumn(140),
          title: t('model.quota.columns.actions'),
          render: (_: unknown, group: ModelQuotaGroupItem) => (
            <div className="model-table-actions">
              <Button
                theme="borderless"
                data-testid={`quota-edit-${group.key}`}
                onClick={() => props.onEdit(group)}
              >
                {t('model.quota.actions.edit')}
              </Button>
              <ConfirmAction
                title={t('model.quota.confirmDelete')}
                testId={`quota-delete-${group.key}`}
                onConfirm={() => props.onDelete(group)}
              >
                {t('model.quota.actions.delete')}
              </ConfirmAction>
            </div>
          )
        }
      ]}
    />
  );
}

/** 额度组列表状态：请求竞态守卫 + 分页（列表页加载的统一形态）。 */
function useQuotaGroupList(isAdmin: boolean) {
  const [items, setItems] = useState<ModelQuotaGroupItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
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
      const result = await listQuotaGroups({ page, page_size: pageSize });
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
  }, [isAdmin, page, pageSize]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    items,
    total,
    page,
    pageSize,
    loading,
    failed,
    reload,
    changePage: setPage,
    changePageSize: (size: number) => {
      setPageSize(size);
      setPage(1);
    }
  };
}

export interface ModelQuotaPanelProps {
  /** 维护成功后的通知（凭据归属与成员容量可能随之变化）；缺省无操作。 */
  onChanged?(): void;
}

export function ModelQuotaPanel(props: ModelQuotaPanelProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const list = useQuotaGroupList(isAdmin);
  const [formVisible, setFormVisible] = useState(false);
  const [editing, setEditing] = useState<ModelQuotaGroupItem | null>(null);
  const [actionErrorKey, setActionErrorKey] = useState<string | null>(null);

  const afterMutation = (): void => {
    setActionErrorKey(null);
    void list.reload();
    props.onChanged?.();
  };

  const remove = async (group: ModelQuotaGroupItem): Promise<void> => {
    setActionErrorKey(null);
    try {
      await deleteQuotaGroup(group.id);
    } catch (error) {
      // 仍被物理 Key 引用等冲突：共享传输已提示后端文案，这里保留面板内定位并保持列表原样
      setActionErrorKey(quotaGroupSaveErrorKey(error));
      return;
    }
    afterMutation();
  };

  if (!isAdmin) {
    return null;
  }

  return (
    <div data-testid="quota-panel">
      <ModuleToolbar
        actions={
          <Button
            theme="solid"
            icon={<IconPlus />}
            data-testid="create-quota-group"
            onClick={() => {
              setEditing(null);
              setFormVisible(true);
            }}
          >
            {t('model.quota.add')}
          </Button>
        }
      />
      <div className="detail-section-title">{t('model.quota.title')}</div>
      <div className="detail-hint">{t('model.quota.subtitle')}</div>
      {actionErrorKey === null ? null : (
        <Banner type="danger" closeIcon={null} description={t(actionErrorKey)} />
      )}
      {list.failed ? (
        <ErrorState onRetry={() => void list.reload()} />
      ) : (
        <QuotaGroupTable
          items={list.items}
          loading={list.loading}
          onEdit={(group) => {
            setEditing(group);
            setFormVisible(true);
          }}
          onDelete={(group) => void remove(group)}
        />
      )}
      {list.total > 0 && !list.failed ? (
        <PaginationFooter
          page={list.page}
          pageSize={list.pageSize}
          total={list.total}
          onPageChange={list.changePage}
          onPageSizeChange={list.changePageSize}
        />
      ) : null}
      {list.total > list.items.length && !list.failed ? (
        <div className="detail-hint" data-testid="quota-truncated">
          {t('model.quota.truncated', { shown: list.items.length, total: list.total })}
        </div>
      ) : null}
      <div className="detail-hint">{t('model.quota.shareHint')}</div>
      <div className="detail-hint">{t('model.quota.limit.unlimitedHint')}</div>
      <div className="detail-hint">{t('model.quota.drainHint')}</div>
      <div className="detail-hint">{t('model.quota.deleteHint')}</div>
      <ModelQuotaGroupForm
        visible={formVisible}
        group={editing}
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
