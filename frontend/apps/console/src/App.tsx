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
import { BindingsPage } from "./pages/bindings/BindingsPage";
import { ResourcesPage } from "./pages/resources/ResourcesPage";
import { RunsPage } from "./pages/runs/RunsPage";
import { UsersChannelsPage } from "./pages/users/UsersChannelsPage";
import { WorkflowsPage } from "./pages/workflows/WorkflowsPage";
import { P1ViewPage } from "./pages/p1/P1ViewPage";
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
    text: "概览与运行",
    items: [
      { icon: <IconPulse />, itemKey: "runtime_status", text: <PlannedText>运行时态</PlannedText> },
      { icon: <IconActivity />, itemKey: "runs", text: "执行记录" }
    ]
  },
  {
    itemKey: "group-define",
    text: "定义与编排",
    items: [
      { icon: <IconList />, itemKey: "resources", text: "运行资产" },
      { icon: <IconFlowChartStroked />, itemKey: "workflows", text: "流程编排" }
    ]
  },
  {
    itemKey: "group-access",
    text: "访问与授权",
    items: [
      { icon: <IconUserGroup />, itemKey: "users_channels", text: "用户管理" },
      { icon: <IconShield />, itemKey: "bindings", text: "资源绑定" }
    ]
  },
  {
    itemKey: "group-governance",
    text: "治理与质量",
    items: [
      { icon: <IconPuzzle />, itemKey: "plugin_policy", text: <PlannedText>插件钩子</PlannedText> },
      { icon: <IconKey />, itemKey: "capabilities", text: <PlannedText>能力注册</PlannedText> },
      { icon: <IconTestScore />, itemKey: "eval", text: <PlannedText>能力评测</PlannedText> },
      { icon: <IconHistory />, itemKey: "audit", text: "操作审计" }
    ]
  }
];

// 规划中的页面在导航里置灰，与实际可用页区分。
function PlannedText({ children }: { readonly children: string }) {
  return <Typography.Text type="tertiary">{children}</Typography.Text>;
}

const OPEN_GROUP_KEYS = ["group-overview", "group-define", "group-access", "group-governance"];

function renderView(view: ConsoleView, api: ConsoleApi) {
  if (view === "users_channels") {
    return <UsersChannelsPage api={api} />;
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
