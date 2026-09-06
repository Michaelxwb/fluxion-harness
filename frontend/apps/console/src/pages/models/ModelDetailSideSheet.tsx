import { useEffect, useState } from "react";

import { Descriptions, SideSheet, Spin, Tag } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import { VersionHistory } from "../../components/VersionHistory";
import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface ModelDetailSideSheetProps {
  readonly api: ConsoleApi;
  readonly resourceId: string | null;
  readonly onClose: () => void;
}

/** TASK-010：模型服务（Provider）只读详情 SideSheet（§7.2）。
 *
 * 只读 Descriptions/Tag；无任何编辑控件。挂载模型（ModelDefinition）
 * 以 Tag 列表呈现（provider_ref 分组，列表页同一数据源）。
 */
export function ModelDetailSideSheet({
  api,
  resourceId,
  onClose
}: ModelDetailSideSheetProps) {
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [models, setModels] = useState<readonly string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (resourceId === null) return;
    let active = true;
    setError(null);
    setResource(null);
    setModels([]);
    void (async () => {
      try {
        const loaded = await api.getResource("model_provider", resourceId);
        const modelsPage = await api.listResources("model_definition");
        const details = await Promise.all(
          modelsPage.items.map((item) => api.getResource("model_definition", item.resourceId))
        );
        if (!active) return;
        setResource(loaded);
        setModels(
          details
            .filter(
              (detail) =>
                ((detail.spec as { provider_ref?: { id?: string } }).provider_ref?.id ??
                  "") === resourceId
            )
            .map(
              (detail) =>
                String((detail.spec as { name?: string }).name ?? detail.resourceId)
            )
        );
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, resourceId]);

  const spec = (resource?.spec ?? {}) as {
    base_url?: string;
    credential_ref?: string;
    protocol?: string;
  };

  return (
    <SideSheet
      motion={false}
      onCancel={onClose}
      title="模型服务详情"
      visible={resourceId !== null}
      width={480}
    >
      <ErrorBanner message={error} />
      {resource === null ? (
        <div aria-label="详情加载中">
          <Spin />
        </div>
      ) : (
        <div aria-label="模型服务详情内容">
          <Descriptions
            align="left"
            data={[
              { key: "资源 ID", value: resource.resourceId },
              { key: "协议", value: String(spec.protocol ?? "-") },
              { key: "Endpoint", value: String(spec.base_url ?? "-") },
              { key: "SecretRef", value: String(spec.credential_ref ?? "-") },
              { key: "状态", value: <StatusTag status={resource.status} /> },
              { key: "版本", value: resource.version },
              { key: "更新时间", value: resource.updatedAt }
            ]}
          />
          <div style={{ marginTop: 16 }}>
            <Descriptions
              align="left"
              data={[
                {
                  key: "挂载模型",
                  value:
                    models.length > 0 ? (
                      <span>
                        {models.map((model) => (
                          <Tag key={model} style={{ margin: 2 }}>
                            {model}
                          </Tag>
                        ))}
                      </span>
                    ) : (
                      "-"
                    )
                }
              ]}
            />
          </div>
          <VersionHistory
            api={api}
            resourceId={resource.resourceId}
            resourceType="model_provider"
          />
        </div>
      )}
    </SideSheet>
  );
}
