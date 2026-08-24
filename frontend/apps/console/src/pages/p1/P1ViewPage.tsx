import { useEffect, useState } from "react";

import { Button, Descriptions, Empty, Spin, Table, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import type { ConsoleApi, ControlPlaneItem } from "../../types/console";
import type { P1View } from "../../types/navigation";

interface P1ViewPageProps {
  readonly api: ConsoleApi;
  readonly view: P1View;
  readonly showHeader?: boolean;
}

const titles: Record<P1View, string> = {
  capabilities: "Capability Registry",
  eval: "Eval",
  plugin_policy: "Plugin / Hook Policy",
  runtime_status: "Runtime Status",
  users_channels: "Users / Channels"
};

export function P1ViewPage({ api, view, showHeader = true }: P1ViewPageProps) {
  const [items, setItems] = useState<readonly ControlPlaneItem[]>([]);
  const [selected, setSelected] = useState<ControlPlaneItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const title = titles[view];

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setSelected(null);
    void api.listP1View(view).then(
      (result) => {
        if (!active) return;
        setItems(result);
        setLoading(false);
      },
      (cause: unknown) => {
        if (!active) return;
        setError(toErrorMessage(cause));
        setLoading(false);
      }
    );
    return () => {
      active = false;
    };
  }, [api, view]);

  return (
    <div className="page-stack">
      {showHeader ? <PageHeader title={title} /> : null}
      <ErrorBanner message={error} />
      {loading ? <LoadingState title={title} /> : null}
      {!loading && !error && items.length === 0 ? <EmptyState title={title} /> : null}
      {!loading && !error && items.length > 0 ? (
        <P1Table items={items} onSelect={setSelected} />
      ) : null}
      {selected ? <P1Detail item={selected} title={title} /> : null}
    </div>
  );
}

function LoadingState({ title }: { readonly title: string }) {
  return (
    <div aria-label={`${title} loading`} className="p1-state" role="status">
      <Spin size="large" />
    </div>
  );
}

function EmptyState({ title }: { readonly title: string }) {
  return (
    <div className="p1-state">
      <Empty description={`${title} 暂无数据`} />
    </div>
  );
}

function P1Table({
  items,
  onSelect
}: {
  readonly items: readonly ControlPlaneItem[];
  readonly onSelect: (item: ControlPlaneItem) => void;
}) {
  const columns = [
    {
      dataIndex: "id",
      render: (_value: unknown, record: ControlPlaneItem) => (
        <Button onClick={() => onSelect(record)} type="tertiary">{record.id}</Button>
      ),
      title: "ID"
    },
    { dataIndex: "name", title: "Name" },
    {
      dataIndex: "status",
      render: (value: unknown) => <Typography.Text>{String(value)}</Typography.Text>,
      title: "Status"
    }
  ];
  return <Table columns={columns} dataSource={[...items]} pagination={false} rowKey="id" />;
}

function P1Detail({ item, title }: { readonly item: ControlPlaneItem; readonly title: string }) {
  return (
    <section aria-label={`${title} Detail`} className="panel">
      <Typography.Title heading={4}>Detail</Typography.Title>
      <Descriptions row>
        <Descriptions.Item itemKey="ID">{item.id}</Descriptions.Item>
        <Descriptions.Item itemKey="Name">{item.name}</Descriptions.Item>
        <Descriptions.Item itemKey="Status">{item.status}</Descriptions.Item>
      </Descriptions>
      <Typography.Paragraph>{item.detail}</Typography.Paragraph>
    </section>
  );
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "加载失败";
}
