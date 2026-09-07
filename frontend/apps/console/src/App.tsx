import { useEffect, useState } from "react";

import { Breadcrumb, Button, Layout, Nav, Tag, Typography } from "@douyinfe/semi-ui";
import {
  IconActivity,
  IconFlowChartStroked,
  IconHistory,
  IconKey,
  IconList,
  IconMoon,
  IconPulse,
  IconPuzzle,
  IconShield,
  IconSun,
  IconUserGroup
} from "@douyinfe/semi-icons";

import { MemoryRouter, Navigate, Outlet, Route, Routes, useInRouterContext, useLocation, useNavigate, useParams } from "react-router-dom";

import { AgentEditorPage } from "./pages/agents/AgentEditorPage";
import { AgentsPage } from "./pages/agents/AgentsPage";
import { ModelsPage } from "./pages/models/ModelsPage";
import { AuditPage } from "./pages/audit/AuditPage";
import { GovernancePoliciesPage } from "./pages/governance/GovernancePoliciesPage";
import { OverviewPage } from "./pages/overview/OverviewPage";
import { BindingsPage } from "./pages/bindings/BindingsPage";
import { CredentialsPage } from "./pages/secrets/CredentialsPage";
import { RunsPage } from "./pages/runs/RunsPage";
import { User360Page } from "./pages/users/User360Page";
import { UsersChannelsPage } from "./pages/users/UsersChannelsPage";
import { WorkflowDesignerPage } from "./pages/workflows/WorkflowDesignerPage";
import { SkillEditorPage } from "./pages/capabilities/SkillEditorPage";
import { McpEditorPage } from "./pages/capabilities/McpEditorPage";
import { PolicyEditorPage } from "./pages/governance/PolicyEditorPage";
import { ToolEditorPage } from "./pages/capabilities/ToolEditorPage";
import { WorkflowsPage } from "./pages/workflows/WorkflowsPage";
import { P1ViewPage } from "./pages/p1/P1ViewPage";
import { CapabilitiesPage } from "./pages/capabilities/CapabilitiesPage";
import type { ConsoleApi } from "./types/console";
import { viewToPath, type ConsoleView } from "./types/navigation";
import { useThemeMode } from "./theme";
import "./styles.css";

interface ConsoleAppProps {
  readonly api: ConsoleApi;
  readonly initialView?: ConsoleView;
}

/**
 * TASK-004：Console 从 state 导航迁移到 Router（行为不变，`ConsoleView` 经
 * `viewToPath` 寻址）。已处于 Router 上下文（main.tsx HashRouter）则直接渲染路由表；
 * 独立渲染（测试）时自建 MemoryRouter 以 `initialView` 为初始路径。
 */
export function ConsoleApp({ api, initialView = "overview" }: ConsoleAppProps) {
  const routes = <ConsoleRoutes api={api} />;
  if (useInRouterContext()) return routes;
  return <MemoryRouter initialEntries={[viewToPath(initialView)]}>{routes}</MemoryRouter>;
}

