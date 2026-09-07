import React from "react";
import { Empty, Typography } from "@douyinfe/semi-ui";

/** token 无效/过期提示页：纯提示，请用户重新获取链接。 */
export function InvalidLink() {
  return (
    <div className="invalid-link">
      <Empty
        title="对话链接失效"
        description={
          <Typography.Text type="tertiary">
            请联系管理员重新获取对话链接
          </Typography.Text>
        }
      />
    </div>
  );
}
