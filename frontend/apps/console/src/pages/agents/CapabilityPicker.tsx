import { useState } from "react";

import { Input, Typography } from "@douyinfe/semi-ui";

import type {
  CapabilitySelection,
  CapabilitySelectionType,
  ConsoleApi
} from "../../types/console";
import { useRemoteResourceOptions } from "../../components/useRemoteResourceOptions";

const CAPABILITY_TYPE_LABELS: Record<CapabilitySelectionType, string> = {
  skill: "Skill",
  tool: "Tool",
  mcp: "MCP"
};

/** closure TASK-008（P1C-04）：typed 能力选择器——产出 CapabilitySelection
 * 三元组（type + capabilityRef + versionPin），展示「名称 + 类型 + 版本」。 */
export function CapabilityPicker({
  api,
  selected,
  onChange
}: {
  readonly api: ConsoleApi;
  readonly selected: readonly CapabilitySelection[];
  readonly onChange: (next: readonly CapabilitySelection[]) => void;
}) {
  // FEAT-03：能力候选远程搜索（skill/tool/mcp，大数据集可达；已选项不受搜索影响）。
  const [keyword, setKeyword] = useState("");
  const remote = useRemoteResourceOptions(api, ["skill", "tool", "mcp"], true);
  const options = remote.options.map((item) => {
    const [kind, rest] = item.value.split(":", 2);
    const [id, version] = (rest ?? "").split("@", 2);
    return {
      id,
      kind: kind as CapabilitySelectionType,
      version,
      label: item.label
    };
  });

  const toggle = (option: { id: string; kind: CapabilitySelectionType; version: string }) => {
    const exists = selected.some(
      (item) => item.type === option.kind && item.capabilityRef === option.id
    );
    onChange(
      exists
        ? selected.filter((item) => !(item.type === option.kind && item.capabilityRef === option.id))
        : [
            ...selected,
            { type: option.kind, capabilityRef: option.id, versionPin: option.version }
          ]
    );
  };

  return (
    <div aria-label="能力绑定选择">
      <Input
        aria-label="搜索能力"
        onChange={(value) => {
          setKeyword(String(value));
          remote.onSearch(String(value));
        }}
        placeholder="输入关键词搜索能力（skill/tool/mcp）"
        value={keyword}
      />
      {remote.truncated ? (
        <Typography.Text type="tertiary" size="small">
          仅显示前 {remote.options.length} 条匹配，请细化关键词
        </Typography.Text>
      ) : null}
      {options.map((option) => {
        const checked = selected.some(
          (item) => item.type === option.kind && item.capabilityRef === option.id
        );
        return (
          <label key={`${option.kind}:${option.id}`} style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={checked}
              onChange={() => toggle(option)}
            />
            {`${option.label} ${CAPABILITY_TYPE_LABELS[option.kind]} v${option.version}`}
          </label>
        );
      })}
    </div>
  );
}
