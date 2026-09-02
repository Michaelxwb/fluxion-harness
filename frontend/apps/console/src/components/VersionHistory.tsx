import { useEffect, useState } from "react";

import { Space, Table, Tag, Typography } from "@douyinfe/semi-ui";

import { StatusTag } from "./StatusTag";
import type { ConsoleApi, ResourceType, ResourceVersion } from "../types/console";

interface VersionHistoryProps {
  readonly api: ConsoleApi;
  readonly resourceType: ResourceType;
  readonly resourceId: string;
}

/** TASK-021（返工）：只读版本历史 + Diff（remediation §3.7 / §14.2）。
 *
 * 版本列表按版本号语义排序（"2" < "10"，非数字回退字符串序）；
 * 默认对比最近两个版本：键级变更摘要（+/±/-）+ spec 只读并排。
 */
export function VersionHistory({ api, resourceType, resourceId }: VersionHistoryProps) {
  const [versions, setVersions] = useState<readonly ResourceVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api
      .listVersions(resourceType, resourceId, { page: 1, pageSize: 20 })
      .then(
        (page) => {
          if (active) setVersions(page.items);
        },
        (cause: unknown) => {
          if (active) setError(cause instanceof Error ? cause.message : "加载失败");
        }
      );
    return () => {
      active = false;
    };
  }, [api, resourceType, resourceId]);

  const sorted = [...(versions ?? [])].sort((a, b) => compareVersionDesc(a.version, b.version));
  const latest = sorted[0];
  const previous = sorted[1];

  return (
    <div aria-label="版本历史">
      {error !== null ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      {versions === null && error === null ? (
        <Typography.Text type="tertiary">加载版本…</Typography.Text>
      ) : null}
      {versions !== null ? (
        <Table
          columns={[
            { title: "版本", dataIndex: "version" },
            {
              title: "状态",
              dataIndex: "status",
              render: (value: string) => <StatusTag status={value as ResourceVersion["status"]} />
            },
            { title: "更新时间", dataIndex: "updatedAt" }
          ]}
          dataSource={sorted.map((version) => ({ key: version.version, ...version }))}
          pagination={false}
          size="small"
        />
      ) : null}
      {latest !== undefined && previous !== undefined ? (
        <div aria-label="版本 Diff">
          <Typography.Text type="tertiary">
            对比 {previous.version} → {latest.version}（只读）
          </Typography.Text>
          <div aria-label="版本变更字段" style={{ margin: "8px 0" }}>
            {diffKeys(previous.spec, latest.spec).map((key) => (
              <Tag key={key} style={{ margin: 2 }}>
                {key}
              </Tag>
            ))}
          </div>
          <Space align="start">
            <pre className="version-diff">{formatSpec(previous.spec)}</pre>
            <pre className="version-diff">{formatSpec(latest.spec)}</pre>
          </Space>
        </div>
      ) : null}
    </div>
  );
}

/** 版本号语义排序（"2" < "10"；容忍 v 前缀，非数字回退字符串序），降序。 */
function compareVersionDesc(a: string, b: string): number {
  const na = Number(a.replace(/^v/, ""));
  const nb = Number(b.replace(/^v/, ""));
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return nb - na;
  return b.localeCompare(a);
}

/** 顶层键级变更：+ 新增 / ± 修改 / - 删除（值为深比较）。 */
function diffKeys(
  previous: Record<string, unknown>,
  latest: Record<string, unknown>
): string[] {
  const changes: string[] = [];
  for (const key of new Set([...Object.keys(previous), ...Object.keys(latest)])) {
    if (!(key in previous)) changes.push(`+${key}`);
    else if (!(key in latest)) changes.push(`-${key}`);
    else if (JSON.stringify(previous[key]) !== JSON.stringify(latest[key])) changes.push(`±${key}`);
  }
  return changes;
}

function formatSpec(spec: Record<string, unknown>): string {
  return JSON.stringify(spec, null, 2);
}
