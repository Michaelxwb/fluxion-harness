import { useEffect, useState } from "react";

import { Button, Input, Modal, Select, Space, Toast, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi } from "../../types/console";
import type { CredentialRow } from "./credentialRow";
import { CREDENTIAL_PURPOSES } from "./credentialRow";

interface SharedModalProps {
  readonly api: ConsoleApi;
  readonly row: CredentialRow | null;
  readonly onClose: () => void;
  readonly onDone: () => void;
}

/** TASK-009 行操作·编辑（元数据）：简单对象允许 Edit Modal（§7.3），
 * 但不在 Detail SideSheet 内编辑。仅改名称/类型（用途），不触碰 SecretRef。 */
export function EditCredentialModal({ api, row, onClose, onDone }: SharedModalProps) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (row === null) return;
    setName(row.displayName);
    setPurpose(row.purpose);
    setError(null);
  }, [row]);

  async function submit(): Promise<void> {
    if (row === null) return;
    if (!name.trim()) {
      setError("凭据名称：必填");
      return;
    }
    if (!purpose.trim()) {
      setError("类型 / 用途：必填（选择预设或自定义输入）");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const working = await api.createDraftFromLatest("secret", row.resourceId);
      await api.updateDraft(working, {
        ...working.spec,
        name: name.trim(),
        purpose: purpose.trim()
      });
      onDone();
      try {
        Toast.success("凭据元数据已更新");
      } catch {
        // jsdom/无 Toast 容器下渲染失败不阻断
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            aria-label="保存凭据元数据"
            loading={submitting}
            onClick={() => void submit()}
            theme="solid"
            type="primary"
          >
            保存
          </Button>
        </Space>
      }
      motion={false}
      onCancel={onClose}
      title="编辑凭据元数据"
      visible={row !== null}
    >
      <div style={{ display: "grid", rowGap: 16 }}>
        <div>
          <Typography.Text>名称 *</Typography.Text>
          <Input
            aria-label="凭据名称"
            onChange={(value) => {
              setName(String(value));
              setError(null);
            }}
            value={name}
          />
        </div>
        <div>
          <Typography.Text>类型 / 用途 *</Typography.Text>
          <Select
            allowCreate
            aria-label="凭据用途"
            onChange={(value) => setPurpose(String(value ?? ""))}
            optionList={CREDENTIAL_PURPOSES.map((item) => ({ label: item, value: item }))}
            placeholder="选择预设或自定义输入"
            style={{ width: "100%" }}
            value={purpose || undefined}
          />
        </div>
        <Typography.Text type="tertiary">
          仅编辑元数据；SecretRef 与密文由创建/轮换管理（规则 17：明文不回显）。
        </Typography.Text>
        {error ? (
          <Typography.Text type="danger" role="alert">
            {error}
          </Typography.Text>
        ) : null}
      </div>
    </Modal>
  );
}

/** TASK-009 行操作·轮换：输入新 Secret（明文只写）；影响说明内嵌（前端规范 8）。 */
export function RotateCredentialModal({ api, row, onClose, onDone }: SharedModalProps) {
  const [secret, setSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (row === null) return;
    setSecret("");
    setError(null);
  }, [row]);

  async function submit(): Promise<void> {
    if (row === null) return;
    if (!secret.trim()) {
      setError("新 Secret：必填");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.rotateCredential(row.resourceId, secret.trim());
      onDone();
      try {
        Toast.success("凭据已轮换");
      } catch {
        // jsdom/无 Toast 容器下渲染失败不阻断
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "轮换失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            aria-label="确认轮换凭据"
            loading={submitting}
            onClick={() => void submit()}
            theme="solid"
            type="warning"
          >
            确认轮换
          </Button>
        </Space>
      }
      motion={false}
      onCancel={onClose}
      title="轮换凭据"
      visible={row !== null}
    >
      <div style={{ display: "grid", rowGap: 16 }}>
        <Typography.Text type="warning">
          影响说明：轮换将生成新版本 SecretRef；已发布连接仍指向旧引用，需更新并重新发布后才会使用新密钥。
        </Typography.Text>
        <div>
          <Typography.Text>新 Secret *</Typography.Text>
          <Input
            aria-label="新凭据 Secret"
            mode="password"
            onChange={(value) => {
              setSecret(String(value));
              setError(null);
            }}
            placeholder="明文只写一次，保存后不回显"
            value={secret}
          />
        </div>
        {error ? (
          <Typography.Text type="danger" role="alert">
            {error}
          </Typography.Text>
        ) : null}
      </div>
    </Modal>
  );
}

/** TASK-009 行操作·禁用：二次确认 + 影响说明（前端强制规范 8）。 */
export function DisableCredentialModal({ api, row, onClose, onDone }: SharedModalProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (row === null) return;
    setError(null);
  }, [row]);

  async function submit(): Promise<void> {
    if (row === null) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.disableCredential(row.resourceId);
      onDone();
      try {
        Toast.success("凭据已禁用");
      } catch {
        // jsdom/无 Toast 容器下渲染失败不阻断
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "禁用失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            aria-label="确认禁用凭据"
            loading={submitting}
            onClick={() => void submit()}
            theme="solid"
            type="danger"
          >
            确认禁用
          </Button>
        </Space>
      }
      motion={false}
      onCancel={onClose}
      title="禁用凭据"
      visible={row !== null}
    >
      <div style={{ display: "grid", rowGap: 16 }}>
        <Typography.Text type="danger">
          影响说明：禁用后引用该凭据的连接在解析时将 fail-closed（不可再用该密钥调用）；
          正在执行的运行按其 Snapshot 冻结引用继续。此操作通过二次确认执行。
        </Typography.Text>
        {error ? (
          <Typography.Text type="danger" role="alert">
            {error}
          </Typography.Text>
        ) : null}
      </div>
    </Modal>
  );
}
