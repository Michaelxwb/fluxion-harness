import { Button, Layout, Nav, Typography } from "@douyinfe/semi-ui";
import {
  IconActivity,
  IconComment,
  IconFlowChartStroked,
  IconHistory,
  IconKey,
  IconList,
  IconMoon,
  IconPulse,
  IconPuzzle,
  IconServer,
  IconShield,
  IconSun,
  IconTestScore,
  IconUserGroup
} from "@douyinfe/semi-icons";

import { MemoryRouter, Navigate, Outlet, Route, Routes, useInRouterContext, useLocation, useNavigate } from "react-router-dom";

import { AuditPage } from "./pages/audit/AuditPage";
import { GovernancePoliciesPage } from "./pages/governance/GovernancePoliciesPage";
import { OverviewPage } from "./pages/overview/OverviewPage";
import { BindingsPage } from "./pages/bindings/BindingsPage";
import { ResourcesPage } from "./pages/resources/ResourcesPage";
import { RunsPage } from "./pages/runs/RunsPage";
import { QueuesPage } from "./pages/operations/QueuesPage";
import { WorkersPage } from "./pages/operations/WorkersPage";
import { UsersChannelsPage } from "./pages/users/UsersChannelsPage";
import { WorkflowsPage } from "./pages/workflows/WorkflowsPage";
import { P1ViewPage } from "./pages/p1/P1ViewPage";
import { CapabilitiesPage } from "./pages/capabilities/CapabilitiesPage";
import { AgentStudioPage } from "./pages/studio/AgentStudioPage";
import { EvalPlaceholderPage } from "./pages/eval/EvalPlaceholderPage";
import type { ConsoleApi } from "./types/console";
import { viewToPath, type ConsoleView } from "./types/navigation";
import { useThemeMode } from "./theme";
import "./styles.css";

interface ConsoleAppProps {
  readonly api: ConsoleApi;
  readonly initialView?: ConsoleView;
  readonly initialAgentId?: string;
}

/**
 * TASK-004：Console 从 state 导航迁移到 Router（行为不变，`ConsoleView` 经
 * `viewToPath` 寻址）。已处于 Router 上下文（main.tsx HashRouter）则直接渲染路由表；
 * 独立渲染（测试）时自建 MemoryRouter 以 `initialView` 为初始路径。
 */
export function ConsoleApp({ api, initialView = "overview", initialAgentId }: ConsoleAppProps) {
  const routes = <ConsoleRoutes api={api} initialAgentId={initialAgentId} />;
  if (useInRouterContext()) return routes;
  return <MemoryRouter initialEntries={[viewToPath(initialView)]}>{routes}</MemoryRouter>;
}

export function ConsoleRoutes({
  api,
  initialAgentId
}: {
  readonly api: ConsoleApi;
  readonly initialAgentId?: string;
}) {
  return (
    <Routes>
      <Route element={<ConsoleLayout />}>
        <Route path="/" element={<Navigate replace to="/overview" />} />
        <Route path="/overview" element={<OverviewPage api={api} />} />
        <Route path="/build/agents" element={<ResourcesPage api={api} />} />
        <Route
          path="/build/agent-studio"
          element={<AgentStudioPage api={api} initialAgentId={initialAgentId} />}
        />
        <Route path="/build/workflows" element={<WorkflowsPage api={api} />} />
        <Route path="/build/capabilities" element={<CapabilitiesPage api={api} />} />
        <Route path="/build/eval" element={<EvalPlaceholderPage />} />
        <Route path="/users" element={<UsersChannelsPage api={api} />} />
        <Route path="/governance/policies" element={<GovernancePoliciesPage api={api} />} />
        <Route
          path="/governance/plugin-policy"
          element={<P1ViewPage api={api} view="plugin_policy" />}
        />
        <Route path="/governance/audit" element={<AuditPage api={api} />} />
        <Route path="/governance/bindings" element={<BindingsPage api={api} />} />
        <Route path="/operations/runs" element={<RunsPage api={api} />} />
        <Route path="/operations/queues" element={<QueuesPage api={api} />} />
        <Route path="/operations/workers" element={<WorkersPage api={api} />} />
        <Route
          path="/operations/runtime-status"
          element={<P1ViewPage api={api} view="runtime_status" />}
        />
        <Route path="/platform/runtime-profiles" element={<ResourcesPage api={api} initialTypeFilter="runtime_profile" />} />
        <Route path="/platform/secrets" element={<ResourcesPage api={api} initialTypeFilter="secret" />} />
        <Route path="/platform/models" element={<ResourcesPage api={api} initialTypeFilter="model" />} />
        <Route path="/platform/assets" element={<ResourcesPage api={api} />} />
        {/* 未匹配路径回退智能体目录（对齐迁移前 toConsoleView 默认行为） */}
        <Route path="*" element={<ResourcesPage api={api} />} />
      </Route>
    </Routes>
  );
}

