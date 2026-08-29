export type P1View =
  | "users_channels"
  | "plugin_policy"
  | "capabilities"
  | "eval"
  | "runtime_status";

export type ConsoleView =
  | "overview"
  | "agent_studio"
  | "platform_runtime_profiles"
  | "platform_secrets"
  | "platform_models"
  | "policies"
  | "capabilities"
  | "platform_assets"
  | "resources"
  | "workflows"
  | "bindings"
  | "runs"
  | "audit"
  | P1View;

const p1Views: readonly P1View[] = [
  "users_channels",
  "plugin_policy",
  "capabilities",
  "eval",
  "runtime_status"
];

export function isP1View(value: ConsoleView): value is P1View {
  return p1Views.includes(value as P1View);
}

export function isConsoleView(value: string): value is ConsoleView {
  return (
    value === "overview" ||
    value === "agent_studio" ||
    value === "platform_runtime_profiles" ||
    value === "platform_secrets" ||
    value === "platform_models" ||
    value === "policies" ||
    value === "capabilities" ||
    value === "platform_assets" ||
    value === "resources" ||
    value === "workflows" ||
    value === "bindings" ||
    value === "runs" ||
    value === "audit" ||
    p1Views.includes(value as P1View)
  );
}

/**
 * TASK-004（RISK-P4-03）：state 导航 → Router 迁移的 `ConsoleView` ↔ 路径映射。
 * IA 对齐 design §3.2（/overview、/build/*、/users、/governance/*、/operations/*、/platform/*）；
 * 视图语义保持不变，测试/深链继续以 ConsoleView 寻址。
 */
const VIEW_PATHS: Readonly<Record<ConsoleView, string>> = {
  agent_studio: "/build/agent-studio",
  audit: "/governance/audit",
  bindings: "/governance/bindings",
  capabilities: "/build/capabilities",
  eval: "/build/eval",
  overview: "/overview",
  platform_assets: "/platform/assets",
  platform_models: "/platform/models",
  platform_runtime_profiles: "/platform/runtime-profiles",
  platform_secrets: "/platform/secrets",
  plugin_policy: "/governance/plugin-policy",
  policies: "/governance/policies",
  resources: "/build/agents",
  runs: "/operations/runs",
  runtime_status: "/operations/runtime-status",
  users_channels: "/users",
  workflows: "/build/workflows"
};

export function viewToPath(view: ConsoleView): string {
  return VIEW_PATHS[view];
}

export function pathToView(path: string): ConsoleView {
  const match = Object.entries(VIEW_PATHS).find(([, route]) => route === path);
  // 未匹配路径回退 resources（对齐迁移前 toConsoleView 的默认行为）
  return match ? (match[0] as ConsoleView) : "resources";
}
