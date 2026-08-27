export type P1View =
  | "users_channels"
  | "plugin_policy"
  | "capabilities"
  | "eval"
  | "runtime_status";

export type ConsoleView =
  | "overview"
  | "platform_runtime_profiles"
  | "platform_secrets"
  | "platform_models"
  | "policies"
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
    value === "platform_runtime_profiles" ||
    value === "platform_secrets" ||
    value === "platform_models" ||
    value === "policies" ||
    value === "platform_assets" ||
    value === "resources" ||
    value === "workflows" ||
    value === "bindings" ||
    value === "runs" ||
    value === "audit" ||
    p1Views.includes(value as P1View)
  );
}