function ConsoleLayout() {
  const { mode, toggle } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const selectedKey = navItemKeys.find((key) => location.pathname.startsWith(key)) ?? "/overview";

  return (
    <Layout className="app-shell">
      <Layout.Sider className="app-sidebar">
        <div className="brand">
          <Typography.Title heading={4}>Fluxion 控制台</Typography.Title>
          <Typography.Text type="tertiary">控制面</Typography.Text>
        </div>
        <Nav
          defaultOpenKeys={OPEN_GROUP_KEYS}
          items={navItems}
          onSelect={(data) => navigate(String(data.itemKey))}
          selectedKeys={[selectedKey]}
        />
      </Layout.Sider>
      <Layout.Content className="app-content">
        <div className="theme-switch">
          <Button
            aria-label={mode === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
            icon={mode === "dark" ? <IconSun /> : <IconMoon />}
            onClick={toggle}
            theme="borderless"
          />
        </div>
        <Outlet />
      </Layout.Content>
    </Layout>
  );
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
      { icon: <IconServer />, itemKey: "/build/agent-studio", text: "智能体工作台" },
      { icon: <IconFlowChartStroked />, itemKey: "/build/workflows", text: "工作流" },
      { icon: <IconKey />, itemKey: "/build/capabilities", text: "能力" },
      { icon: <IconTestScore />, itemKey: "/build/eval", text: <PlannedText>评测</PlannedText> }
    ]
  },
  {
    itemKey: "group-users",
    text: "用户",
    items: [{ icon: <IconUserGroup />, itemKey: "/users", text: "用户与渠道" }]
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
    items: [
      { icon: <IconActivity />, itemKey: "/operations/runs", text: "执行记录" },
      { icon: <IconServer />, itemKey: "/operations/queues", text: "队列" },
      { icon: <IconServer />, itemKey: "/operations/workers", text: "Worker" },
      { icon: <IconPulse />, itemKey: "/operations/runtime-status", text: <PlannedText>运行时态</PlannedText> }
    ]
  },
  {
    itemKey: "group-platform",
    text: "平台",
    items: [
      { icon: <IconPulse />, itemKey: "/platform/runtime-profiles", text: "运行设置" },
      { icon: <IconKey />, itemKey: "/platform/secrets", text: "凭据" },
      { icon: <IconList />, itemKey: "/platform/models", text: "模型" },
      { icon: <IconComment />, itemKey: "/platform/assets", text: "运行资产" }
    ]
  }
];

const navItemKeys = [
  "/overview",
  "/build/agents",
  "/build/agent-studio",
  "/build/workflows",
  "/build/capabilities",
  "/build/eval",
  "/users",
  "/governance/policies",
  "/governance/plugin-policy",
  "/governance/audit",
  "/governance/bindings",
  "/operations/runs",
  "/operations/queues",
  "/operations/workers",
  "/operations/runtime-status",
  "/platform/runtime-profiles",
  "/platform/secrets",
  "/platform/models",
  "/platform/assets"
];

// 规划中的页面在导航里置灰，与实际可用页区分。
function PlannedText({ children }: { readonly children: string }) {
  return <Typography.Text type="tertiary">{children}</Typography.Text>;
}

const OPEN_GROUP_KEYS = [
  "group-overview",
  "group-build",
  "group-users",
  "group-governance",
  "group-operations",
  "group-platform"
];
