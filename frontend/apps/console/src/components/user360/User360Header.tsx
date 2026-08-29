/** C405 User360Header（TASK-013 / CMP-10）：用户身份概要（展示组件）。 */
import { Card, Descriptions, Tag } from "@douyinfe/semi-ui";

import type { User360Summary } from "../../types/console";

interface User360HeaderProps {
  readonly summary: User360Summary;
}

export function User360Header({ summary }: User360HeaderProps) {
  return (
    <Card aria-label="User 360 Header" title="身份概要">
      <Descriptions row>
        <Descriptions.Item itemKey="平台用户">
          {summary.identity.platform_user_id}
        </Descriptions.Item>
        <Descriptions.Item itemKey="显示名">{summary.identity.display_name}</Descriptions.Item>
        <Descriptions.Item itemKey="渠道数">{summary.identity.channels.length}</Descriptions.Item>
      </Descriptions>
      <div className="user360-channels">
        {summary.identity.channels.map((channel) => (
          <Tag key={`${channel.channel_type}:${channel.channel_user_id}`}>
            {channel.channel_type} · {channel.channel_user_id}
          </Tag>
        ))}
      </div>
    </Card>
  );
}
