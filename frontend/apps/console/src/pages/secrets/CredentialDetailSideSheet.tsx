import { useEffect, useState } from "react";

import { Descriptions, SideSheet, Spin, Tag } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusTag } from "../../components/StatusTag";
import { VersionHistory } from "../../components/VersionHistory";
import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CredentialDetailSideSheetProps {
  readonly api: ConsoleApi;
  readonly resourceId: string | null;
  readonly onClose: () => void;
}

/** TASK-009：凭据详情只读 SideSheet（§7.2 Detail = Read Only Projection）。
 *
 * 只读 Descriptions/Tag；保留 SecretRef 展示（有意设计，非明文，规则 17）；
 * 禁止任何可写表单组件（Input/Select/Switch/TextArea）。编辑走列表「编辑」
 * 元数据 Modal（§7.3 简单对象允许 Edit Modal）。
 */
export function CredentialDetailSideSheet({
  api,
  resourceId,
  onClose
}: CredentialDetailSideSheetProps) {
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (resourceId === null) return;
    let active = true;
    setError(null);
    setResource(null);
    void api.getResource("secret", resourceId).then(
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

  const spec = (resource?.spec ?? {}) as {
    name?: string;
    purpose?: string;
    secret_ref?: string;
    revoked?: boolean;
  };

  return (
    <SideSheet
      motion={false}
      onCancel={onClose}
      title="凭据详情"
      visible={resourceId !== null}
      width={800}
    >
      <ErrorBanner message={error} />
      {resource === null ? (
        <div aria-label="详情加载中">
          <Spin />
        </div>
      ) : (
        <div aria-label="凭据详情内容">
          <Descriptions
            align="left"
            data={[
              { key: "名称", value: String(spec.name ?? resource.resourceId) },
              { key: "资源 ID", value: resource.resourceId },
              { key: "SecretRef", value: String(spec.secret_ref ?? "-") },
              { key: "类型", value: String(spec.purpose || "-") },
              {
                key: "状态",
                value: (
                  <span>
                    <StatusTag status={resource.status} />
                    {spec.revoked ? (
                      <Tag color="red" style={{ marginLeft: 8 }}>
                        已禁用
                      </Tag>
                    ) : null}
                  </span>
                )
              },
              { key: "版本", value: resource.version },
              { key: "更新时间", value: <RelativeTime value={resource.updatedAt} /> }
            ]}
          />
          <VersionHistory api={api} resourceId={resource.resourceId} resourceType="secret" />
        </div>
      )}
    </SideSheet>
  );
}
