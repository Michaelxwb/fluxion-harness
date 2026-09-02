export type P1View =
  | "users_channels"
  | "plugin_policy"
  | "capabilities";

export type ConsoleView =
  | "overview"
  | "platform_secrets"
  | "platform_models"
  | "policies"
  | "capabilities"
  | "resources"
  | "workflows"
  | "bindings"
  | "runs"
  | "audit"
  | P1View;

const p1Views: readonly P1View[] = [
  "users_channels",
  "plugin_policy",
  "capabilities"
];

export function isP1View(value: ConsoleView): value is P1View {
  return p1Views.includes(value as P1View);
}

export function isConsoleView(value: string): value is ConsoleView {
  return (
    value === "overview" ||
    value === "platform_secrets" ||
    value === "platform_models" ||
    value === "policies" ||
    value === "capabilities" ||
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
 * agent-studio/eval/queues/workers/runtime-status/runtime-profiles/assets 已随页面
 * 移除退出映射（TASK-016 返工）。`resources` 保留为智能体目录的 legacy 别名
 * （迁移前 toConsoleView 默认视图，测试/深链兼容）。
 */
const VIEW_PATHS: Readonly<Record<ConsoleView, string>> = {
  audit: "/governance/audit",
  bindings: "/governance/bindings",
  capabilities: "/build/capabilities",
  overview: "/overview",
  platform_models: "/platform/models",
  platform_secrets: "/platform/credentials",
  plugin_policy: "/governance/plugin-policy",
  policies: "/governance/policies",
  resources: "/build/agents",
  runs: "/operations/runs",
  users_channels: "/users",
  workflows: "/build/workflows"
};

export function viewToPath(view: ConsoleView): string {
  return VIEW_PATHS[view];
}

export function pathToView(path: string): ConsoleView {
  const match = Object.entries(VIEW_PATHS).find(([, route]) => route === path);
  // 未匹配路径回退智能体目录（对齐迁移前 toConsoleView 的默认行为）
  return match ? (match[0] as ConsoleView) : "resources";
}
