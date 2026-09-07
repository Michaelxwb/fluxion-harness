import { useState } from "react";

import { Button, Toast, Tooltip, Typography } from "@douyinfe/semi-ui";
import { IconCopy } from "@douyinfe/semi-icons";

interface ResourceIdProps {
  readonly id: string;
  /** 超过该长度截断（默认前 10…后 4）。 */
  readonly keepEnds?: readonly [number, number];
}

const { Text } = Typography;

/** 资源 ID：等宽截断 + hover 全文 + 一键复制。 */
export function ResourceId({ id, keepEnds = [10, 4] as const }: ResourceIdProps) {
  const [head, tail] = keepEnds;
  const display = id.length > head + tail + 1 ? `${id.slice(0, head)}…${id.slice(-tail)}` : id;
  return (
    <span className="resource-id" title={id}>
      <Tooltip content={id}>
        <Text code>{display}</Text>
      </Tooltip>
      <CopyButton id={id} />
    </span>
  );
}

function CopyButton({ id }: { readonly id: string }) {
  const [copied, setCopied] = useState(false);
  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      Toast.success("已复制");
    } catch {
      Toast.error("复制失败，请手动复制");
    }
  }
  return (
    <Button
      aria-label="复制资源 ID"
      icon={<IconCopy />}
      onClick={() => void copy()}
      size="small"
      theme="borderless"
      type={copied ? "primary" : "tertiary"}
    />
  );
}
