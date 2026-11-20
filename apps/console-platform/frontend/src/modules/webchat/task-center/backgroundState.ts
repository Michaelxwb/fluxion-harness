/**
 * 后台任务列表/详情的单一状态源（design frontend §3.5；TASK-005）。
 *
 * 与定时任务控制器同一纪律：
 *
 * 1. 构造不发请求，也没有定时器/SSE；`load/open/refresh/setPage/loadChildrenPage`
 *    是唯一读取入口。
 * 2. 列表、详情、子任务分页各有 epoch；切换目标/翻页后迟到的旧响应不会覆盖新状态。
 * 3. 刷新失败保留上一份有效内容，只把稳定错误码放进独立字段；404 是显式状态。
 *
 * 子任务翻页复用执行详情路由，只带 `children_page`，本模块不另建 children 请求。
 */

import { taskCenterErrorCode } from '../services/taskCenterApi';
import type {
  ExecutionDetail,
  TaskCenterClient,
  TaskCenterTaskItem,
  TaskCenterTaskPage,
  WebchatPageQuery
} from '../types';
import { TASK_CENTER_PAGE_SIZE, type AsyncPhase } from './taskCenterState';

export interface BackgroundTaskFilters {
  agentId: string | null;
}

export interface BackgroundTaskListState {
  phase: AsyncPhase;
  items: TaskCenterTaskItem[];
  page: number;
  pageSize: number;
  total: number;
  errorCode: string | null;
  filters: BackgroundTaskFilters;
}

export interface BackgroundTaskDetailState {
  taskId: string | null;
  phase: AsyncPhase;
  detail: ExecutionDetail | null;
  errorCode: string | null;
  childrenPhase: AsyncPhase;
  childrenErrorCode: string | null;
  selectedChildId: string | null;
  childPhase: AsyncPhase;
  childDetail: ExecutionDetail | null;
  childErrorCode: string | null;
}

export type BackgroundTaskListPort = Pick<TaskCenterClient, 'listTasks'>;
export type BackgroundTaskDetailPort = Pick<TaskCenterClient, 'getExecution'>;

export interface BackgroundTaskListController {
  snapshot(): BackgroundTaskListState;
  subscribe(listener: (state: BackgroundTaskListState) => void): () => void;
  load(): Promise<void>;
  refresh(): Promise<void>;
  setFilter(patch: Partial<BackgroundTaskFilters>): Promise<void>;
  setPage(page: number): Promise<void>;
  setPageSize(pageSize: number): Promise<void>;
  dispose(): void;
}

export interface BackgroundTaskDetailController {
  snapshot(): BackgroundTaskDetailState;
  subscribe(listener: (state: BackgroundTaskDetailState) => void): () => void;
  open(taskId: string): Promise<void>;
  refresh(): Promise<void>;
  loadChildrenPage(page: number): Promise<void>;
  selectChild(childId: string): Promise<void>;
  clearChild(): void;
  retryChild(): Promise<void>;
  dispose(): void;
}

export function emptyBackgroundTaskListState(): BackgroundTaskListState {
  return {
    phase: 'IDLE',
    items: [],
    page: 1,
    pageSize: TASK_CENTER_PAGE_SIZE,
    total: 0,
    errorCode: null,
    filters: { agentId: null }
  };
}

export function emptyBackgroundTaskDetailState(): BackgroundTaskDetailState {
  return {
    taskId: null,
    phase: 'IDLE',
    detail: null,
    errorCode: null,
    childrenPhase: 'IDLE',
    childrenErrorCode: null,
    selectedChildId: null,
    childPhase: 'IDLE',
    childDetail: null,
    childErrorCode: null
  };
}

/** 分页参数只发送调用方已知的值：首屏不发 `page_size`（服务端默认是权威）。 */
function pageQuery(page: number, pageSize: number): WebchatPageQuery {
  const query: WebchatPageQuery = { page };
  if (pageSize > 0) query.page_size = pageSize;
  return query;
}

interface ListRuntime {
  port: BackgroundTaskListPort;
  state: BackgroundTaskListState;
  epoch: number;
  listeners: Set<(state: BackgroundTaskListState) => void>;
}

