/**
 * X401 WorkspaceLayout（TASK-003 / FEAT-P4-01，remediation §15.1）。
 *
 * 容器：绑定状态（resolveAccess）+ 侧边导航（八项）+ 顶栏（绑定状态 + 主题切换）
 * + Router Outlet。未绑定用户仅见 /bind 绑定流程，导航不渲染（B-01 正式 Channel 规则）。
 */
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { Button, Layout, Nav, Spin, Tag, Typography } from "@douyinfe/semi-ui";
import {
  IconComment,
  IconHistory,
  IconHome,
  IconList,
  IconMail,
  IconMoon,
  IconServerStroked,
  IconSetting,
  IconSun,
  IconUser
} from "@douyinfe/semi-icons";

import { useThemeMode } from "../theme";
import type { ChatApi, ChatAccess } from "../types/chat";
import { BindGate } from "./BindGate";

interface WorkspaceLayoutProps {
  readonly api: ChatApi;
}

const NAV_ITEMS = [
  { itemKey: "/home", text: "首页" },
  { itemKey: "/agents", text: "智能体" },
  { itemKey: "/tasks", text: "任务" },
  { itemKey: "/approvals", text: "审批" },
  { itemKey: "/history", text: "历史" },
  { itemKey: "/memory", text: "记忆" },
  { itemKey: "/chat", text: "对话" },
  { itemKey: "/settings", text: "设置" }
] as const;

const NAV_ICONS: Readonly<Record<string, React.ReactNode>> = {
  "/agents": <IconServerStroked />,
  "/approvals": <IconMail />,
  "/chat": <IconComment />,
  "/history": <IconHistory />,
  "/home": <IconHome />,
  "/memory": <IconUser />,
  "/settings": <IconSetting />,
  "/tasks": <IconList />
};

export function WorkspaceLayout({ api }: WorkspaceLayoutProps) {
  const [access, setAccess] = useState<ChatAccess | null>(null);
  const [resolving, setResolving] = useState(api.resolveAccess !== undefined);
  const { mode, toggle } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (!api.resolveAccess) return;
    let active = true;
    void api
      .resolveAccess()
      .then((resolved) => {
        if (active) setAccess(resolved);
      })
      .catch(() => {
        if (active) setAccess(null);
      })
      .finally(() => {
        if (active) setResolving(false);
      });
    return () => {
      active = false;
    };
  }, [api]);

  if (resolving) {
    return (
      <div className="workspace-loading" role="status" aria-label="正在加载工作区">
        <Spin size="large" />
      </div>
    );
  }

  if (!access) {
    return (
      <BindGate
        api={api}
        onBound={(platformUserId) => {
          setAccess({ accessId: "bind-message", agentId: "", platformUserId });
          navigate("/home");
        }}
      />
    );
  }

  const selectedKey =
    NAV_ITEMS.find((item) => location.pathname.startsWith(item.itemKey))?.itemKey ?? "/home";

  return (
    <Layout className="workspace-shell">
      <Layout.Sider className="workspace-sider">
        <div className="brand">
          <Typography.Title heading={4}>我的工作区</Typography.Title>
        </div>
        <Nav
          items={NAV_ITEMS.map((item) => ({
            icon: NAV_ICONS[item.itemKey],
            itemKey: item.itemKey,
            text: item.text
          }))}
          onSelect={(data) => navigate(String(data.itemKey))}
          selectedKeys={[selectedKey]}
        />
      </Layout.Sider>
      <Layout>
        <header className="workspace-header">
          <Tag color="green">已绑定 {access.platformUserId}</Tag>
          <Button
            aria-label={mode === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
            icon={mode === "dark" ? <IconSun /> : <IconMoon />}
            onClick={toggle}
            theme="borderless"
          />
        </header>
        <Layout.Content className="workspace-content">
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
