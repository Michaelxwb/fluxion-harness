import { Banner, Button, Modal, Select, Table, Typography } from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { StatusTag } from '../../components/common/StatusTag';
import { actionColumn } from '../../components/common/tableLayout';
import { TableText } from '../../components/common/TableText';
import { CredentialFields } from './CredentialFields';
import { apiKeyOptions, enabledOptions, probeStatusOptions } from './statusOptions';
import { listModels, type ModelItem } from './services/models';
import {
  ModelCredentialInputError,
  buildCredentialCreateInput,
  buildCredentialUpdateInput,
  createCredential,
  credentialSaveErrorKey,
  deleteCredential,
  listCredentials,
  listQuotaGroups,
  probeCredentials,
  updateCredential,
  type ModelCredentialFormValues,
  type ModelCredentialInputReason,
  type ModelCredentialItem,
  type ModelProbeResult,
  type ModelQuotaGroupOption
} from './services/modelCredentials';

/**
 * 物理 Key 管理面（design frontend §3.2/§3.4/§3.7；backend API-16）。
 *
 * - **仅 ADMIN**：非 ADMIN 不渲染维护面（「前端隐藏 + 后端 403 兜底」的前端一半）；
 * - **读取只 `configured`**：列表/详情不持有密钥字段，密钥只在写入表单提交；
 * - **留空保留、替换显式提交**：编辑时密钥输入从空值开始，留空不发送 `api_key`；
 * - **检测只点名单个成员**：一组 `(模型 × 凭据)` 一探，结果逐项展示，不伪称整池健康；
 * - 额度组维护（RPM/TPM 单位与共享关系）属额度组面板（TASK-088），这里只消费归属选项。
 */

const DEFAULT_PAGE_SIZE = 15;
const PROBE_MODEL_PAGE_SIZE = 100;

const INPUT_ERROR_KEYS: Record<ModelCredentialInputReason, string> = {
  API_KEY_REQUIRED: 'model.credential.form.apiKeyRequired',
  MAX_INFLIGHT_INVALID: 'model.credential.form.maxInflightInvalid'
};

interface CredentialFormValues {
  key: string;
  provider_key: string;
  base_url: string;
  api_key: string;
  quota_group_id?: string | null;
  max_inflight: number;
  enabled: boolean;
}

function toServiceValues(values: CredentialFormValues): ModelCredentialFormValues {
  return {
    key: values.key ?? '',
    provider_key: values.provider_key ?? '',
    base_url: values.base_url ?? '',
    api_key: values.api_key ?? '',
    quota_group_id: values.quota_group_id ?? null,
    max_inflight: Number(values.max_inflight),
    enabled: values.enabled ?? true
  };
}

export interface ModelCredentialFormProps {
  visible: boolean;
  credential: ModelCredentialItem | null;
  quotaGroups: ModelQuotaGroupOption[];
  onCancel(): void;
  onSaved(): void;
}

