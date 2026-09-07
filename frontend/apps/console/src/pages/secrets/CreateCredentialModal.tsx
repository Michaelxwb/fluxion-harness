import { useState } from "react";

import { Button, Input, Modal, Select, Space, Toast, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi } from "../../types/console";
import { CREDENTIAL_PURPOSES } from "./credentialRow";

interface CreateCredentialModalProps {
  readonly api: ConsoleApi;
  readonly visible: boolean;
  readonly onClose: () => void;
  readonly onCreated: () => void;
}

/** TASK-009：CreateCredentialModal——明文只写不回显（规则 17）。
 *
 * 名称/Secret/用途 → POST /api/v1/credentials；服务端写入 SecretStore 得
 * SecretRef，创建 Secret 元数据资源。提交后明文不进入列表/详情/日志。
 */
export function CreateCredentialModal({
  api,
  visible,
  onClose,
  onCreated
}: CreateCredentialModalProps) {
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");
  const [purpose, setPurpose] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset(): void {
    setName("");
    setSecret("");
    setPurpose("");
    setError(null);
  }

  async function submit(): Promise<void> {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("凭据名称：必填");
      return;
    }
    if (!secret.trim()) {
      setError("Secret：必填");
      return;
    }
    if (!purpose.trim()) {
      setError("用途：必填（选择预设或自定义输入）");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.createCredential({
        name: trimmedName,
        secret: secret.trim(),
        purpose: purpose.trim()
      });
      reset();
      onCreated();
      try {
        Toast.success("凭据已创建");
      } catch {
        // jsdom/无 Toast 容器下渲染失败不阻断
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
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
            aria-label="创建凭据"
            loading={submitting}
            onClick={() => void submit()}
            theme="solid"
            type="primary"
          >
            创建
          </Button>
        </Space>
      }
      motion={false}
      onCancel={onClose}
      title="新增凭据"
      visible={visible}
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
            placeholder="如：openai-key"
            value={name}
          />
        </div>
        <div>
          <Typography.Text>Secret *</Typography.Text>
          <Input
            aria-label="凭据 Secret"
            mode="password"
            onChange={(value) => {
              setSecret(String(value));
              setError(null);
            }}
            placeholder="明文只写一次，保存后不回显"
            value={secret}
          />
        </div>
        <div>
          <Typography.Text>用途 *</Typography.Text>
          <Select
            allowCreate
            aria-label="凭据用途"
            data-testid="purpose-select"
            onChange={(value) => {
              setPurpose(String(value ?? ""));
              setError(null);
            }}
            optionList={CREDENTIAL_PURPOSES.map((item) => ({ label: item, value: item }))}
            placeholder="选择预设或自定义输入，如：模型供应商连接"
            style={{ width: "100%" }}
            value={purpose || undefined}
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
