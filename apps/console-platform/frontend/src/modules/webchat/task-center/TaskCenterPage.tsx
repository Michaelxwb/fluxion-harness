/**
 * WebChat 定时任务中心页面（design frontend §3.2、§3.3 CMP-02、§3.5；TASK-027）。
 *
 * 容器职责：把同一份 typed client 注入到列表/详情/产物三个控制器，把路由（深链
 * `/chat/tasks/:scheduleId`）与用户动作接到控制器上。展示层不碰 HTTP、不读 URL。
 *
 * 读取时机：本组件只在任务 Tab 可见时挂载（TASK-026 的 Tab 壳保证），首屏读取是「用户切到
 * 任务 Tab」的直接结果；其余读取都由用户动作触发（翻页、打开执行、点下载、点刷新）——
 * 没有定时器、没有 SSE、切走即卸载。
 */

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { createActionsController, type ActionsPort } from './actionsState';
import { createPendingController, type PendingPort } from './pendingState';
import { PendingPanel } from './PendingPanel';
import { createTaskCenterClient } from '../services/taskCenterApi';
import type { TaskArtifact, TaskCenterClient } from '../types';
import {
  browserArtifactEnvironment,
  createArtifactDownloadController
} from './artifactDownload';
import { ScheduleDetailPanel } from './ScheduleDetailPanel';
import { ScheduleListPanel, type AgentOption } from './ScheduleListPanel';
import {
  createScheduleDetailController,
  createScheduleListController
} from './taskCenterState';

export interface WebchatTaskCenterPageProps {
  /** `/chat/tasks/:scheduleId` 的深链目标；列表态为 null。 */
  scheduleId?: string | null;
  /** 授权 Agent 集合（来自 WebChat 入口）；缺省不渲染过滤控件，读取仍是全部授权 Agent。 */
  agents?: readonly AgentOption[];
  /** `pending` = 待处理过滤视图（`/chat/tasks?view=pending`）；列表态为 null。 */
  view?: 'pending' | null;
}

