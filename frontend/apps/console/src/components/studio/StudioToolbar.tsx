/**
 * C403 StudioToolbar（TASK-012 / CMP-09）：校验/发布动作条（复用现有工作流动作语义；
 * 发布需先通过校验）。校验诊断（E-02 逐字段定位）在下方渲染。
 */
import { Button, Space, Typography } from "@douyinfe/semi-ui";
import { IconPlay, IconSave } from "@douyinfe/semi-icons";

import type { WorkflowV2Diagnostic } from "../../types/console";

interface StudioToolbarProps {
  readonly canPublish: boolean;
  readonly diagnostics: readonly WorkflowV2Diagnostic[];
  readonly onSave: () => void;
  readonly onValidate: () => void;
  readonly onPublish: () => void;
}

export function StudioToolbar({
  canPublish,
  diagnostics,
  onSave,
  onValidate,
  onPublish
}: StudioToolbarProps) {
  return (
    <div className="studio-toolbar">
      <Space wrap>
        <Button aria-label="保存草稿" icon={<IconSave />} onClick={onSave} type="primary">
          保存草稿
        </Button>
        <Button aria-label="校验" icon={<IconPlay />} onClick={onValidate}>
          校验
        </Button>
        <Button
          aria-label="发布"
          disabled={!canPublish}
          icon={<IconPlay />}
          onClick={onPublish}
          theme="solid"
          type="warning"
        >
          发布
        </Button>
      </Space>
      {diagnostics.length > 0 ? (
        <ul aria-label="校验诊断" className="studio-diagnostics">
          {diagnostics.map((diagnostic, index) => (
            <li key={index}>
              <Typography.Text type="danger">
                {diagnostic.nodeId ? `${diagnostic.nodeId}.${diagnostic.field}` : diagnostic.field}
                ：{diagnostic.message}
              </Typography.Text>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