export function ConsoleRoutes({
  api
}: {
  readonly api: ConsoleApi;
}) {
  return (
    <Routes>
      <Route element={<ConsoleLayout />}>
        <Route path="/" element={<Navigate replace to="/overview" />} />
        <Route path="/overview" element={<OverviewPage api={api} />} />
        <Route path="/build/agents" element={<AgentsPage api={api} />} />
        <Route path="/build/agents/:resourceId/edit" element={<AgentEditorPage api={api} />} />
        <Route path="/build/workflows" element={<WorkflowsPage api={api} />} />
        <Route
          path="/build/workflows/:resourceId/edit"
          element={<WorkflowDesignerPage api={api} />}
        />
        <Route path="/build/capabilities" element={<Navigate replace to="/build/capabilities/skill" />} />
        <Route path="/build/capabilities/:type" element={<CapabilitiesRoute api={api} />} />
        <Route path="/build/skills/:resourceId/edit" element={<SkillEditorPage api={api} />} />
        <Route path="/build/tools/:resourceId/edit" element={<ToolEditorPage api={api} />} />
        <Route path="/build/mcp/:resourceId/edit" element={<McpEditorPage api={api} />} />
        <Route path="/build/policies/:resourceId/edit" element={<PolicyEditorPage api={api} />} />
        <Route path="/users" element={<UsersChannelsPage api={api} />} />
        <Route path="/users/:platformUserId" element={<User360Page api={api} />} />
        <Route path="/governance/policies" element={<GovernancePoliciesPage api={api} />} />
        <Route
          path="/governance/plugin-policy"
          element={<P1ViewPage api={api} view="plugin_policy" />}
        />
        <Route path="/governance/audit" element={<AuditPage api={api} />} />
        <Route path="/governance/bindings" element={<BindingsPage api={api} />} />
        <Route path="/operations/runs" element={<RunsPage api={api} />} />
        <Route path="/platform/credentials" element={<CredentialsPage api={api} />} />
        <Route path="/platform/models" element={<ModelsPage api={api} />} />
        {/* IA 对齐 design §3.2：移除 agent-studio/eval/queues/workers/
            runtime-status/runtime-profiles/assets 路由；未匹配路径回退概览 */}
        <Route path="*" element={<Navigate replace to="/overview" />} />
      </Route>
    </Routes>
  );
}

