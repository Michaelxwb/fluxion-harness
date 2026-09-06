import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Empty,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Table,
  Tag,
  Typography
} from "@douyinfe/semi-ui";

import type {
  AgentWebChannel,
  ChannelVerifyResult,
  ConsoleApi,
  PlatformUser
} from "../../types/console";

interface AgentChannelsPanelProps {
  readonly agentId: string;
  readonly api: ConsoleApi;
}

/** TASK-014（§9.2）：Agent → 渠道 tab。
 *
 * Web Chat 是正式 Channel（规则 15）：渠道列表（类型/状态 Tag）+ 开通入口
 * （选择已授权用户 → 签发 chat access → 入口链接仅本次显示）+ 验证渠道
 * （真实 resolve 链检查）+ 撤销入口（二次确认，链接立即失效）。
 * 企业微信/Mattermost/微信：后端仅鉴权器、无入站链路——诚实呈现「暂未开放」，
 * 不提供假开关。
 */
export function AgentChannelsPanel({ agentId, api }: AgentChannelsPanelProps) {
  const [channel, setChannel] = useState<AgentWebChannel | null>(null);
  const [users, setUsers] = useState<readonly PlatformUser[]>([]);
  const [selectedUser, setSelectedUser] = useState("");
  const [openModal, setOpenModal] = useState(false);
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [verify, setVerify] = useState<ChannelVerifyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const [web, userPage] = await Promise.all([
        api.listAgentChannels(agentId),
        api.listPlatformUsers({ page: 1, pageSize: 100 })
      ]);
      setChannel(web);
      setUsers(userPage.items);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "渠道数据加载失败");
    }
  }, [agentId, api]);

  useEffect(() => {
    setChannel(null);
    setVerify(null);
    void reload();
  }, [reload]);

  async function openChannel(): Promise<void> {
    if (!selectedUser) {
      setError("请选择用户");
      return;
    }
    setBusy(true);
    try {
      const issued = await api.issueChatAccess(selectedUser, agentId);
      setIssuedToken(issued.token);
      setOpenModal(false);
      setSelectedUser("");
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "入口生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function runVerify(): Promise<void> {
    setBusy(true);
    try {
      setVerify(await api.verifyAgentWebChannel(agentId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "渠道验证失败");
    } finally {
      setBusy(false);
    }
  }

  async function revokeEntry(platformUserId: string): Promise<void> {
    setBusy(true);
    try {
      const target = channel?.entries.find(
        (entry) => entry.platformUserId === platformUserId
      );
      if (target) await api.revokeChatAccess(target.accessId);
      setVerify(null);
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "撤销失败");
    } finally {
      setBusy(false);
    }
  }

  if (channel === null && !error) return <Spin />;

  const authorized = new Set(
    (channel?.entries ?? []).map((entry) => entry.platformUserId)
  );
  const selectable = users.filter((user) => !authorized.has(user.platformUserId));

  return (
    <div aria-label="Agent 渠道" style={{ display: "grid", gap: 16 }}>
      <div style={{ alignItems: "center", display: "flex", gap: 12 }}>
        <Typography.Text strong>Web Chat</Typography.Text>
        <Tag color={channel?.status === "active" ? "green" : "grey"}>
          {channel?.status === "active" ? "可用" : "未发布"}
        </Tag>
        <Button onClick={() => setOpenModal(true)} theme="solid" type="primary">
          开通并生成入口
        </Button>
        <Button loading={busy} onClick={() => void runVerify()}>
          验证渠道
        </Button>
        <Tag color="grey">企业微信 · 暂未开放</Tag>
        <Tag color="grey">Mattermost · 暂未开放</Tag>
        <Tag color="grey">微信 · 暂未开放</Tag>
      </div>

      <Modal
        cancelText="取 消"
        confirmText="确 定"
        okButtonProps={{ disabled: !selectedUser, loading: busy }}
        onOk={() => void openChannel()}
        onCancel={() => setOpenModal(false)}
        title="开通 Web Chat 渠道"
        visible={openModal}
      >
        <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
          <Typography.Text type="tertiary">
            选择已授权用户生成对话入口；入口链接仅本次显示 token，撤销后立即失效。
          </Typography.Text>
          <Select
            aria-label="入口用户"
            filter
            onChange={(value) => setSelectedUser(String(value ?? ""))}
            optionList={selectable.map((user) => ({
              label: `${user.displayName}（${user.platformUserId}）`,
              value: user.platformUserId
            }))}
            placeholder="选择用户"
            style={{ width: "100%" }}
            value={selectedUser}
          />
        </div>
      </Modal>

      <Modal
        cancelText="关 闭"
        footer={null}
        onCancel={() => setIssuedToken(null)}
        title="Web Chat 渠道入口"
        visible={issuedToken !== null}
      >
        {issuedToken ? (
          <div style={{ display: "grid", gap: 8 }}>
            <Typography.Text copyable aria-label="Web Chat 入口链接">
              {`${window.location.origin}/chat/#/${issuedToken}`}
            </Typography.Text>
            <Typography.Text type="warning">
              链接仅本次显示 token，复制后请妥善保存；撤销入口后立即失效。
            </Typography.Text>
          </div>
        ) : null}
      </Modal>

      {channel !== null && channel.entries.length === 0 ? (
        <Empty description="尚无 Web Chat 入口；开通后已授权用户即可经 Web Chat 与此智能体对话" />
      ) : (
        <Table
          aria-label="Web Chat 入口列表"
          columns={[
            { dataIndex: "displayName", title: "用户" },
            { dataIndex: "platformUserId", title: "用户 ID" },
            {
              dataIndex: "createdAt",
              render: (value: string) => new Date(value).toLocaleString(),
              title: "开通时间"
            },
            {
              dataIndex: "platformUserId",
              title: "操作",
              render: (platformUserId: string) => (
                <RevokedEntryButton
                  onConfirm={() => void revokeEntry(platformUserId)}
                  platformUserId={platformUserId}
                />
              )
            }
          ]}
          dataSource={[...(channel?.entries ?? [])]}
          pagination={false}
          rowKey="accessId"
        />
      )}

      {verify ? (
        <div aria-label="渠道验证结果">
          {verify.ok ? (
            <Typography.Text type="success">验证通过</Typography.Text>
          ) : (
            <div>
              <Typography.Text type="danger">验证未通过：</Typography.Text>
              <ul>
                {verify.problems.map((problem) => (
                  <li key={problem}>
                    <Typography.Text type="danger">{problem}</Typography.Text>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : null}

      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
    </div>
  );
}

function RevokedEntryButton({
  platformUserId,
  onConfirm
}: {
  readonly platformUserId: string;
  readonly onConfirm: () => void;
}) {
  return (
    <Popconfirm
      content="撤销后该入口链接立即失效，用户无法再经此入口对话。"
      okButtonProps={{ type: "danger" }}
      okText="确认撤销"
      onConfirm={onConfirm}
      title="确认撤销该 Web Chat 入口？"
    >
      <Button aria-label={`撤销入口 ${platformUserId}`} size="small" type="danger">
        撤销入口
      </Button>
    </Popconfirm>
  );
}
