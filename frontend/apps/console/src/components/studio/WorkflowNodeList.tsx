/** C403 WorkflowNodeList（TASK-012 / CMP-09）：节点列表（增/删/选择）；props 只读、事件上抛。 */
import { Button, Empty, Tag } from "@douyinfe/semi-ui";
import { IconDelete, IconPlus } from "@douyinfe/semi-icons";

import type { WorkflowV2Node } from "../../types/console";

interface WorkflowNodeListProps {
  readonly nodes: readonly WorkflowV2Node[];
  /** 选择/删除按索引寻址：编辑期节点 id 可变（清空重输），索引在增删前保持稳定。 */
  readonly selectedIndex: number | null;
  readonly onSelect: (index: number) => void;
  readonly onAdd: () => void;
  readonly onRemove: (index: number) => void;
}

export function WorkflowNodeList({
  nodes,
  selectedIndex,
  onSelect,
  onAdd,
  onRemove
}: WorkflowNodeListProps) {
  return (
    <section aria-label="节点列表" className="workflow-node-list">
      {nodes.length === 0 ? (
        <Empty description="暂无节点，点击添加" />
      ) : (
        <ul className="node-rows">
          {nodes.map((node, index) => (
            <li className={index === selectedIndex ? "node-row selected" : "node-row"} key={index}>
              <Button
                aria-label={`选择节点 ${node.id || `#${index + 1}`}`}
                onClick={() => onSelect(index)}
                theme="light"
              >
                <span className="node-id">{node.id || `#${index + 1}`}</span>
                <Tag color="cyan" size="small">
                  {node.type}
                </Tag>
              </Button>
              <Button
                aria-label={`删除节点 ${node.id || `#${index + 1}`}`}
                icon={<IconDelete />}
                onClick={() => onRemove(index)}
                type="danger"
              />
            </li>
          ))}
        </ul>
      )}
      <Button aria-label="添加节点" icon={<IconPlus />} onClick={onAdd} theme="solid">
        添加节点
      </Button>
    </section>
  );
}
