import { useEffect, useMemo, useState } from "react";

import { Button, Descriptions, Empty, Input, Modal, Select, SideSheet, Space, Table, Typography } from "@douyinfe/semi-ui";
import { useNavigate } from "react-router-dom";
import { IconCopy, IconDelete, IconLink, IconPlus } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import {
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../../components/StandardListShell";
import type {
  ConsoleApi,
  IssuedChatAccess,
  PlatformUser
} from "../../types/console";
import { useRemoteResourceOptions } from "../../components/useRemoteResourceOptions";

interface UsersChannelsPageProps {
  readonly api: ConsoleApi;
}

const USER_PAGE_SIZE = 20;

/** TASK-019（§8.8）：用户页标准化——搜索 + 右下单套分页（StandardListShell）；
 * Agent Select 从列表卡片头迁入「生成对话链接」弹窗（消除过滤错觉）；
 * Agent 授权入口引导至 Agent Editor 用户 tab（TASK-013，授权放 Agent 维度）。 */
export function UsersChannelsPage({ api }: UsersChannelsPageProps) {
  const navigate = useNavigate();
  const [users, setUsers] = useState<readonly PlatformUser[] | null>(null);
  const [platformUserId, setPlatformUserId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [issued, setIssued] = useState<IssuedChatAccess | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [issueTarget, setIssueTarget] = useState<PlatformUser | null>(null);
  // closure TASK-010（P1C-06）：签发目标数据源切 agent_definition（产品模型）。
  // FEAT-03：签发弹窗的智能体选择器远程搜索（大数据集可达）。
  const agentOptions = useRemoteResourceOptions(api, ["agent_definition"], issueTarget !== null);
  const [issueAgentId, setIssueAgentId] = useState<string>("");
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [userPage, setUserPage] = useState(1);
  const [userTotal, setUserTotal] = useState(0);

  async function load(page: number): Promise<void> {
    try {
      const userPageResult = await api.listPlatformUsers({ page, pageSize: USER_PAGE_SIZE });
      setUsers(userPageResult.items);
      setUserPage(page);
      setUserTotal(userPageResult.total);
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

  async function issue(user: PlatformUser, agentId: string): Promise<void> {
    await runAction(async () => {
      setIssued(await api.issueChatAccess(user.platformUserId, agentId));
      setIssueTarget(null);
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

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (users ?? []).filter(
      (user) =>
        !keyword ||
        user.platformUserId.toLowerCase().includes(keyword) ||
        user.displayName.toLowerCase().includes(keyword)
    );
  }, [search, users]);

  return (
    <div className="page-stack">
      <PageHeader description="创建本地用户并签发可撤销的专属对话链接。" title="用户管理" />
      <ErrorBanner message={error} />
      {!issued && notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      <div aria-label="用户列表">
        <StandardListCard
          empty={users !== null && filtered.length === 0}
          emptyDescription="暂无用户"
          error={error}
          footer={
            users !== null && filtered.length > 0 ? (
              <StandardListFooter
                onPageChange={(page) => void load(page)}
                page={userPage}
                pageSize={USER_PAGE_SIZE}
                total={userTotal}
              />
            ) : undefined
          }
          loading={users === null && !error}
          onRetry={() => void load(userPage)}
          toolbar={
            <StandardListToolbar
              primary={
                <Button aria-label="新增用户" icon={<IconPlus />} onClick={() => setCreateOpen(true)} type="primary">
                  新增用户
                </Button>
              }
              search={
                <StandardListSearch
                  onChange={setSearch}
                  placeholder="搜索用户"
                  value={search}
                />
              }
            />
          }
        >
          <Table
            columns={userColumns(
              setIssueTarget,
              (user) => navigate(`/users/${user.platformUserId}`)
            )}
            dataSource={[...filtered]}
            empty={<Empty description="暂无用户" />}
            pagination={false}
            rowKey="platformUserId"
          />
        </StandardListCard>
      </div>
      <Typography.Text type="tertiary">
        用户级 Agent 授权在智能体编辑器「用户」tab 管理（Agent 维度授权，不塞进用户创建流程）。
      </Typography.Text>
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
      {issueTarget ? (
        <Modal
          cancelText="取 消"
          okButtonProps={{ disabled: !issueAgentId }}
          okText="确 定"
          onOk={() => void issue(issueTarget, issueAgentId)}
          onCancel={() => setIssueTarget(null)}
          title="生成对话链接"
          visible
        >
          <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
            <Typography.Text>{`为用户「${issueTarget.displayName || issueTarget.platformUserId}」签发对话链接；选择目标智能体：`}</Typography.Text>
            <Select
              aria-label="签发目标智能体"
              data-testid="agent-select"
              filter={false}
              loading={agentOptions.loading}
              onChange={(value) => setIssueAgentId(typeof value === "string" ? value : "")}
              onSearch={agentOptions.onSearch}
              optionList={agentOptions.options.map((profile) => ({ label: profile.label, value: profile.resourceId }))}
              outerBottomSlot={
                agentOptions.truncated ? (
                  <Typography.Text type="tertiary" size="small">
                    仅显示前 {agentOptions.options.length} 条匹配，请细化关键词
                  </Typography.Text>
                ) : undefined
              }
              placeholder="输入关键词搜索智能体"
              remote
              style={{ width: "100%" }}
              value={issueAgentId}
            />
          </div>
        </Modal>
      ) : null}
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
