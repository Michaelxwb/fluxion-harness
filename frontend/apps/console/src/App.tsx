import { useState } from "react";

import { Button, Layout, Nav, Typography } from "@douyinfe/semi-ui";
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
  IconTestScore,
  IconUserGroup
} from "@douyinfe/semi-icons";

import { AuditPage } from "./pages/audit/AuditPage";
import { OverviewPage } from "./pages/overview/OverviewPage";
import { BindingsPage } from "./pages/bindings/BindingsPage";
import { ResourcesPage } from "./pages/resources/ResourcesPage";
import { RunsPage } from "./pages/runs/RunsPage";
import { UsersChannelsPage } from "./pages/users/UsersChannelsPage";
import { WorkflowsPage } from "./pages/workflows/WorkflowsPage";
import { P1ViewPage } from "./pages/p1/P1ViewPage";
import { CapabilitiesPage } from "./pages/capabilities/CapabilitiesPage";
import type { ConsoleApi } from "./types/console";
import { isConsoleView, isP1View, type ConsoleView } from "./types/navigation";
import { useThemeMode } from "./theme";
import "./styles.css";

interface ConsoleAppProps {
  readonly api: ConsoleApi;
  readonly initialView?: ConsoleView;
}

export function ConsoleApp({ api, initialView = "resources" }: ConsoleAppProps) {
  const [activeView, setActiveView] = useState<ConsoleView>(initialView);
  const { mode, toggle } = useThemeMode();

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
          onSelect={(data) => setActiveView(toConsoleView(String(data.itemKey)))}
          selectedKeys={[activeView]}
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
        {renderView(activeView, api)}
      </Layout.Content>
    </Layout>
  );
}

const navItems = [
  {
    itemKey: "group-overview",
    text: "概览",
    items: [{ icon: <IconPulse />, itemKey: "overview", text: "平台概览" }]
  },
  {
    itemKey: "group-build",
    text: "构建",
    items: [
      { icon: <IconUserGroup />, itemKey: "resources", text: "智能体" },
      { icon: <IconFlowChartStroked />, itemKey: "workflows", text: "工作流" },
      { icon: <IconKey />, itemKey: "capabilities", text: "能力" },
      { icon: <IconTestScore />, itemKey: "eval", text: <PlannedText>评测</PlannedText> }
    ]
  },
  {
    itemKey: "group-users",
    text: "用户",
    items: [{ icon: <IconUserGroup />, itemKey: "users_channels", text: "用户与渠道" }]
  },
  {
    itemKey: "group-governance",
    text: "治理",
    items: [
      { icon: <IconPuzzle />, itemKey: "plugin_policy", text: <PlannedText>插件策略</PlannedText> },
      { icon: <IconHistory />, itemKey: "audit", text: "操作审计" },
      { icon: <IconShield />, itemKey: "bindings", text: "资源绑定" }
    ]
  },
  {
    itemKey: "group-operations",
    text: "运营",
    items: [
      { icon: <IconActivity />, itemKey: "runs", text: "执行记录" },
      { icon: <IconPulse />, itemKey: "runtime_status", text: <PlannedText>运行时态</PlannedText> }
    ]
  },
  {
    itemKey: "group-platform",
    text: "平台",
    items: [{ icon: <IconList />, itemKey: "platform_assets", text: "运行资产" }]
  }
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

function renderView(view: ConsoleView, api: ConsoleApi) {
  if (view === "overview") {
    return <OverviewPage api={api} />;
  }
  if (view === "platform_assets") {
    return <ResourcesPage api={api} />;
  }
  if (view === "users_channels") {
    return <UsersChannelsPage api={api} />;
  }
  if (view === "capabilities") {
    return <CapabilitiesPage api={api} />;
  }
  if (isP1View(view)) {
    return <P1ViewPage api={api} view={view} />;
  }
  if (view === "bindings") {
    return <BindingsPage api={api} />;
  }
  if (view === "workflows") {
    return <WorkflowsPage api={api} />;
  }
  if (view === "runs") {
    return <RunsPage api={api} />;
  }
  if (view === "audit") {
    return <AuditPage api={api} />;
  }
  return <ResourcesPage api={api} />;
}

function toConsoleView(value: string): ConsoleView {
  return isConsoleView(value) ? value : "resources";
}