function ConsoleLayout() {
  const { mode, toggle } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  // BUG-SHELL1 修复：最长前缀匹配（如 /build/policies/:id/edit 归属授权规则，
  // 而不是回退高亮平台概览）。
  const selectedKey =
    [...navItemKeys].sort((a, b) => b.length - a.length).find((key) => location.pathname.startsWith(key)) ??
    "/overview";
  const [openKeys, setOpenKeys] = useState<string[]>(() => initialOpenKeys(selectedKey));
  // 路由切到未展开分组时自动展开并持久化。
  useEffect(() => {
    setOpenKeys((current) => {
      const group = groupKeyOf(selectedKey);
      const next = group && !current.includes(group) ? [...current, group] : current;
      persistOpenKeys(next);
      return next;
    });
  }, [selectedKey]);
  const crumbs = breadcrumbOf(location.pathname, selectedKey);
  const host = window.location.hostname;
  const isLocal = host === "localhost" || host === "127.0.0.1";

  return (
    <Layout className="app-shell">
      <Layout.Sider className="app-sidebar">
        <div className="brand">
          <Typography.Title heading={4}>Fluxion 控制台</Typography.Title>
          <Typography.Text type="tertiary">控制面</Typography.Text>
        </div>
        <Nav
          items={navItems}
          onOpenChange={(data) => {
            const next = [...(data.openKeys ?? [])].map(String);
            persistOpenKeys(next);
            setOpenKeys(next);
          }}
          onSelect={(data) => navigate(String(data.itemKey))}
          openKeys={openKeys}
          selectedKeys={[selectedKey]}
        />
      </Layout.Sider>
      <Layout.Content className="app-content">
        <div className="topbar">
          <Breadcrumb aria-label="当前位置">
            {crumbs.map((crumb) => (
              <Breadcrumb.Item key={crumb}>{crumb}</Breadcrumb.Item>
            ))}
          </Breadcrumb>
          <span className="topbar__right">
            {isLocal ? <Tag color="blue">本地环境</Tag> : null}
            <Button
              aria-label={mode === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
              icon={mode === "dark" ? <IconSun /> : <IconMoon />}
              onClick={toggle}
              theme="borderless"
            />
          </span>
        </div>
        <Outlet />
      </Layout.Content>
    </Layout>
  );
}

const NAV_OPEN_KEYS_STORAGE = "fluxion.console.navOpen";

function groupKeyOf(itemKey: string): string | null {
  for (const group of navItems) {
    if (group.items.some((item) => itemKey === item.itemKey || itemKey.startsWith(`${item.itemKey}/`))) {
      return group.itemKey;
    }
  }
  return null;
}

function initialOpenKeys(selectedKey: string): string[] {
  try {
    const stored = localStorage.getItem(NAV_OPEN_KEYS_STORAGE);
    if (stored) {
      return (JSON.parse(stored) as string[]).filter((key) =>
        navItems.some((group) => group.itemKey === key)
      );
    }
  } catch {
    // localStorage 不可用时回退默认
  }
  const group = groupKeyOf(selectedKey);
  return group ? [group] : OPEN_GROUP_KEYS;
}

function persistOpenKeys(keys: string[]): void {
  try {
    localStorage.setItem(NAV_OPEN_KEYS_STORAGE, JSON.stringify(keys));
  } catch {
    // 忽略持久化失败
  }
}

function breadcrumbOf(pathname: string, selectedKey: string): string[] {
  // 非字符串菜单文本（如规划中置灰项）走显式映射。
  const planned: Record<string, string[]> = {
    "/governance/plugin-policy": ["治理", "插件策略"]
  };
  if (planned[selectedKey]) {
    return planned[selectedKey];
  }
  for (const group of navItems) {
    for (const item of group.items) {
      const label = typeof item.text === "string" ? item.text : null;
      if (selectedKey === item.itemKey && label) {
        const tail = pathname.slice(item.itemKey.length).split("/").filter(Boolean);
        return tail.length > 0 ? [group.text, label, decodeURIComponent(tail[tail.length - 1])] : [group.text, label];
      }
    }
  }
  return ["概览", "平台概览"];
}

const navItems = [
  {
    itemKey: "group-overview",
    text: "概览",
    items: [{ icon: <IconPulse />, itemKey: "/overview", text: "平台概览" }]
  },
  {
    itemKey: "group-build",
    text: "构建",
    items: [
      { icon: <IconUserGroup />, itemKey: "/build/agents", text: "智能体" },
      { icon: <IconFlowChartStroked />, itemKey: "/build/workflows", text: "工作流" },
      { icon: <IconKey />, itemKey: "/build/capabilities", text: "能力" }
    ]
  },
  {
    itemKey: "group-users",
    text: "用户",
    items: [{ icon: <IconUserGroup />, itemKey: "/users", text: "用户" }]
  },
  {
    itemKey: "group-governance",
    text: "治理",
    items: [
      { icon: <IconShield />, itemKey: "/governance/policies", text: "授权规则" },
      { icon: <IconPuzzle />, itemKey: "/governance/plugin-policy", text: <PlannedText>插件策略</PlannedText> },
      { icon: <IconHistory />, itemKey: "/governance/audit", text: "操作审计" }
    ]
  },
  {
    itemKey: "group-operations",
    text: "运营",
    items: [{ icon: <IconActivity />, itemKey: "/operations/runs", text: "执行记录" }]
  },
  {
    itemKey: "group-platform",
    text: "平台",
    items: [
      { icon: <IconKey />, itemKey: "/platform/credentials", text: "凭据" },
      { icon: <IconList />, itemKey: "/platform/models", text: "模型" }
    ]
  }
];

const navItemKeys = [
  "/overview",
  "/build/agents",
  "/build/workflows",
  "/build/capabilities",
  "/users",
  "/governance/policies",
  "/governance/plugin-policy",
  "/governance/audit",
  "/governance/bindings",
  "/operations/runs",
  "/platform/credentials",
  "/platform/models"
];

// 规划中的页面在导航里置灰，与实际可用页区分。
function PlannedText({ children }: { readonly children: string }) {
  return <Typography.Text type="tertiary">{children}</Typography.Text>;
}

function CapabilitiesRoute({ api }: { readonly api: ConsoleApi }) {
  const navigate = useNavigate();
  const { type } = useParams<{ type: string }>();
  const kind = type === "tool" || type === "mcp" ? type : "skill";
  return (
    <CapabilitiesPage
      api={api}
      initialKind={kind}
      key={kind}
      onKindChange={(next) => navigate(`/build/capabilities/${next}`)}
    />
  );
}

const OPEN_GROUP_KEYS = [
  "group-overview",
  "group-build",
  "group-users",
  "group-governance",
  "group-operations",
  "group-platform"
];
