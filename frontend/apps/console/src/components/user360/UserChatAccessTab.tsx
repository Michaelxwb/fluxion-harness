import { useEffect, useState } from "react";

import { Button, Empty, Space, Table, Toast, Typography } from "@douyinfe/semi-ui";

import { RelativeTime } from "../RelativeTime";
import { RiskConfirm } from "../RiskConfirm";
import type { ConsoleApi, UserChatAccess } from "../../types/console";

interface UserChatAccessTabProps {
  readonly api: ConsoleApi;
  readonly platformUserId: string;
}

interface AccessRow extends UserChatAccess {
  readonly key: string;
  readonly agentName: string;
}

/** 用户详情·对话链接 Tab：该用户签发的专属链接（token 仅签发时可见，此处可撤销）。 */
export function UserChatAccessTab({ api, platformUserId }: UserChatAccessTabProps) {
  const [rows, setRows] = useState<readonly AccessRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<AccessRow | null>(null);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const items = await api.listUserChatAccess(platformUserId);
        const names = new Map<string, string>();
        await Promise.all(
          items.map(async (item) => {
            if (names.has(item.agentId)) return;
            try {
              const detail = await api.getResource("agent_definition", item.agentId);
              const spec = detail.spec as Record<string, unknown>;
              names.set(item.agentId, String(spec.display_name ?? spec.name ?? item.agentId));
            } catch {
              names.set(item.agentId, item.agentId);
            }
          })
        );
        if (!active) return;
        setRows(
          items.map((item) => ({
            ...item,
            agentName: names.get(item.agentId) ?? item.agentId,
            key: item.accessId
          }))
        );
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, platformUserId]);

  async function confirmRevoke(): Promise<void> {
    if (!revoking) return;
    try {
      await api.revokeChatAccess(revoking.accessId);
      setRows((current) => (current ?? []).filter((row) => row.accessId !== revoking.accessId));
      setRevoking(null);
      Toast.success("链接已撤销");
    } catch (cause) {
      Toast.error(cause instanceof Error ? cause.message : "撤销失败");
    }
  }

  if (error !== null) {
    return <Typography.Text type="danger">{error}</Typography.Text>;
  }
  if (rows === null) {
    return <Typography.Text type="tertiary">加载中…</Typography.Text>;
  }
  if (rows.length === 0) {
    return <Empty description="该用户暂无对话链接，可在用户列表签发" />;
  }
  return (
    <div>
      <Table<AccessRow>
        aria-label="用户对话链接表格"
        columns={[
          { dataIndex: "agentName", title: "智能体" },
          {
            dataIndex: "createdAt",
            render: (value: string) => <RelativeTime value={value} />,
            title: "签发时间"
          },
          {
            render: (_value: unknown, record: AccessRow) => (
              <Space>
                <Button
                  aria-label={`撤销链接 ${record.accessId}`}
                  onClick={() => setRevoking(record)}
                  size="small"
                  type="danger"
                >
                  撤销
                </Button>
              </Space>
            ),
            title: "操作"
          }
        ]}
        dataSource={[...rows]}
        pagination={false}
        rowKey="key"
      />
      {revoking ? (
        <RiskConfirm
          impact={["该链接会立即失效", "用户无法继续通过该链接对话"]}
          onCancel={() => setRevoking(null)}
          onConfirm={() => void confirmRevoke()}
          title="撤销对话链接"
          visible
        />
      ) : null}
    </div>
  );
}
