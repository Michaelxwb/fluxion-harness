import { useState } from "react";

import { Layout, Nav, Typography } from "@douyinfe/semi-ui";
import {
  IconActivity,
  IconFlowChartStroked,
  IconHistory,
  IconKey,
  IconList,
  IconPulse,
  IconPuzzle,
  IconShield,
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
import "./styles.css";

interface ConsoleAppProps {
  readonly api: ConsoleApi;
  readonly initialView?: ConsoleView;
}

export function ConsoleApp({ api, initialView = "resources" }: ConsoleAppProps) {
  const [activeView, setActiveView] = useState<ConsoleView>(initialView);

  return (
    <Layout className="app-shell">
      <Layout.Sider className="app-sidebar">
        <div className="brand">
          <Typography.Title heading={4}>Fluxion Console</Typography.Title>
          <Typography.Text type="tertiary">Control Plane</Typography.Text>
        </div>
        <Nav
          items={navItems}
          onSelect={(data) => setActiveView(toConsoleView(String(data.itemKey)))}
          selectedKeys={[activeView]}
        />
      </Layout.Sider>
      <Layout.Content className="app-content">{renderView(activeView, api)}</Layout.Content>
    </Layout>
  );
}

const navItems = [
  { icon: <IconList />, itemKey: "resources", text: "Runtime Profiles" },
  { icon: <IconFlowChartStroked />, itemKey: "workflows", text: "Workflows" },
  { icon: <IconUserGroup />, itemKey: "users_channels", text: "Users / Channels" },
  { icon: <IconPuzzle />, itemKey: "plugin_policy", text: "Plugin / Hook Policy" },
  { icon: <IconKey />, itemKey: "capabilities", text: "Capabilities" },
  { icon: <IconTestScore />, itemKey: "eval", text: "Eval" },
  { icon: <IconPulse />, itemKey: "runtime_status", text: "Runtime Status" },
  { icon: <IconShield />, itemKey: "bindings", text: "Bindings / Policy" },
  { icon: <IconActivity />, itemKey: "runs", text: "Runs / Trace" },
  { icon: <IconHistory />, itemKey: "audit", text: "Audit" }
];

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
