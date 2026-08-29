/**
 * Phase 5 TASK-012（C405 深链）：`/users/:platformUserId` 路由页。
 *
 * User 360 从 SideSheet（组件状态）升级为 URL 路由——深链/刷新直达；
 * 复用 User360Header/User360Tabs（五维视图）；四态完备
 * （loading/empty/error/success）；返回列表经路由跳转。
 */
import { useEffect, useState } from "react";

import { Button, Skeleton, Typography } from "@douyinfe/semi-ui";
import { IconArrowLeft } from "@douyinfe/semi-icons";
import { useNavigate, useParams } from "react-router-dom";

import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/PageHeader";
import { User360Header } from "../../components/user360/User360Header";
import { User360Tabs } from "../../components/user360/User360Tabs";
import type { ConsoleApi, User360Summary } from "../../types/console";

interface User360PageProps {
  readonly api: ConsoleApi;
}

export function User360Page({ api }: User360PageProps) {
  const { platformUserId = "" } = useParams();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<User360Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setSummary(null);
    setError(null);
    void api
      .getUser360(platformUserId)
      .then((loaded) => {
        if (active) setSummary(loaded);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, platformUserId]);

  return (
    <div className="page-stack">
      <PageHeader
        description={`平台用户 ${platformUserId} 的五维聚合视图（身份/画像/能力/策略/活动）。`}
        extra={
          <Button
            icon={<IconArrowLeft />}
            onClick={() => navigate("/users")}
          >
            返回用户列表
          </Button>
        }
        title="User 360"
      />
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setError(null)}
        />
      ) : summary === null ? (
        <div aria-label="User 360 加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <div aria-label="User 360" className="page-stack">
          <User360Header summary={summary} />
          <User360Tabs summary={summary} />
          <Typography.Text type="tertiary">活动记录数：{summary.activity_count}</Typography.Text>
        </div>
      )}
    </div>
  );
}
