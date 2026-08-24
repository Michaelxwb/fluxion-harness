import { useEffect, useMemo, useState } from "react";

import { Button, Input, Modal, Select, Space, Table, Typography } from "@douyinfe/semi-ui";
import { IconCopy, IconDelete, IconLink, IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { P1ViewPage } from "../p1/P1ViewPage";
import type {
  ConsoleApi,
  IssuedChatAccess,
  PlatformUser,
  ResourceSummary
} from "../../types/console";

interface UsersChannelsPageProps {
  readonly api: ConsoleApi;
}

export function UsersChannelsPage({ api }: UsersChannelsPageProps) {
  const [users, setUsers] = useState<readonly PlatformUser[]>([]);
  const [profiles, setProfiles] = useState<readonly ResourceSummary[]>([]);
  const [platformUserId, setPlatformUserId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [runtimeProfileId, setRuntimeProfileId] = useState<string>();
  const [issued, setIssued] = useState<IssuedChatAccess | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    try {
      const [userPage, profilePage] = await Promise.all([
        api.listPlatformUsers(),
        api.listResources("runtime_profile")
      ]);
      setUsers(userPage.items);
      setProfiles(profilePage.items);
      setRuntimeProfileId((current) => current ?? profilePage.items[0]?.resourceId);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function createUser(): Promise<void> {
    if (!platformUserId.trim()) return;
    await runAction(async () => {
      await api.createPlatformUser(platformUserId.trim(), displayName.trim());
      setPlatformUserId("");
      setDisplayName("");
      setNotice("用户已创建");
      await load();
    });
  }

  async function issue(user: PlatformUser): Promise<void> {
    if (!runtimeProfileId) return;
    await runAction(async () => {
      setIssued(await api.issueChatAccess(user.platformUserId, runtimeProfileId));
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
      <PageHeader description="创建本地用户并签发可撤销的专属 Chat 链接。" title="Users / Channels" />
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <section className="panel" aria-label="创建用户">
        <Typography.Title heading={4}>创建用户</Typography.Title>
        <Space wrap>
          <Input aria-label="用户 ID" onChange={setPlatformUserId} placeholder="user-id" value={platformUserId} />
          <Input aria-label="显示名称" onChange={setDisplayName} placeholder="显示名称" value={displayName} />
          <Button
            aria-label="创建用户"
            disabled={!platformUserId.trim()}
            icon={<IconPlus />}
            onClick={() => void createUser()}
            type="primary"
          >
            创建用户
          </Button>
        </Space>
      </section>
      <section className="panel" aria-label="用户列表">
        <Space align="center">
          <Typography.Text strong>RuntimeProfile</Typography.Text>
          <Select
            aria-label="RuntimeProfile"
            data-testid="runtime-profile-select"
            onChange={(value) => setRuntimeProfileId(typeof value === "string" ? value : undefined)}
            optionList={profiles.map((profile) => ({ label: profile.displayName, value: profile.resourceId }))}
            value={runtimeProfileId}
          />
        </Space>
        <Table
          columns={userColumns((user) => void issue(user), Boolean(runtimeProfileId))}
          dataSource={[...users]}
          pagination={false}
          rowKey="platformUserId"
        />
      </section>
      {issued ? (
        <section className="panel" aria-label="Chat 链接">
          <Typography.Title heading={4}>Chat 链接</Typography.Title>
          <Space vertical align="start">
            <Input aria-label="专属 Chat 链接" readOnly value={link} />
            <Space>
              <Button icon={<IconCopy />} onClick={() => void navigator.clipboard.writeText(link)}>复制链接</Button>
              <Button icon={<IconLink />} onClick={() => window.open(link, "_blank", "noopener,noreferrer")}>打开 Chat</Button>
              <Button icon={<IconDelete />} onClick={() => setRevokeOpen(true)} type="danger">撤销</Button>
            </Space>
          </Space>
        </section>
      ) : null}
      <P1ViewPage api={api} showHeader={false} view="users_channels" />
      {revokeOpen ? (
        <Modal
          cancelText="取消"
          okButtonProps={{ type: "danger" }}
          okText="确认撤销"
          onCancel={() => setRevokeOpen(false)}
          onOk={() => void revoke()}
          title="撤销 Chat 链接"
          visible
        >
          撤销后，当前链接会立即失效，用户无法继续通过该链接对话。
        </Modal>
      ) : null}
    </div>
  );
}

function userColumns(onIssue: (user: PlatformUser) => void, enabled: boolean) {
  return [
    { dataIndex: "platformUserId", title: "User ID" },
    { dataIndex: "displayName", title: "Name" },
    {
      render: (_value: unknown, user: PlatformUser) => (
        <Button
          aria-label="生成 Chat 链接"
          disabled={!enabled}
          icon={<IconLink />}
          onClick={() => onIssue(user)}
          type="primary"
        >
          生成 Chat 链接
        </Button>
      ),
      title: "Action"
    }
  ];
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
