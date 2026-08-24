import type { ReactNode } from "react";

import { Typography } from "@douyinfe/semi-ui";

interface PageHeaderProps {
  readonly title: string;
  readonly description?: string;
  readonly extra?: ReactNode;
}

export function PageHeader({ description, extra, title }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div>
        <Typography.Title heading={2}>{title}</Typography.Title>
        {description ? <Typography.Text type="tertiary">{description}</Typography.Text> : null}
      </div>
      {extra ? <div className="page-header__extra">{extra}</div> : null}
    </div>
  );
}