export function ModelCredentialForm(props: ModelCredentialFormProps) {
  const { t } = useTranslation();
  const { credential } = props;
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
      key: credential?.key ?? '',
      provider_key: credential?.provider_key ?? '',
      base_url: credential?.base_url ?? '',
      quota_group_id: credential?.quota_group_id ?? undefined,
      max_inflight: credential?.max_inflight ?? 1,
      enabled: credential?.enabled ?? true,
      // 密钥仅写入：编辑时从空值开始，留空表示保留原值
      api_key: ''
    });
  }, [props.visible, credential]);

  const submit = async (raw: CredentialFormValues): Promise<void> => {
    setSaving(true);
    setErrorKey(null);
    const values = toServiceValues(raw);
    try {
      if (credential === null) {
        await createCredential(buildCredentialCreateInput(values), idempotencyKey.current);
      } else {
        await updateCredential(credential.id, buildCredentialUpdateInput(credential, values));
      }
      props.onSaved();
    } catch (error) {
      setErrorKey(
        error instanceof ModelCredentialInputError
          ? INPUT_ERROR_KEYS[error.reason]
          : credentialSaveErrorKey(error)
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={640}
      title={credential === null ? t('model.credential.form.createTitle') : t('model.credential.form.editTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as CredentialFormValues);
      }}
    >
      <CredentialFields credential={credential} quotaGroups={props.quotaGroups} />
      {errorKey === null ? null : <Banner type="danger" closeIcon={null} description={t(errorKey)} />}
    </FormModal>
  );
}

interface CredentialProbeModalProps {
  credential: ModelCredentialItem;
  onCancel(): void;
}

/** 单成员检测：只探被点名的 (模型 × 凭据)，占额/合并计量/结算都在服务端。 */
function CredentialProbeModal(props: CredentialProbeModalProps) {
  const { t } = useTranslation();
  const { credential } = props;
  const [models, setModels] = useState<ModelItem[]>([]);
  const [modelsTotal, setModelsTotal] = useState(0);
  const [modelId, setModelId] = useState('');
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);
  const [results, setResults] = useState<ModelProbeResult[]>([]);

  useEffect(() => {
    listModels({ page: 1, page_size: PROBE_MODEL_PAGE_SIZE })
      .then((page) => {
        setModels(page.items);
        setModelsTotal(page.total);
      })
      .catch(() => {
        setModels([]);
        setModelsTotal(0);
      });
  }, []);

  const run = async (): Promise<void> => {
    if (!modelId) {
      return;
    }
    setRunning(true);
    setFailed(false);
    try {
      setResults(await probeCredentials([{ model_id: modelId, credential_id: credential.id }]));
    } catch {
      setResults([]);
      setFailed(true);
    } finally {
      setRunning(false);
    }
  };

  const modelLabels: Record<string, string> = Object.fromEntries(
    models.map((model) => [model.id, `${model.key} · ${model.name}`])
  );

  return (
    <Modal
      centered
      visible
      width={720}
      title={`${t('model.credential.probe.title')} · ${credential.key}`}
      okText={t('model.credential.probe.run')}
      cancelText={t('common.cancel')}
      okButtonProps={{ loading: running, disabled: modelId === '' }}
      onOk={() => void run()}
      onCancel={props.onCancel}
    >
      <div className="detail-hint">{t('model.credential.probe.modelHint')}</div>
      <Select
        value={modelId === '' ? undefined : modelId}
        onChange={(value) => setModelId(String(value))}
        style={{ width: '100%' }}
        placeholder={t('model.credential.probe.model')}
        optionList={models.map((model) => ({
          value: model.id,
          label: `${model.key} · ${model.name}`
        }))}
      />
      {modelsTotal > models.length ? (
        <div className="detail-hint" data-testid="credential-probe-truncated">
          {t('model.credential.probe.pickerTruncated', { shown: models.length, total: modelsTotal })}
        </div>
      ) : null}
      {failed ? <Banner type="danger" closeIcon={null} description={t('model.credential.probe.failed')} /> : null}
      {results.length === 0 ? (
        <Typography.Paragraph type="tertiary">{t('model.credential.probe.empty')}</Typography.Paragraph>
      ) : (
        <Table
          dataSource={results}
          rowKey="model_id"
          pagination={false}
          columns={[
            {
              title: t('model.credential.probe.columns.model'),
              dataIndex: 'model_id',
              render: (value: string) => <TableText>{modelLabels[value] ?? value}</TableText>
            },
            {
              title: t('model.credential.probe.columns.status'),
              dataIndex: 'status',
              render: (value: ModelProbeResult['status']) => (
                <StatusTag status={value} options={probeStatusOptions(t)} />
              )
            },
            { title: t('model.credential.probe.columns.latency'), dataIndex: 'latency_ms', width: 96 },
            {
              title: t('model.credential.probe.columns.errorCode'),
              dataIndex: 'error_code',
              render: (value: string | null) => value ?? '-'
            },
            {
              title: t('model.credential.probe.columns.testedAt'),
              dataIndex: 'tested_at',
              width: 176,
              render: (value: string) => <DateTimeText value={value} />
            }
          ]}
        />
      )}
      <div className="detail-hint">{t('model.credential.probe.memberOnly')}</div>
    </Modal>
  );
}

interface CredentialTableProps {
  items: ModelCredentialItem[];
  loading: boolean;
  quotaGroupLabel(quotaGroupId: string | null): string;
  onEdit(credential: ModelCredentialItem): void;
  onProbe(credential: ModelCredentialItem): void;
  onDelete(credential: ModelCredentialItem): void;
}

