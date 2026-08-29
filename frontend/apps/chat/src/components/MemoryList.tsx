/**
 * 展示组件：Memory 列表（§3.4 契约：items/learningEnabled 只读，
 * onCorrect/onDelete/onToggleLearning 事件上抛；删除须二次确认）。
 */
import { useState } from "react";

import { Button, Empty, Input, Modal, Switch, Tag, Typography } from "@douyinfe/semi-ui";

import type { PersonalMemoryItem } from "../types/chat";

interface MemoryListProps {
  readonly items: readonly PersonalMemoryItem[];
  readonly learningEnabled: boolean;
  readonly onCorrect: (memoryId: string, corrected: string) => void;
  readonly onDelete: (memoryId: string) => void;
  readonly onToggleLearning: (enabled: boolean) => void;
}

export function MemoryList({
  items,
  learningEnabled,
  onCorrect,
  onDelete,
  onToggleLearning
}: MemoryListProps) {
  return (
    <section aria-label="个人记忆" className="memory-list">
      <div className="memory-head">
        <Typography.Title heading={5}>个人记忆</Typography.Title>
        <span className="learning-switch">
          <Typography.Text>自动学习</Typography.Text>
          <Switch
            aria-label="自动学习"
            checked={learningEnabled}
            onChange={(checked) => onToggleLearning(checked)}
          />
        </span>
      </div>
      {items.length === 0 ? (
        <Empty description="暂无记忆" />
      ) : (
        <ul className="memory-items">
          {items.map((item) => (
            <MemoryRow item={item} key={item.memoryId} onCorrect={onCorrect} onDelete={onDelete} />
          ))}
        </ul>
      )}
    </section>
  );
}

interface MemoryRowProps {
  readonly item: PersonalMemoryItem;
  readonly onCorrect: (memoryId: string, corrected: string) => void;
  readonly onDelete: (memoryId: string) => void;
}

function MemoryRow({ item, onCorrect, onDelete }: MemoryRowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.content);
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="memory-row">
      {editing ? (
        <>
          <Input aria-label="纠正内容" onChange={setDraft} value={draft} />
          <Button
            onClick={() => {
              onCorrect(item.memoryId, draft);
              setEditing(false);
            }}
            size="small"
            theme="solid"
            type="primary"
          >
            提交纠正
          </Button>
          <Button
            onClick={() => {
              setDraft(item.content);
              setEditing(false);
            }}
            size="small"
          >
            取消
          </Button>
        </>
      ) : (
        <>
          <span className="memory-content">{item.content}</span>
          <Tag color="violet">{item.source}</Tag>
          <Button onClick={() => setEditing(true)} size="small">
            纠正
          </Button>
          <Button
            onClick={() => setConfirming(true)}
            size="small"
            type="danger"
          >
            删除
          </Button>
        </>
      )}
      <Modal
        footer={
          <span>
            <Button onClick={() => setConfirming(false)}>取消</Button>
            <Button
              onClick={() => {
                setConfirming(false);
                onDelete(item.memoryId);
              }}
              style={{ marginLeft: 8 }}
              theme="solid"
              type="danger"
            >
              确认删除
            </Button>
          </span>
        }
        onCancel={() => setConfirming(false)}
        title="确认删除记忆"
        visible={confirming}
      >
        <Typography.Text>删除后不可恢复，确定要删除这条记忆吗？</Typography.Text>
      </Modal>
    </li>
  );
}
