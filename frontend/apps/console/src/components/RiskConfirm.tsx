import { useState } from "react";

import { Input, Modal, Space, Typography } from "@douyinfe/semi-ui";

interface RiskConfirmProps {
  readonly visible: boolean;
  readonly title: string;
  /** 影响范围逐条说明。 */
  readonly impact: readonly string[];
  /** 要求输入该名称才可确认（二次确认），不传则直接确认。 */
  readonly requireName?: string;
  readonly confirmText?: string;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

/** 高风险操作统一确认：影响范围 + 可选输入资源名二次确认。 */
export function RiskConfirm({
  visible,
  title,
  impact,
  requireName,
  confirmText,
  onConfirm,
  onCancel
}: RiskConfirmProps) {
  const [input, setInput] = useState("");
  const matched = requireName === undefined || input === requireName;
  function close(): void {
    setInput("");
    onCancel();
  }
  function confirm(): void {
    if (!matched) return;
    setInput("");
    onConfirm();
  }
  const confirmLabel = confirmText ?? title;
  return (
    <Modal
      okButtonProps={{ "aria-label": confirmLabel, disabled: !matched }}
      okText={confirmLabel}
      okType="danger"
      onCancel={close}
      onOk={confirm}
      title={title}
      visible={visible}
    >
      <Space vertical align="start">
        <Typography.Text strong>该操作将产生以下影响：</Typography.Text>
        <ul className="risk-confirm__impact">
          {impact.map((item) => (
            <li key={item}>
              <Typography.Text>{item}</Typography.Text>
            </li>
          ))}
        </ul>
        {requireName === undefined ? null : (
          <>
            <Typography.Text>
              请输入 <Typography.Text code>{requireName}</Typography.Text> 以确认：
            </Typography.Text>
            <Input
              aria-label="确认名称"
              onChange={setInput}
              placeholder={requireName}
              value={input}
            />
          </>
        )}
      </Space>
    </Modal>
  );
}
