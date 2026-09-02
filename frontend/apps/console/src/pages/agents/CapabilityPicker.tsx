import { useEffect, useState } from "react";

import type {
  CapabilitySelection,
  CapabilitySelectionType,
  ConsoleApi
} from "../../types/console";

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
  const [options, setOptions] = useState<
    readonly { id: string; kind: CapabilitySelectionType; version: string; label: string }[]
  >([]);
  useEffect(() => {
    void (async () => {
      const merged: { id: string; kind: CapabilitySelectionType; version: string; label: string }[] = [];
      for (const kind of ["skill", "tool", "mcp"] as const) {
        const items = await api.listVisibleResources(kind);
        merged.push(
          ...items.map((i) => ({
            id: i.resourceId,
            kind,
            version: i.currentVersion,
            label: i.displayName || i.resourceId
          }))
        );
      }
      setOptions(merged);
    })();
  }, [api]);

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
