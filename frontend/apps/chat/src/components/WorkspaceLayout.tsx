/**
 * X401 WorkspaceLayout（瘦身后）：仅保留 access 门控 + Outlet 透传壳。
 * 侧边导航与顶栏已拆除（chat 单页不需要）；对话框自带头部。
 * 未绑定用户仍走 /bind 绑定流程（服务端逻辑保留）。
 */
import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";

import { Layout, Spin } from "@douyinfe/semi-ui";

import type { ChatApi, ChatAccess } from "../types/chat";
import { InvalidLink } from "./InvalidLink";

interface WorkspaceLayoutProps {
  readonly api: ChatApi;
}

export function WorkspaceLayout({ api }: WorkspaceLayoutProps) {
  const [access, setAccess] = useState<ChatAccess | null>(null);
  const [resolving, setResolving] = useState(api.resolveAccess !== undefined);

  useEffect(() => {
    if (!api.resolveAccess) return;
    let active = true;
    void api
      .resolveAccess()
      .then((resolved) => {
        if (active) setAccess(resolved);
      })
      .catch(() => {
        if (active) setAccess(null);
      })
      .finally(() => {
        if (active) setResolving(false);
      });
    return () => {
      active = false;
    };
  }, [api]);

  if (resolving) {
    return (
      <div className="workspace-loading" role="status" aria-label="正在加载工作区">
        <Spin size="large" />
      </div>
    );
  }

  if (!access) {
    return <InvalidLink />;
  }

  return (
    <Layout className="workspace-shell">
      <Layout.Content className="workspace-content">
        <Outlet />
      </Layout.Content>
    </Layout>
  );
}
