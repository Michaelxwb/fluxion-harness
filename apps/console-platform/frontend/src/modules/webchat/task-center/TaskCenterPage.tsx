/**
 * WebChat 任务中心页面（design frontend §3.2、§3.3 CMP-01/CMP-02、§3.5；TASK-027/TASK-005）。
 *
 * 容器职责：把同一份 typed client 注入到定时任务/后台任务/产物控制器，把路由
 * （`/chat/tasks/:scheduleId`、`?view=pending`、`?view=background&taskId=`）与用户动作接到
 * 控制器上。展示层不碰 HTTP、不读 URL。
 *
 * 读取时机：本组件只在任务 Tab 可见时挂载（TASK-026 的 Tab 壳保证），首屏读取是「用户切到
 * 任务 Tab」的直接结果；其余读取都由用户动作触发（切分组、翻页、打开详情、子任务翻页、
 * 点下载、点刷新）——没有定时器、没有 SSE、切走即卸载。
 */

import { Typography } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { createActionsController, type ActionsPort } from './actionsState';
import { BackgroundTaskDetailPanel } from './BackgroundTaskDetailPanel';
import { BackgroundTaskListPanel } from './BackgroundTaskListPanel';
import {
  createBackgroundTaskDetailController,
  createBackgroundTaskListController
} from './backgroundState';
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
import { parseTaskCenterQuery } from './taskCenterRoute';

export interface WebchatTaskCenterPageProps {
  /** `/chat/tasks/:scheduleId` 的深链目标；列表态为 null。 */
  scheduleId?: string | null;
  /** 授权 Agent 集合（来自 WebChat 入口）；缺省不渲染过滤控件，读取仍是全部授权 Agent。 */
  agents?: readonly AgentOption[];
  /** `pending` = 待处理过滤视图（`/chat/tasks?view=pending`）；列表态为 null。 */
  view?: 'pending' | null;
  /** 回到对话：复用离开任务前的入口/会话位置，可额外合并 `draft` 等导航参数。 */
  onOpenConversation(extra?: Readonly<Record<string, string>>): void;
}

type TaskCenterGroup = 'schedule' | 'background';

/** 分组标题的静态词条键：保留字面量，i18n 死键扫描才能看见动态枚举。 */
const GROUP_TITLE_KEYS: Record<TaskCenterGroup, string> = {
  schedule: 'chat.taskCenter.group.schedule',
  background: 'chat.taskCenter.group.background'
};

/** 两个分组共用同一条 `/chat/tasks` 路由；分组只是页面内视图，不是新路由。 */
function TaskCenterGroups(props: {
  active: TaskCenterGroup;
  onSelect(group: TaskCenterGroup): void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="chat-task-center-groupbar"
      role="tablist"
      aria-label={t('chat.taskCenter.label')}
    >
      {(['schedule', 'background'] as const).map((group) => (
        <button
          key={group}
          type="button"
          role="tab"
          aria-selected={props.active === group}
          className={`chat-task-center-group${props.active === group ? ' is-active' : ''}`}
          data-testid={`chat-task-center-group-${group}`}
          onClick={() => props.onSelect(group)}
        >
          {t(GROUP_TITLE_KEYS[group])}
        </button>
      ))}
    </div>
  );
}

