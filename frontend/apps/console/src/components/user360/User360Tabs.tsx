/**
 * C405 User360Tabs（TASK-013 / CMP-10）：五维度 Tab
 * （Identity 身份 / Profile 画像（含偏好）/ Capability 能力授权 / Policy 策略 / Activity 活动）。
 * 展示组件：props 只读；无数据维度显示「该用户暂无数据」。
 */
import { Card, Descriptions, Empty, Tabs, Typography } from "@douyinfe/semi-ui";

import type { User360Summary } from "../../types/console";

interface User360TabsProps {
  readonly summary: User360Summary;
}

const EMPTY_TEXT = "该用户暂无数据";

/** P2（review）：嵌套值展示——避免 `String(object)` 渲染成 `[object Object]`。 */
function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => displayValue(item)).join("、");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function User360Tabs({ summary }: User360TabsProps) {
  return (
    <div aria-label="User 360 Tabs" className="user360-tabs">
      <Tabs type="line" defaultActiveKey="identity">
        <Tabs.TabPane itemKey="identity" tab="身份">
          <Descriptions row>
            <Descriptions.Item itemKey="平台用户">
              {summary.identity.platform_user_id}
            </Descriptions.Item>
            <Descriptions.Item itemKey="显示名">{summary.identity.display_name}</Descriptions.Item>
            <Descriptions.Item itemKey="渠道数">{summary.identity.channels.length}</Descriptions.Item>
          </Descriptions>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="profile" tab="画像">
          {summary.profile === null && summary.preferences === null ? (
            <Empty description={EMPTY_TEXT} />
          ) : (
            <>
              <Card title="画像">
                {summary.profile ? (
                  <Descriptions row>
                    {Object.entries(summary.profile).map(([key, value]) => (
                      <Descriptions.Item itemKey={key} key={key}>
                        {displayValue(value)}
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : (
                  <Typography.Text type="tertiary">暂无画像</Typography.Text>
                )}
              </Card>
              <Card title="偏好">
                {summary.preferences ? (
                  <Descriptions row>
                    {Object.entries(summary.preferences).map(([key, value]) => (
                      <Descriptions.Item itemKey={key} key={key}>
                        {displayValue(value)}
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                ) : (
                  <Typography.Text type="tertiary">暂无偏好</Typography.Text>
                )}
              </Card>
            </>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="capability" tab="能力授权">
          {summary.capabilities.length === 0 ? (
            <Empty description={EMPTY_TEXT} />
          ) : (
            <Descriptions row>
              {summary.capabilities.map((capability, index) =>
                Object.entries(capability).map(([key, value]) => (
                  <Descriptions.Item itemKey={`${key} #${index + 1}`} key={`${index}-${key}`}>
                    {displayValue(value)}
                  </Descriptions.Item>
                ))
              )}
            </Descriptions>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="policy" tab="策略">
          {summary.policy.length === 0 ? (
            <Empty description={EMPTY_TEXT} />
          ) : (
            <Descriptions row>
              {summary.policy.map((policy, index) =>
                Object.entries(policy).map(([key, value]) => (
                  <Descriptions.Item itemKey={`${key} #${index + 1}`} key={`${index}-${key}`}>
                    {displayValue(value)}
                  </Descriptions.Item>
                ))
              )}
            </Descriptions>
          )}
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="activity" tab="活动">
          <Descriptions row>
            <Descriptions.Item itemKey="活动记录数">{summary.activity_count}</Descriptions.Item>
          </Descriptions>
          {summary.activity_count === 0 ? (
            <Empty description={EMPTY_TEXT} />
          ) : (
            <Typography.Text type="tertiary">最近活动经操作审计追溯</Typography.Text>
          )}
        </Tabs.TabPane>
      </Tabs>
    </div>
  );
}