function publishList(runtime: ListRuntime): void {
  const snapshot = runtime.state;
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function setListState(runtime: ListRuntime, patch: Partial<BackgroundTaskListState>): void {
  runtime.state = { ...runtime.state, ...patch };
  publishList(runtime);
}

function applyTaskPage(runtime: ListRuntime, page: TaskCenterTaskPage): void {
  setListState(runtime, {
    phase: 'READY',
    items: page.items,
    page: page.page,
    pageSize: page.page_size,
    total: page.total,
    errorCode: null
  });
}

async function requestList(runtime: ListRuntime): Promise<void> {
  const epoch = ++runtime.epoch;
  const { filters, page, pageSize } = runtime.state;
  setListState(runtime, {
    phase: runtime.state.items.length > 0 ? 'READY' : 'LOADING'
  });
  try {
    const result = await runtime.port.listTasks({
      ...(filters.agentId === null ? {} : { agent_id: filters.agentId }),
      ...pageQuery(page, pageSize)
    });
    if (runtime.epoch !== epoch) return;
    applyTaskPage(runtime, result);
  } catch (error) {
    if (runtime.epoch !== epoch) return;
    setListState(runtime, {
      phase: runtime.state.items.length > 0 ? 'READY' : 'ERROR',
      errorCode: taskCenterErrorCode(error)
    });
  }
}

export function createBackgroundTaskListController(deps: {
  port: BackgroundTaskListPort;
}): BackgroundTaskListController {
  const runtime: ListRuntime = {
    port: deps.port,
    state: emptyBackgroundTaskListState(),
    epoch: 0,
    listeners: new Set()
  };
  return {
    snapshot: () => runtime.state,
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    load: () => requestList(runtime),
    refresh: () => requestList(runtime),
    async setFilter(patch) {
      runtime.state = {
        ...runtime.state,
        filters: { ...runtime.state.filters, ...patch },
        page: 1
      };
      publishList(runtime);
      await requestList(runtime);
    },
    async setPage(page) {
      const target = Math.max(1, Math.trunc(page));
      if (target === runtime.state.page && runtime.state.phase === 'READY') return;
      runtime.state = { ...runtime.state, page: target };
      publishList(runtime);
      await requestList(runtime);
    },
    async setPageSize(pageSize) {
      const target = Math.max(1, Math.trunc(pageSize));
      if (target === runtime.state.pageSize && runtime.state.phase === 'READY') return;
      runtime.state = { ...runtime.state, pageSize: target, page: 1 };
      publishList(runtime);
      await requestList(runtime);
    },
    dispose() {
      runtime.epoch += 1;
      runtime.listeners.clear();
    }
  };
}

interface DetailRuntime {
  port: BackgroundTaskDetailPort;
  state: BackgroundTaskDetailState;
  detailEpoch: number;
  childrenEpoch: number;
  childEpoch: number;
  listeners: Set<(state: BackgroundTaskDetailState) => void>;
}

function publishDetail(runtime: DetailRuntime): void {
  const snapshot = runtime.state;
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function setDetailState(
  runtime: DetailRuntime,
  patch: Partial<BackgroundTaskDetailState>
): void {
  runtime.state = { ...runtime.state, ...patch };
  publishDetail(runtime);
}

function childPageQuery(
  runtime: DetailRuntime,
  page: number
): { children_page: number; children_page_size?: number } {
  const size = runtime.state.detail?.children.page_size ?? 0;
  return size > 0
    ? { children_page: page, children_page_size: size }
    : { children_page: page };
}

async function requestDetail(runtime: DetailRuntime, childrenPage: number): Promise<void> {
  const taskId = runtime.state.taskId;
  if (taskId === null) return;
  const epoch = ++runtime.detailEpoch;
  setDetailState(runtime, {
    phase: runtime.state.detail === null ? 'LOADING' : 'READY',
    errorCode: null
  });
  try {
    const detail = await runtime.port.getExecution(
      taskId,
      childPageQuery(runtime, childrenPage)
    );
    if (runtime.detailEpoch !== epoch) return;
    setDetailState(runtime, {
      phase: 'READY',
      detail,
      errorCode: null,
      childrenPhase: 'READY',
      childrenErrorCode: null
    });
  } catch (error) {
    if (runtime.detailEpoch !== epoch) return;
    setDetailState(runtime, {
      phase: runtime.state.detail === null ? 'ERROR' : 'READY',
      errorCode: taskCenterErrorCode(error)
    });
  }
}

async function requestChildrenPage(runtime: DetailRuntime, page: number): Promise<void> {
  const taskId = runtime.state.taskId;
  if (taskId === null || runtime.state.detail === null) return;
  const epoch = ++runtime.childrenEpoch;
  const query = childPageQuery(runtime, page);
  setDetailState(runtime, { childrenPhase: 'LOADING', childrenErrorCode: null });
  try {
    const detail = await runtime.port.getExecution(taskId, query);
    if (runtime.childrenEpoch !== epoch) return;
    setDetailState(runtime, {
      detail,
      childrenPhase: 'READY',
      childrenErrorCode: null
    });
  } catch (error) {
    if (runtime.childrenEpoch !== epoch) return;
    setDetailState(runtime, {
      childrenPhase: 'ERROR',
      childrenErrorCode: taskCenterErrorCode(error)
    });
  }
}

async function requestChildExecution(runtime: DetailRuntime, childId: string): Promise<void> {
  const epoch = ++runtime.childEpoch;
  setDetailState(runtime, {
    selectedChildId: childId,
    childPhase: 'LOADING',
    childDetail: null,
    childErrorCode: null
  });
  try {
    const detail = await runtime.port.getExecution(childId);
    if (runtime.childEpoch !== epoch) return;
    setDetailState(runtime, {
      childPhase: 'READY',
      childDetail: detail,
      childErrorCode: null
    });
  } catch (error) {
    if (runtime.childEpoch !== epoch) return;
    setDetailState(runtime, {
      childPhase: 'ERROR',
      childErrorCode: taskCenterErrorCode(error)
    });
  }
}

function clearChildExecution(runtime: DetailRuntime): void {
  runtime.childEpoch += 1;
  setDetailState(runtime, {
    selectedChildId: null,
    childPhase: 'IDLE',
    childDetail: null,
    childErrorCode: null
  });
}

export function createBackgroundTaskDetailController(deps: {
  port: BackgroundTaskDetailPort;
}): BackgroundTaskDetailController {
  const runtime: DetailRuntime = {
    port: deps.port,
    state: emptyBackgroundTaskDetailState(),
    detailEpoch: 0,
    childrenEpoch: 0,
    childEpoch: 0,
    listeners: new Set()
  };
  return {
    snapshot: () => runtime.state,
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    async open(taskId) {
      const id = taskId.trim();
      if (id === '') return;
      const sameTask = runtime.state.taskId === id;
      if (sameTask && runtime.state.detail !== null) return;
      if (!sameTask) {
        runtime.detailEpoch += 1;
        runtime.childrenEpoch += 1;
        runtime.childEpoch += 1;
        runtime.state = { ...emptyBackgroundTaskDetailState(), taskId: id };
        publishDetail(runtime);
      }
      await requestDetail(runtime, 1);
    },
    async refresh() {
      if (runtime.state.taskId === null) return;
      const page = runtime.state.detail?.children.page ?? 1;
      const selectedChildId = runtime.state.selectedChildId;
      await requestDetail(runtime, page);
      if (selectedChildId !== null) await requestChildExecution(runtime, selectedChildId);
    },
    async loadChildrenPage(page) {
      await requestChildrenPage(runtime, Math.max(1, Math.trunc(page)));
      const selected = runtime.state.selectedChildId;
      if (
        selected !== null &&
        runtime.state.detail !== null &&
        !runtime.state.detail.children.items.some((item) => item.task_id === selected)
      ) {
        clearChildExecution(runtime);
      }
    },
    async selectChild(childId) {
      const id = childId.trim();
      if (id === '') return;
      if (id === runtime.state.selectedChildId && runtime.state.childDetail !== null) return;
      await requestChildExecution(runtime, id);
    },
    clearChild() {
      clearChildExecution(runtime);
    },
    async retryChild() {
      const childId = runtime.state.selectedChildId;
      if (childId !== null) await requestChildExecution(runtime, childId);
    },
    dispose() {
      runtime.detailEpoch += 1;
      runtime.childrenEpoch += 1;
      runtime.childEpoch += 1;
      runtime.listeners.clear();
    }
  };
}