function CredentialTable(props: CredentialTableProps) {
  const { t } = useTranslation();
  return (
    <DataTable<ModelCredentialItem>
      className="app-table-fit app-table-nowrap"
      scroll={{ x: '100%' }}
      loading={props.loading}
      dataSource={props.items}
      rowKey="id"
      pagination={false}
      empty={<EmptyState title={t('model.credential.empty')} description={t('common.emptyHint')} />}
      columns={[
        {
          title: t('model.credential.columns.key'),
          dataIndex: 'key',
          width: 'auto',
          ellipsis: true,
          render: (value: string) => <TableText>{value}</TableText>
        },
        { title: t('model.credential.columns.provider'), dataIndex: 'provider_key', width: 120, ellipsis: true },
        { title: t('model.credential.columns.baseUrl'), dataIndex: 'base_url', width: 'auto', ellipsis: true },
        {
          title: t('model.credential.columns.quotaGroup'),
          dataIndex: 'quota_group_id',
          width: 160,
          ellipsis: true,
          render: (value: string | null) => <TableText>{props.quotaGroupLabel(value)}</TableText>
        },
        { title: t('model.credential.columns.maxInflight'), dataIndex: 'max_inflight', width: 96 },
        {
          title: t('model.credential.columns.apiKey'),
          dataIndex: 'configured',
          width: 96,
          render: (_: unknown, credential: ModelCredentialItem) => (
            <StatusTag status={String(credential.configured)} options={apiKeyOptions(t)} />
          )
        },
        {
          title: t('model.credential.columns.enabled'),
          dataIndex: 'enabled',
          width: 96,
          render: (value: boolean) => <StatusTag status={value} options={enabledOptions(t)} />
        },
        {
          title: t('model.credential.columns.revision'),
          dataIndex: 'revision',
          width: 88,
          render: (value: number) => `r${value}`
        },
        {
          ...actionColumn(168),
          title: t('model.credential.columns.actions'),
          render: (_: unknown, credential: ModelCredentialItem) => (
            <div className="model-table-actions">
              <Button
                theme="borderless"
                data-testid={`credential-edit-${credential.key}`}
                onClick={() => props.onEdit(credential)}
              >
                {t('model.credential.actions.edit')}
              </Button>
              <Button
                theme="borderless"
                data-testid={`credential-test-${credential.key}`}
                onClick={() => props.onProbe(credential)}
              >
                {t('model.credential.actions.test')}
              </Button>
              <ConfirmAction
                title={t('model.credential.confirmDelete')}
                testId={`credential-delete-${credential.key}`}
                onConfirm={() => props.onDelete(credential)}
              >
                {t('model.credential.actions.delete')}
              </ConfirmAction>
            </div>
          )
        }
      ]}
    />
  );
}

/** 凭据列表状态：请求竞态守卫 + 分页（列表页加载的统一形态）。 */
function useCredentialList(isAdmin: boolean) {
  const [items, setItems] = useState<ModelCredentialItem[]>([]);
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
      const result = await listCredentials({ page, page_size: pageSize });
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

export interface ModelCredentialPanelProps {
  /** 维护成功后的通知（模型列表的成员可用性可能随之变化）；缺省无操作。 */
  onChanged?(): void;
}

export function ModelCredentialPanel(props: ModelCredentialPanelProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const list = useCredentialList(isAdmin);
  const [quotaGroups, setQuotaGroups] = useState<ModelQuotaGroupOption[]>([]);
  const [formVisible, setFormVisible] = useState(false);
  const [editing, setEditing] = useState<ModelCredentialItem | null>(null);
  const [probeCredential, setProbeCredential] = useState<ModelCredentialItem | null>(null);
  const [actionErrorKey, setActionErrorKey] = useState<string | null>(null);

  // 归属选项加载失败只影响「可选额度组」：留空（独占自身容量）仍然可用，不阻塞维护面
  useEffect(() => {
    if (!isAdmin) {
      return;
    }
    listQuotaGroups()
      .then((result) => setQuotaGroups(result.items))
      .catch(() => setQuotaGroups([]));
  }, [isAdmin]);

  const quotaGroupLabel = (quotaGroupId: string | null): string => {
    if (!quotaGroupId) {
      return t('model.credential.quota.exclusive');
    }
    return quotaGroups.find((group) => group.id === quotaGroupId)?.key ?? quotaGroupId;
  };

  const afterMutation = (): void => {
    setActionErrorKey(null);
    void list.reload();
    props.onChanged?.();
  };

  const remove = async (credential: ModelCredentialItem): Promise<void> => {
    setActionErrorKey(null);
    try {
      await deleteCredential(credential.id);
    } catch (error) {
      // 仍被成员引用等冲突：共享传输已提示后端文案，这里保留面板内定位
      setActionErrorKey(credentialSaveErrorKey(error));
      return;
    }
    afterMutation();
  };

  if (!isAdmin) {
    return null;
  }

  return (
    <div data-testid="credential-panel">
      <ModuleToolbar
        actions={
          <Button
            theme="solid"
            icon={<IconPlus />}
            data-testid="create-credential"
            onClick={() => {
              setEditing(null);
              setFormVisible(true);
            }}
          >
            {t('model.credential.add')}
          </Button>
        }
      />
      <div className="detail-hint">{t('model.credential.subtitle')}</div>
      {actionErrorKey === null ? null : (
        <Banner type="danger" closeIcon={null} description={t(actionErrorKey)} />
      )}
      {list.failed ? (
        <ErrorState onRetry={() => void list.reload()} />
      ) : (
        <CredentialTable
          items={list.items}
          loading={list.loading}
          quotaGroupLabel={quotaGroupLabel}
          onEdit={(credential) => {
            setEditing(credential);
            setFormVisible(true);
          }}
          onProbe={setProbeCredential}
          onDelete={(credential) => void remove(credential)}
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
      <ModelCredentialForm
        visible={formVisible}
        credential={editing}
        quotaGroups={quotaGroups}
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
      {probeCredential === null ? null : (
        <CredentialProbeModal credential={probeCredential} onCancel={() => setProbeCredential(null)} />
      )}
    </div>
  );
}
