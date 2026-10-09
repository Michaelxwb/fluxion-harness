import { CopyableText } from '../../components/common/CopyableText';

export interface EntityNameTextProps {
  /** 名称权威源在 Console 的 `control.*`；缺失（未登记/已删/跨租户）时回落到 id。 */
  name?: string | null;
  /** 实体 id：名称缺失时展示前 8 位，完整值进 title 与复制。 */
  id: string;
  /** 名称存在时附带的 key（Skill 列口径：`名称 (key)`）。 */
  keySuffix?: string | null;
}

/** 任务/定时任务列表的实体单元格：名称优先、短 id 兜底（`id` 前 8 位）。 */
export function EntityNameText({ name, id, keySuffix }: EntityNameTextProps) {
  // key 与名称相同（常见：skill key == skill name）时不重复展示 `名称 (key)`。
  const suffix = keySuffix && keySuffix !== name ? ` (${keySuffix})` : '';
  const full = name ? `${name}${suffix}` : id;
  return <CopyableText display={name ? full : id.slice(0, 8)} full={full} />;
}
