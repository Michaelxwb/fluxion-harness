import { useEffect, useMemo, useState } from "react";

import { Button, Card, Descriptions, Empty, Input, Modal, Select, SideSheet, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconCopy, IconDelete, IconLink, IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { ListPager } from "../../components/ListPager";
import { PageHeader } from "../../components/PageHeader";
import { User360Header } from "../../components/user360/User360Header";
import { User360Tabs } from "../../components/user360/User360Tabs";
import type {
  ConsoleApi,
  User360Summary,
  IssuedChatAccess,
  PlatformUser,
  ResourceSummary
} from "../../types/console";

interface UsersChannelsPageProps {
  readonly api: ConsoleApi;
}

const USER_PAGE_SIZE = 20;

export function UsersChannelsPage({ api }: UsersChannelsPageProps) {
  const [users, setUsers] = useState<readonly PlatformUser[]>([]);
  // closure TASK-010（P1C-06）：选择器数据源切 agent_definition（产品模型），
  // 消除「RuntimeProfile 资源 ID 被当 agentId 签发」的错配。
  const [profiles, setProfiles] = useState<readonly ResourceSummary[]>([]);
  const [platformUserId, setPlatformUserId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [agentId, setAgentId] = useState<string>();
  const [selected360, setSelected360] = useState<string>();
  const [view360, setView360] = useState<User360Summary | null>(null);
  const [issued, setIssued] = useState<IssuedChatAccess | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [userPage, setUserPage] = useState(1);
  const [userTotal, setUserTotal] = useState(0);

  async function load(page: number): Promise<void> {
    try {
      const [userPageResult, profileItems] = await Promise.all([
        api.listPlatformUsers({ page, pageSize: USER_PAGE_SIZE }),
        api.listVisibleResources("agent_definition")
      ]);
      setUsers(userPageResult.items);
      setUserPage(page);
      setUserTotal(userPageResult.total);
      setProfiles(profileItems);
      setAgentId((current) => current ?? profileItems[0]?.resourceId);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  useEffect(() => {
    void load(1);
  }, []);

  async function createUser(): Promise<void> {
    if (!platformUserId.trim()) return;
    await runAction(async () => {
      await api.createPlatformUser(platformUserId.trim(), displayName.trim());
      setCreateOpen(false);
      setPlatformUserId("");
      setDisplayName("");
      setNotice("用户已创建");
      await load(userPage);
    });
  }

  async function issue(user: PlatformUser): Promise<void> {
    if (!agentId) return;
    await runAction(async () => {
      setIssued(await api.issueChatAccess(user.platformUserId, agentId));
      setNotice("Chat 链接已生成，仅本次显示 token");
    });
  }

  async function revoke(): Promise<void> {
    if (!issued) return;
    await runAction(async () => {
      await api.revokeChatAccess(issued.accessId);
      setIssued(null);
      setRevokeOpen(false);
      setNotice("Chat 链接已撤销");
    });
  }

  async function runAction(action: () => Promise<void>): Promise<void> {
    try {
      await action();
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  const link = useMemo(
    () => issued ? new URL(issued.chatPath, window.location.origin).toString() : "",
    [issued]
  );

  return (
    <div className="page-stack">
      <PageHeader description="创建本地用户并签发可撤销的专属对话链接。" title="用户管理" />
      <ErrorBanner message={error} />
      {!issued && notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <Card
        aria-label="用户列表"
        bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }}
        header={
          <div className="list-card-header list-card-header--spread">
            <Space>
              <Button aria-label="新增" icon={<IconPlus />} onClick={() => setCreateOpen(true)} type="primary">新增</Button>
            </Space>
            <Space align="center">
              <Typography.Text>智能体</Typography.Text>
              <Select
                aria-label="智能体"
                data-testid="agent-select"
                onChange={(value) => setAgentId(typeof value === "string" ? value : undefined)}
                optionList={profiles.map((profile) => ({ label: profile.displayName, value: profile.resourceId }))}
                style={{ width: 160 }}
                value={agentId}
              />
            </Space>
          </div>
        }
      >
        <Table
          columns={userColumns(
            (user) => void issue(user),
            Boolean(agentId),
            (user) => {
              setSelected360(user.platformUserId);
              void api.getUser360(user.platformUserId).then(setView360).catch(() => setView360(null));
            }
          )}
          dataSource={[...users]}
          empty={<Empty description="暂无用户" />}
          pagination={false}
          rowKey="platformUserId"
        />
        <ListPager onChange={(page) => void load(page)} page={userPage} pageSize={USER_PAGE_SIZE} total={userTotal} />
      </Card>
      {createOpen ? (
        <Modal
          footer={
            <Space>
              <Button aria-label="取消" onClick={() => setCreateOpen(false)}>取消</Button>
              <Button
                aria-label="创建用户"
                disabled={!platformUserId.trim()}
                onClick={() => void createUser()}
                theme="solid"
                type="primary"
              >
                创建用户
              </Button>
            </Space>
          }
          onCancel={() => setCreateOpen(false)}
          title="新增用户"
          visible
        >
          <Space vertical align="start" style={{ width: "100%" }}>
            <Input aria-label="用户 ID" onChange={setPlatformUserId} placeholder="用户 ID" value={platformUserId} />
            <Input aria-label="显示名" onChange={setDisplayName} placeholder="显示名" value={displayName} />
          </Space>
        </Modal>
      ) : null}
      <SideSheet
        onCancel={() => setSelected360(undefined)}
        title={`User 360 · ${selected360 ?? ""}`}
        visible={selected360 !== undefined}
        width={520}
      >
        {view360 ? (
          <div aria-label="User 360" className="page-stack">
            <User360Header summary={view360} />
            <User360Tabs summary={view360} />
          </div>
        ) : null}
      </SideSheet>

      <SideSheet
        onCancel={() => {
          setIssued(null);
          setNotice(null);
        }}
        title="对话链接"
        visible={issued !== null}
        width={720}
      >
        {issued ? (
          <div className="page-stack">
            <ErrorBanner message={error} />
            {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
            <Descriptions row>
              <Descriptions.Item itemKey="用户">{issued.platformUserId}</Descriptions.Item>
              <Descriptions.Item itemKey="运行态">{issued.agentId}</Descriptions.Item>
              <Descriptions.Item itemKey="创建时间">{formatDateTime(issued.createdAt)}</Descriptions.Item>
            </Descriptions>
            <Typography.Text type="tertiary">链接仅本次显示 token，复制后请妥善保存；撤销后链接立即失效。</Typography.Text>
            <Input aria-label="专属对话链接" readOnly value={link} />
            <Space>
              <Button icon={<IconCopy />} onClick={() => void navigator.clipboard.writeText(link)}>复制链接</Button>
              <Button icon={<IconLink />} onClick={() => window.open(link, "_blank", "noopener,noreferrer")}>打开对话</Button>
              <Button icon={<IconDelete />} onClick={() => setRevokeOpen(true)} type="danger">撤销</Button>
            </Space>
            {revokeOpen ? (
              <Modal
                cancelText="取消"
                okButtonProps={{ type: "danger" }}
                okText="确认撤销"
                onCancel={() => setRevokeOpen(false)}
                onOk={() => void revoke()}
                title="撤销对话链接"
                visible
              >
                撤销后，当前链接会立即失效，用户无法继续通过该链接对话。
              </Modal>
            ) : null}
          </div>
        ) : null}
      </SideSheet>
    </div>
  );
}

function userColumns(
  onIssue: (user: PlatformUser) => void,
  enabled: boolean,
  onView360: (user: PlatformUser) => void
) {
  return [
    { dataIndex: "platformUserId", title: "用户 ID" },
    { dataIndex: "displayName", title: "名称" },
    { dataIndex: "createdAt", title: "创建时间" },
    {
      render: (_value: unknown, user: PlatformUser) => (
        <Space>
          <Button
            aria-label="生成对话链接"
            disabled={!enabled}
            icon={<IconLink />}
            onClick={() => onIssue(user)}
            type="primary"
          >
            生成对话链接
          </Button>
          <Button aria-label={`查看 360 ${user.platformUserId}`} onClick={() => onView360(user)}>
            查看 360
          </Button>
        </Space>
      ),
      title: "操作"
    }
  ];
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
