export type P1View =
  | "users_channels"
  | "plugin_policy"
  | "capabilities"
  | "eval"
  | "runtime_status";

export type ConsoleView =
  | "overview"
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
