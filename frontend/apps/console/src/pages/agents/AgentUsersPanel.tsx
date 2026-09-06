import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Table,
  Tag,
  Typography
} from "@douyinfe/semi-ui";

import type {
  AuthorizedUserSummary,
  ConsoleApi,
  IssuedChatAccess
} from "../../types/console";
import { useRemoteUserOptions } from "../../components/useRemoteResourceOptions";

interface AgentUsersPanelProps {
  readonly agentId: string;
  readonly api: ConsoleApi;
}

/** TASK-013（§9.1）：Agent → 用户授权 tab。
 *
 * - 添加/移除用户授权（产品投影，不暴露 Binding 内部结构）；
 * - Agent Access 列表 + 用户级 Capability 差异（交集生效 / 相对增项）；
 * - 行内签发对话链接（真实 Chat 授权链入口）；
 * - 移除授权为高风险操作：二次确认 + 影响说明（已签发链接立即失效）。
 */
export function AgentUsersPanel({ agentId, api }: AgentUsersPanelProps) {
  const [rows, setRows] = useState<readonly AuthorizedUserSummary[] | null>(null);
  // FEAT-03：授权候选用户远程搜索（大数据集可达；已授权排除仍在页内执行）。
  const userOptions = useRemoteUserOptions(api, true);
  const [selectedUser, setSelectedUser] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newUserId, setNewUserId] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [issued, setIssued] = useState<IssuedChatAccess | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const authorized = await api.listAuthorizedUsers(agentId);
      setRows(authorized);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "授权数据加载失败");
    }
  }, [agentId, api]);

  useEffect(() => {
    setRows(null);
    setSelectedUser("");
    void reload();
  }, [reload]);

  async function createUser(): Promise<void> {
    if (!newUserId.trim()) {
      setError("请输入用户 ID");
      return;
    }
    setBusy(true);
    try {
      const created = await api.createPlatformUser(
        newUserId.trim(),
        newUserName.trim() || newUserId.trim()
      );
      setCreateOpen(false);
      setNewUserId("");
      setNewUserName("");
      setSelectedUser(created.platformUserId);
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "用户创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function authorize(): Promise<void> {
    if (!selectedUser) {
      setError("请选择用户");
      return;
    }
    setBusy(true);
    try {
      await api.authorizeAgentUser(agentId, selectedUser);
      setSelectedUser("");
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "授权失败");
    } finally {
      setBusy(false);
    }
  }

  async function issueLink(platformUserId: string): Promise<void> {
    setBusy(true);
    try {
      setIssued(await api.issueChatAccess(platformUserId, agentId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "对话链接签发失败");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(platformUserId: string): Promise<void> {
    setBusy(true);
    try {
      await api.revokeAgentUserAuthorization(agentId, platformUserId);
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "移除授权失败");
    } finally {
      setBusy(false);
    }
  }

  if (rows === null && !error) return <Spin />;

  const authorizedIds = new Set((rows ?? []).map((row) => row.platformUserId));
  const selectable = userOptions.options.filter((user) => !authorizedIds.has(user.resourceId));

  return (
    <div aria-label="Agent 用户授权" style={{ display: "grid", gap: 16 }}>
      <div style={{ alignItems: "end", display: "grid", gap: 12, gridTemplateColumns: "minmax(240px, 360px) auto auto" }}>
        <div>
          <Typography.Text id="agent-authorize-user-label">授权用户</Typography.Text>
          <Select
            aria-labelledby="agent-authorize-user-label"
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
            placeholder="输入关键词搜索要授权的用户"
            remote
            style={{ width: "100%" }}
            value={selectedUser}
          />
        </div>
        <Button
          disabled={!selectedUser}
          loading={busy}
          onClick={() => void authorize()}
          theme="solid"
          type="primary"
        >
          添加授权
        </Button>
        <Button onClick={() => setCreateOpen(true)}>新建用户</Button>
      </div>

      {rows !== null && rows.length === 0 ? (
        <Empty description="暂无授权用户，添加后该用户即可通过 Chat 使用此智能体" />
      ) : (
        <Table<AuthorizedUserSummary>
          aria-label="Agent 授权用户列表"
          columns={[
            { title: "用户", dataIndex: "displayName" },
            { title: "用户 ID", dataIndex: "platformUserId" },
            {
              title: "状态",
              dataIndex: "enabled",
              render: (enabled: boolean) =>
                enabled ? <Tag color="green">已授权</Tag> : <Tag color="grey">已移除</Tag>
            },
            {
              title: "生效能力（∩ Agent 默认）",
              dataIndex: "capabilityOverlap",
              render: (refs: readonly string[]) =>
                refs.length ? refs.map((ref) => <Tag key={ref}>{ref}</Tag>) : "—"
            },
            {
              title: "相对增项",
              dataIndex: "capabilityAdditions",
              render: (refs: readonly string[]) =>
                refs.length ? refs.map((ref) => <Tag key={ref} color="blue">{ref}</Tag>) : "—"
            },
            {
              title: "操作",
              dataIndex: "platformUserId",
              render: (platformUserId: string) => (
                <>
                  <Button
                    aria-label={`签发对话链接 ${platformUserId}`}
                    disabled={busy}
                    onClick={() => void issueLink(platformUserId)}
                    size="small"
                  >
                    签发对话链接
                  </Button>
                  <Popconfirm
                    title="确认移除该用户的授权？"
                    content="移除后该用户已签发的对话链接立即失效，无法再与此智能体对话。"
                    okButtonProps={{ type: "danger" }}
                    okText="确认移除"
                    onConfirm={() => void revoke(platformUserId)}
                  >
                    <Button
                      aria-label={`移除授权 ${platformUserId}`}
                      disabled={busy}
                      size="small"
                      type="danger"
                    >
                      移除授权
                    </Button>
                  </Popconfirm>
                </>
              )
            }
          ]}
          dataSource={[...(rows ?? [])]}
          pagination={false}
          rowKey="platformUserId"
        />
      )}

      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}

      <Modal
        cancelText="取 消"
        okText="确 定"
        okButtonProps={{ loading: busy }}
        onOk={() => void createUser()}
        onCancel={() => setCreateOpen(false)}
        title="新建用户"
        visible={createOpen}
      >
        <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
          <div>
            <Typography.Text>用户 ID *</Typography.Text>
            <Input
              aria-label="用户 ID"
              onChange={(value) => setNewUserId(String(value))}
              placeholder="如 ops-alice"
              value={newUserId}
            />
          </div>
          <div>
            <Typography.Text>用户显示名</Typography.Text>
            <Input
              aria-label="用户显示名"
              onChange={(value) => setNewUserName(String(value))}
              placeholder="显示名（缺省同用户 ID）"
              value={newUserName}
            />
          </div>
        </div>
      </Modal>

      <Modal
        cancelText="关 闭"
        footer={null}
        onCancel={() => setIssued(null)}
        title="对话链接已签发"
        visible={issued !== null}
      >
        {issued ? (
          <div style={{ display: "grid", gap: 8 }}>
            <Typography.Text>用户：{issued.platformUserId}</Typography.Text>
            <Typography.Text copyable aria-label="Chat 访问 token">
              {issued.token}
            </Typography.Text>
            <Typography.Text type="warning">
              链接仅本次显示 token，复制后请妥善保存；移除授权后立即失效。
            </Typography.Text>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
