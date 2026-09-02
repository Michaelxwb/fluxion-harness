import { useEffect, useState } from "react";

import { Descriptions, SideSheet, Spin } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import { VersionHistory } from "../../components/VersionHistory";
import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface AgentDetailSideSheetProps {
  readonly api: ConsoleApi;
  readonly resourceId: string | null;
  readonly onClose: () => void;
}

/** TASK-013：Agent 详情只读 SideSheet——Detail = Read Only Projection。
 *
 * 只读 Descriptions/Tag；禁止任何可写表单组件（Input/Select/Switch/TextArea）。
 * 编辑从列表「编辑」进入专属 Editor（TASK-014），不从详情发起。
 */
export function AgentDetailSideSheet({ api, resourceId, onClose }: AgentDetailSideSheetProps) {
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (resourceId === null) return;
    let active = true;
    setError(null);
    setResource(null);
    void api.getResource("agent_definition", resourceId).then(
      (loaded) => {
        if (active) setResource(loaded);
      },
      (cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    );
    return () => {
      active = false;
    };
  }, [api, resourceId]);

  const name = resource?.spec?.name ?? "";
  const modelPolicy = resource?.spec?.model_policy as
    | { primary_model_ref?: { id?: string } }
    | undefined;
  const capabilityCount = Array.isArray(resource?.spec?.capabilities)
    ? resource.spec.capabilities.length
    : 0;

  return (
    <SideSheet
      motion={false}
      onCancel={onClose}
      title="智能体详情"
      visible={resourceId !== null}
      width={480}
    >
      <ErrorBanner message={error} />
      {resource === null ? (
        <div aria-label="详情加载中">
          <Spin />
        </div>
      ) : (
        <div aria-label="智能体详情内容">
          <Descriptions
            align="left"
            data={[
              { key: "名称", value: String(name || resource.resourceId) },
              { key: "资源 ID", value: resource.resourceId },
              { key: "状态", value: <StatusTag status={resource.status} /> },
              { key: "版本", value: resource.version },
              { key: "可见性", value: resource.visibility },
              { key: "默认模型", value: modelPolicy?.primary_model_ref?.id ?? "-" },
              { key: "能力数", value: String(capabilityCount) },
              { key: "更新时间", value: resource.updatedAt }
            ]}
          />
          <VersionHistory api={api} resourceId={resource.resourceId} resourceType="agent_definition" />
        </div>
      )}
    </SideSheet>
  );
}
