import { Tag } from "@douyinfe/semi-ui";

type ActionTagColor = "blue" | "red" | "green" | "grey" | "amber";

/** 操作类型着色：publish 蓝 / 高危红 / chat_access 绿 / secret 灰 / 其他琥珀。 */
export function ActionTag({ action }: { readonly action: string }) {
  return <Tag color={actionTagColor(action)}>{action}</Tag>;
}

export function isRiskyAction(action: string): boolean {
  return /revoke|disable|deprecate|rollback/i.test(action);
}

function actionTagColor(action: string): ActionTagColor {
  if (isRiskyAction(action)) {
    return "red";
  }
  if (action.startsWith("publish")) {
    return "blue";
  }
  if (action.startsWith("chat_access")) {
    return "green";
  }
  if (action.startsWith("secret")) {
    return "grey";
  }
  return "amber";
}
