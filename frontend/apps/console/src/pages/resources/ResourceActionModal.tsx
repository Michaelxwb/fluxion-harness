import { Button, Modal, Space, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

export type ConfirmAction =
  | { readonly type: "publish"; readonly resource: ResourceVersion }
  | { readonly type: "rollback"; readonly resource: ResourceVersion; readonly targetVersion: string };

interface ResourceActionModalProps {
  readonly action: ConfirmAction | null;
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onDone: (message: string, resource: ResourceVersion) => Promise<void>;
}

export function ResourceActionModal({ action, api, onClose, onDone }: ResourceActionModalProps) {
  const title = action?.type === "rollback" ? "确认回滚" : "确认发布";
  return (
    <Modal
      footer={<ModalFooter confirmText={title} onCancel={onClose} onConfirm={() => void confirm()} />}
      onCancel={onClose}
      title={title}
      visible={Boolean(action)}
    >
      {action ? <ConfirmBody action={action} /> : null}
    </Modal>
  );

  async function confirm(): Promise<void> {
    if (!action) {
      return;
    }
    if (action.type === "publish") {
      await api.publishVersion(action.resource);
      onClose();
      await onDone(`已发布 ${action.resource.version}`, action.resource);
      return;
    }
    await api.rollbackVersion(action.resource, action.targetVersion);
    onClose();
    await onDone(`已回滚到 ${action.targetVersion}`, action.resource);
  }
}

function ModalFooter({
  confirmText,
  onCancel,
  onConfirm
}: {
  readonly confirmText: string;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}) {
  return (
    <Space>
      <Button aria-label="取消" onClick={onCancel}>
        取消
      </Button>
      <Button aria-label={confirmText} onClick={onConfirm} theme="solid" type="primary">
        {confirmText}
      </Button>
    </Space>
  );
}

function ConfirmBody({ action }: { readonly action: ConfirmAction }) {
  if (action.type === "publish") {
    return (
      <Space vertical align="start">
        <Typography.Text>{`${action.resource.resourceType}/${action.resource.resourceId}`}</Typography.Text>
        <Typography.Text>{action.resource.version}</Typography.Text>
      </Space>
    );
  }
  return (
    <Space vertical align="start">
      <Typography.Text>{action.resource.resourceId}</Typography.Text>
      <Typography.Text>{action.targetVersion}</Typography.Text>
    </Space>
  );
}