export function WebchatTaskCenterPage(props: WebchatTaskCenterPageProps): ReactNode {
  const navigate = useNavigate();
  const location = useLocation();
  // `?view=pending` 是待处理过滤视图；列表/详情仍是同一条路由的两个投影。
  const pendingView = new URLSearchParams(location.search).get('view') === 'pending';
  const scheduleId = props.scheduleId ?? null;
  const clientRef = useRef<TaskCenterClient | null>(null);
  if (clientRef.current === null) clientRef.current = createTaskCenterClient();
  const client = clientRef.current;

  const list = useMemo(
    () => createScheduleListController({ port: client }),
    [client]
  );
  const detail = useMemo(
    () => createScheduleDetailController({ port: client }),
    [client]
  );
  const pending = useMemo(
    () => createPendingController({ port: client as PendingPort }),
    [client]
  );
  const actions = useMemo(
    () => createActionsController({ port: client as ActionsPort }),
    [client]
  );
  const pendingState = useSyncExternalStore(pending.subscribe, pending.snapshot);
  const actionsState = useSyncExternalStore(actions.subscribe, actions.snapshot);
  const downloads = useMemo(
    () =>
      createArtifactDownloadController({
        port: client,
        environment: browserArtifactEnvironment()
      }),
    [client]
  );

  const listState = useSyncExternalStore(list.subscribe, list.snapshot);
  const detailState = useSyncExternalStore(detail.subscribe, detail.snapshot);
  const downloadState = useSyncExternalStore(downloads.subscribe, downloads.snapshot);

  useEffect(
    () => () => {
      list.dispose();
      detail.dispose();
      downloads.dispose();
      pending.dispose();
      actions.dispose();
    },
    [list, detail, downloads, pending, actions]
  );

  // 待处理视图：与列表同一 Agent 范围，独立分页；切走时不轮询。
  useEffect(() => {
    if (!pendingView) return;
    void pending.load();
  }, [pendingView, pending]);

  // 列表态：进入/返回列表时读取当前筛选；详情态不读列表（避免两套事实互相覆盖）。
  useEffect(() => {
    if (scheduleId !== null) return;
    void list.load();
  }, [scheduleId, list]);

  // 详情态：深链或列表点击都走同一入口；换 Schedule 时丢弃旧目标的在途响应。
  const agentId = listState.filters.agentId;
  useEffect(() => {
    if (scheduleId === null) return;
    void detail.open(scheduleId, agentId);
  }, [scheduleId, agentId, detail]);

  const handleOpen = useCallback(
    (target: string) => {
      navigate(`/chat/tasks/${target}`);
    },
    [navigate]
  );

  const handleBack = useCallback(() => {
    navigate('/chat/tasks');
  }, [navigate]);

  const handleRefresh = useCallback(() => {
    if (scheduleId === null) {
      void list.refresh();
      return;
    }
    void detail.refresh();
  }, [scheduleId, list, detail]);

  const handleAgentChange = useCallback(
    (nextAgentId: string | null) => {
      void list.setFilter({ agentId: nextAgentId });
    },
    [list]
  );

  const handlePageChange = useCallback(
    (page: number) => {
      void list.setPage(page);
    },
    [list]
  );

  const handleFirePage = useCallback(
    (page: number) => {
      void detail.loadFires(page);
    },
    [detail]
  );

  const handleOpenExecution = useCallback(
    (executionId: string) => {
      // 换执行 = 换产物身份：先丢弃上一份下载状态与兜底释放 URL。
      downloads.reset();
      void detail.openExecution(executionId);
    },
    [detail, downloads]
  );

  const handleCloseExecution = useCallback(() => {
    downloads.reset();
    detail.closeExecution();
  }, [detail, downloads]);

  const handleDownloadArtifact = useCallback(
    (artifact: TaskArtifact) => {
      downloads.download(artifact);
    },
    [downloads]
  );

  const navigateToPending = useCallback(() => {
    navigate('/chat/tasks?view=pending');
  }, [navigate]);

  const handleRespond = useCallback(
    (interactionId: string, response: Record<string, unknown>, idempotencyKey: string) => {
      void pending.respond(interactionId, response, idempotencyKey);
    },
    [pending]
  );

  const handleResumeFromPending = useCallback(
    (target: string) => {
      void actions.run(target, 'resume', { idempotencyKey: `resume-${target}` });
    },
    [actions]
  );

  if (pendingView) {
    return (
      <PendingPanel
        state={pendingState}
        onRespond={handleRespond}
        onRefresh={() => void pending.refresh()}
        onPage={(page) => void pending.setPage(page)}
        onResume={handleResumeFromPending}
        onOpenSchedule={handleOpen}
      />
    );
  }

  if (scheduleId === null) {
    return (
      <ScheduleListPanel
        onOpenPending={() => navigate('/chat/tasks?view=pending')}
        pendingCount={listState.badges?.pending_human ?? 0}
        state={listState}
        agents={props.agents}
        onRefresh={handleRefresh}
        onOpen={handleOpen}
        onAgentChange={handleAgentChange}
        onPageChange={handlePageChange}
      />
    );
  }

  return (
    <ScheduleDetailPanel
      state={detailState}
      downloads={downloadState}
      onBack={handleBack}
      onRefresh={handleRefresh}
      onFirePage={handleFirePage}
      onOpenExecution={handleOpenExecution}
      onCloseExecution={handleCloseExecution}
      onDownloadArtifact={handleDownloadArtifact}
      actions={{
        state: actionsState,
        onAction: (
          action: 'pause' | 'resume' | 'cancel',
          scope?: 'FUTURE_ONLY' | 'ALL_ACTIVE'
        ) =>
          void actions.run(scheduleId as string, action, {
            scope,
            idempotencyKey: `${action}-${scheduleId}`
          }),
        onRecreate: () => navigate('/chat'),
        onEdit: () => {
          const name = detailState.detail?.name ?? '';
          navigate(`/chat?draft=修改任务 ${name}：`);
        }
      }}
      onOpenPending={navigateToPending}
    />
  );
}
