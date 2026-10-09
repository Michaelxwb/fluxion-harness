import { Typography } from '@douyinfe/semi-ui';

export interface CopyableTextProps {
  /** 单元格展示文本（可为短 id 等截断值）。 */
  display: string;
  /** 悬停 title 与复制内容：始终是完整值。 */
  full: string;
}

/** 列表实体单元格的通用文本：`title` 与复制都取完整值，展示可截断。 */
export function CopyableText({ display, full }: CopyableTextProps) {
  return (
    <span title={full}>
      <Typography.Text copyable={{ content: full }}>{display}</Typography.Text>
    </span>
  );
}