export function WebchatTaskCenterPage(props: WebchatTaskCenterPageProps): ReactNode {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const scheduleId = props.scheduleId ?? null;
  const route = parseTaskCenterQuery(location.search, scheduleId);
  const pendingView = route.pending || props.view === 'pending';
  const backgroundView = route.background;
  const backgroundTaskId = route.backgroundTaskId;
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
  const backgroundList = useMemo(
    () => createBackgroundTaskListController({ port: client }),
    [client]
  );
  const backgroundDetail = useMemo(
    () => createBackgroundTaskDetailController({ port: client }),
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
  const backgroundListState = useSyncExternalStore(backgroundList.subscribe, backgroundList.snapshot);
  const backgroundDetailState = useSyncExternalStore(
    backgroundDetail.subscribe,
    backgroundDetail.snapshot
  );
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
      backgroundList.dispose();
      backgroundDetail.dispose();
      downloads.dispose();
      pending.dispose();
      actions.dispose();
    },
    [list, detail, backgroundList, backgroundDetail, downloads, pending, actions]
  );

  // 待处理视图：与列表同一 Agent 范围，独立分页；切走时不轮询。
  useEffect(() => {
    if (!pendingView) return;
    void pending.load();
  }, [pendingView, pending]);

  // 列表态：进入/返回列表时读取当前筛选；详情态不读列表（避免两套事实互相覆盖）。
  useEffect(() => {
    if (pendingView || backgroundView || scheduleId !== null) return;
    void list.load();
  }, [pendingView, backgroundView, scheduleId, list]);

  // 后台列表：只在分组可见且没有详情目标时读取一次；翻页/过滤才再读。
  useEffect(() => {
    if (!backgroundView || backgroundTaskId !== null) return;
    void backgroundList.load();
  }, [backgroundView, backgroundTaskId, backgroundList]);

  // 后台详情：深链与列表点击走同一入口；换目标时丢弃旧目标的在途响应。
  useEffect(() => {
    if (!backgroundView || backgroundTaskId === null) return;
    void backgroundDetail.open(backgroundTaskId);
  }, [backgroundView, backgroundTaskId, backgroundDetail]);

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

  const handlePageSizeChange = useCallback(
    (pageSize: number) => {
      void list.setPageSize(pageSize);
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

  const handleSelectGroup = useCallback(
    (group: TaskCenterGroup) => {
      navigate(group === 'background' ? '/chat/tasks?view=background' : '/chat/tasks');
    },
    [navigate]
  );

  const handleBackgroundOpen = useCallback(
    (taskId: string) => {
      downloads.reset();
      navigate(`/chat/tasks?view=background&taskId=${encodeURIComponent(taskId)}`);
    },
    [downloads, navigate]
  );

  const handleBackgroundBack = useCallback(() => {
    downloads.reset();
    navigate('/chat/tasks?view=background');
  }, [downloads, navigate]);

  const handleBackgroundParent = useCallback(
    (parentId: string) => {
      downloads.reset();
      navigate(`/chat/tasks?view=background&taskId=${encodeURIComponent(parentId)}`);
    },
    [downloads, navigate]
  );

  const handleBackgroundRefresh = useCallback(() => {
    if (backgroundTaskId === null) {
      void backgroundList.refresh();
      return;
    }
    void backgroundDetail.refresh();
  }, [backgroundTaskId, backgroundList, backgroundDetail]);

  const handleBackgroundPage = useCallback(
    (page: number) => {
      void backgroundList.setPage(page);
    },
    [backgroundList]
  );

  const handleBackgroundPageSize = useCallback(
    (pageSize: number) => {
      void backgroundList.setPageSize(pageSize);
    },
    [backgroundList]
  );

  const handleBackgroundAgentChange = useCallback(
    (nextAgentId: string | null) => {
      void backgroundList.setFilter({ agentId: nextAgentId });
    },
    [backgroundList]
  );

  const handleBackgroundChildPage = useCallback(
    (page: number) => {
      void backgroundDetail.loadChildrenPage(page);
    },
    [backgroundDetail]
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

  if (backgroundView) {
    if (backgroundTaskId !== null) {
      return (
        <BackgroundTaskDetailPanel
          state={backgroundDetailState}
          downloads={downloadState}
          agents={props.agents}
          onBack={handleBackgroundBack}
          onBackToParent={handleBackgroundParent}
          onRefresh={handleBackgroundRefresh}
          onChildPage={handleBackgroundChildPage}
          onOpenChild={handleBackgroundOpen}
          onDownloadArtifact={handleDownloadArtifact}
        />
      );
    }
    return (
      <div className="chat-task-center-shell">
        <header className="chat-task-center-page-header">
          <Typography.Title heading={4}>{t('chat.taskCenter.label')}</Typography.Title>
        </header>
        <TaskCenterGroups active="background" onSelect={handleSelectGroup} />
        <BackgroundTaskListPanel
          state={backgroundListState}
          agents={props.agents}
          onOpen={handleBackgroundOpen}
          onPage={handleBackgroundPage}
          onPageSizeChange={handleBackgroundPageSize}
          onAgentChange={handleBackgroundAgentChange}
          onRefresh={handleBackgroundRefresh}
        />
      </div>
    );
  }

  if (scheduleId === null) {
    return (
      <div className="chat-task-center-shell">
        <header className="chat-task-center-page-header">
          <Typography.Title heading={4}>{t('chat.taskCenter.label')}</Typography.Title>
        </header>
        <TaskCenterGroups active="schedule" onSelect={handleSelectGroup} />
        <ScheduleListPanel
          onOpenPending={() => navigate('/chat/tasks?view=pending')}
          pendingCount={listState.badges?.pending_human ?? 0}
          state={listState}
          agents={props.agents}
          onRefresh={handleRefresh}
          onOpen={handleOpen}
          onAgentChange={handleAgentChange}
          onPageChange={handlePageChange}
          onPageSizeChange={handlePageSizeChange}
        />
      </div>
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
        onRecreate: () => props.onOpenConversation(),
        onEdit: () => {
          const name = detailState.detail?.name ?? '';
          props.onOpenConversation({ draft: `修改任务 ${name}：` });
        }
      }}
      onOpenPending={navigateToPending}
    />
  );
}
