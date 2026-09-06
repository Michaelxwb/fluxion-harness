import { useMemo } from "react";

import { Modal, Table, Typography } from "@douyinfe/semi-ui";

import type { JsonRecord } from "../types/console";

interface SpecDiffModalProps {
  readonly onClose: () => void;
  readonly visible: boolean;
  /** 左侧（旧）版本标识与 spec。 */
  readonly left: { readonly label: string; readonly spec: JsonRecord } | null;
  /** 右侧（新）版本标识与 spec。 */
  readonly right: { readonly label: string; readonly spec: JsonRecord } | null;
}

interface DiffRow {
  readonly change: "added" | "removed" | "changed";
  readonly key: string;
  readonly left: string;
  readonly right: string;
}

/** golden-path-closure TASK-024（§14 P2）：Version Diff——两版本 spec 键级变更
 * 摘要（顶层键 added/removed/changed），接入标准版本历史交互。 */
export function SpecDiffModal({ left, onClose, right, visible }: SpecDiffModalProps) {
  const rows = useMemo<readonly DiffRow[]>(() => {
    if (!left || !right) return [];
    const keys = new Set([...Object.keys(left.spec), ...Object.keys(right.spec)]);
    const result: DiffRow[] = [];
    for (const key of [...keys].sort()) {
      const inLeft = key in left.spec;
      const inRight = key in right.spec;
      const leftText = inLeft ? JSON.stringify(left.spec[key]) : "-";
      const rightText = inRight ? JSON.stringify(right.spec[key]) : "-";
      if (!inLeft && inRight) result.push({ change: "added", key, left: "-", right: rightText });
      else if (inLeft && !inRight) result.push({ change: "removed", key, left: leftText, right: "-" });
      else if (leftText !== rightText) result.push({ change: "changed", key, left: leftText, right: rightText });
    }
    return result;
  }, [left, right]);

  return (
    <Modal
      cancelText="关 闭"
      footer={null}
      onCancel={onClose}
      title={left && right ? `版本对比 · ${left.label} → ${right.label}` : "版本对比"}
      visible={visible}
      width={760}
    >
      <div aria-label="版本对比">
        {rows.length === 0 ? (
          <Typography.Text type="tertiary">两版本 spec 无顶层键级差异</Typography.Text>
        ) : (
          <Table<DiffRow>
            columns={[
              {
                dataIndex: "change",
                render: (change: DiffRow["change"]) => (
                  <Typography.Text
                    type={change === "removed" ? "danger" : change === "added" ? "success" : "warning"}
                  >
                    {change === "added" ? "新增" : change === "removed" ? "移除" : "变更"}
                  </Typography.Text>
                ),
                title: "变更类型"
              },
              { dataIndex: "key", title: "键" },
              {
                dataIndex: "left",
                render: (value: string) => (
                  <Typography.Text code style={{ fontSize: 12, wordBreak: "break-all" }}>
                    {value}
                  </Typography.Text>
                ),
                title: left?.label ?? "旧版本"
              },
              {
                dataIndex: "right",
                render: (value: string) => (
                  <Typography.Text code style={{ fontSize: 12, wordBreak: "break-all" }}>
                    {value}
                  </Typography.Text>
                ),
                title: right?.label ?? "新版本"
              }
            ]}
            dataSource={[...rows]}
            pagination={false}
            rowKey="key"
            size="small"
          />
        )}
      </div>
    </Modal>
  );
}
