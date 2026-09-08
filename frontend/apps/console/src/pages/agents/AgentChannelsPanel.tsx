import { useCallback, useEffect, useState } from "react";

import {
  Banner,
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Toast,
  Typography
} from "@douyinfe/semi-ui";
import { IconCopy, IconExternalOpen, IconPlus } from "@douyinfe/semi-icons";

import type {
  AgentWebChannel,
  ChannelVerifyResult,
  ConsoleApi
} from "../../types/console";
import { useRemoteUserOptions } from "../../components/useRemoteResourceOptions";
import { RelativeTime } from "../../components/RelativeTime";

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
  const [selectedUser, setSelectedUser] = useState("");
  const [openModal, setOpenModal] = useState(false);
  // FEAT-03：入口用户远程搜索（大数据集可达；已授权排除仍在页内执行）。
  const userOptions = useRemoteUserOptions(api, openModal);
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [issuedFor, setIssuedFor] = useState<string | null>(null);
  const [verify, setVerify] = useState<ChannelVerifyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const web = await api.listAgentChannels(agentId);
      setChannel(web);
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
      setIssuedFor(selectedUser);
      setOpenModal(false);
      setSelectedUser("");
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "入口生成失败");
    } finally {
      setBusy(false);
    }
  }

  function closeIssued(): void {
    setIssuedToken(null);
    setIssuedFor(null);
  }

  async function copyIssuedLink(link: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(link);
      Toast.success("入口链接已复制");
    } catch {
      Toast.error("复制失败，请手动复制");
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
  const selectable = userOptions.options.filter((user) => !authorized.has(user.resourceId));

  return (
    <div aria-label="Agent 渠道" style={{ display: "grid", gap: 16 }}>
      <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: 12 }}>
        <Typography.Title heading={5} style={{ margin: 0 }}>
          Web Chat
        </Typography.Title>
        <Tag color={channel?.status === "active" ? "green" : "grey"}>
          {channel?.status === "active" ? "可用" : "未发布"}
        </Tag>
        <Typography.Text type="tertiary">
          内置对话通道：为已授权用户开通专属入口，对方即可经 Web Chat 与此智能体对话。
        </Typography.Text>
      </div>

      <Space wrap>
        <Button aria-label="开通并生成入口" icon={<IconPlus />} onClick={() => setOpenModal(true)} theme="solid" type="primary">
          开通并生成入口
        </Button>
        <Button loading={busy} onClick={() => void runVerify()}>
          验证渠道
        </Button>
      </Space>

      <div style={{ display: "grid", gap: 8 }}>
        <Typography.Text strong>IM 通道</Typography.Text>
        <Space wrap>
          <Tag color="grey">企业微信 · 暂未开放</Tag>
          <Tag color="grey">Mattermost · 暂未开放</Tag>
          <Tag color="grey">微信 · 暂未开放</Tag>
        </Space>
        <Typography.Text size="small" type="tertiary">
          企业微信 / Mattermost / 微信后端仅有鉴权器，入站链路尚未开放，故不提供开通开关。
        </Typography.Text>
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
            filter={false}
            loading={userOptions.loading}
            onChange={(value) => setSelectedUser(String(value ?? ""))}
            onSearch={userOptions.onSearch}
            optionList={selectable.map((user) => ({
              label: user.label,
              value: user.resourceId
            }))}
            outerBottomSlot={
              userOptions.truncated ? (
                <Typography.Text type="tertiary" size="small">
                  仅显示前 {userOptions.options.length} 条匹配，请细化关键词
                </Typography.Text>
              ) : undefined
            }
            placeholder="输入关键词搜索用户"
            remote
            style={{ width: "100%" }}
            value={selectedUser}
          />
        </div>
      </Modal>

      <Modal
        cancelText="关 闭"
        footer={null}
        onCancel={closeIssued}
        title="Web Chat 渠道入口"
        visible={issuedToken !== null}
        width={640}
      >
        {issuedToken ? (
          <div style={{ display: "grid", gap: 12 }}>
            <Typography.Text type="tertiary">
              {issuedFor ? `已为用户「${issuedFor}」生成专属入口：` : "专属入口已生成："}
            </Typography.Text>
            <Input
              aria-label="Web Chat 入口链接"
              readOnly
              value={`${window.location.origin}/chat/#/${issuedToken}`}
            />
            <Space>
              <Button
                aria-label="复制链接"
                icon={<IconCopy />}
                onClick={() => void copyIssuedLink(`${window.location.origin}/chat/#/${issuedToken}`)}
                theme="solid"
                type="primary"
              >
                复制链接
              </Button>
              <Button
                aria-label="打开对话"
                icon={<IconExternalOpen />}
                onClick={() => window.open(`${window.location.origin}/chat/#/${issuedToken}`, "_blank", "noopener,noreferrer")}
              >
                打开对话
              </Button>
            </Space>
            <Banner
              description="链接仅本次显示 token，复制后请妥善保存；撤销入口后立即失效。"
              type="warning"
            />
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
              render: (value: string) => <RelativeTime value={value} />,
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
