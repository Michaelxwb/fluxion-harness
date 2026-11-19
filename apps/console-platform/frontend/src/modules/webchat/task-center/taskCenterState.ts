/**
 * 任务中心列表/详情的单一状态源（design frontend §3.3、§3.5、§3.6；TASK-027）。
 *
 * 三条硬约束：
 *
 * 1. **只在用户可见、用户触发时读取**：控制器自身不带定时器、不订阅 SSE；构造不发请求，
 *    `load/open/refresh/setPage/loadFires/openExecution` 是唯一的读取入口。Tab 隐藏时页面
 *    不挂载（TASK-026 的 Tab 壳保证），因此切走就没有后台轮询。
 * 2. **过期响应按 epoch 丢弃**：列表、详情、Fire 分页、Execution 各有自己的 epoch；筛选/翻页
 *    或切换 Schedule 之后迟到的旧响应不会覆盖新状态。
 * 3. **失败不清空有效内容**：刷新失败保留上一份列表/详情/Fire，只把错误码放在独立字段；
 *    徽标失败不阻塞列表。404 是显式状态，不静默回列表。
 *
 * 状态只存服务端事实的投影；不把同一份事实复制到多个组件（列表/详情各自一个控制器）。
 */

import { taskCenterErrorCode } from '../services/taskCenterApi';
import type {
  DstEvent,
  FireItem,
  HistoryRetention,
  ScheduleDetail,
  ExecutionDetail,
  ScheduleListItem,
  ScheduleListPage,
  ScheduleMode,
  ScheduleStatus,
  TaskCenterBadges,
  TaskCenterClient,
  WebchatPageQuery
} from '../types';

export type AsyncPhase = 'IDLE' | 'LOADING' | 'READY' | 'ERROR';

/** 与 Console 列表统一：默认每页 15 条，PaginationFooter 的选项也从 15 起。 */
export const TASK_CENTER_PAGE_SIZE = 15;
/** 运行列表固定每页 6 条：与右侧详情等高，并保持分页可读。 */
export const SCHEDULE_FIRE_PAGE_SIZE = 6;

/** 授权范围里的 Agent 过滤；`null` = 全部授权 Agent（服务端按授权集合取交集）。 */
export interface ScheduleFilters {
  agentId: string | null;
  status: ScheduleStatus | null;
  mode: ScheduleMode | null;
}

export interface ScheduleListState {
  phase: AsyncPhase;
  items: ScheduleListItem[];
  page: number;
  pageSize: number;
  total: number;
  errorCode: string | null;
  badges: TaskCenterBadges | null;
  badgesErrorCode: string | null;
  filters: ScheduleFilters;
}

export interface FireHistoryState {
  phase: AsyncPhase;
  items: FireItem[];
  dstEvents: DstEvent[];
  retention: HistoryRetention | null;
  page: number;
  pageSize: number;
  total: number;
  errorCode: string | null;
}

export type ExecutionPhase = 'IDLE' | 'LOADING' | 'READY' | 'ERROR';

export interface ExecutionState {
  phase: ExecutionPhase;
  executionId: string | null;
  detail: ExecutionDetail | null;
  errorCode: string | null;
}

export interface ScheduleDetailState {
  scheduleId: string | null;
  agentId: string | null;
  phase: AsyncPhase;
  detail: ScheduleDetail | null;
  errorCode: string | null;
  fires: FireHistoryState;
  execution: ExecutionState;
}

/** 页面只消费这两个端口的这些方法（任务中心 typed client 是唯一实现）。 */
export type ScheduleListPort = Pick<TaskCenterClient, 'listSchedules' | 'getBadges'>;
export type ScheduleDetailPort = Pick<
  TaskCenterClient,
  'getSchedule' | 'listFires' | 'getExecution'
>;

export interface ScheduleListController {
  snapshot(): ScheduleListState;
  subscribe(listener: (state: ScheduleListState) => void): () => void;
  /** 当前筛选与页码的显式读取（首屏由 Tab 挂载触发，后续都由用户动作触发）。 */
  load(): Promise<void>;
  /** 保留当前内容的刷新（失败不清空）。 */
  refresh(): Promise<void>;
  setFilter(patch: Partial<ScheduleFilters>): Promise<void>;
  setPage(page: number): Promise<void>;
  setPageSize(pageSize: number): Promise<void>;
  dispose(): void;
}

export interface ScheduleDetailController {
  snapshot(): ScheduleDetailState;
  subscribe(listener: (state: ScheduleDetailState) => void): () => void;
  open(scheduleId: string, agentId?: string | null): Promise<void>;
  refresh(): Promise<void>;
  loadFires(page: number): Promise<void>;
  openExecution(executionId: string): Promise<void>;
  closeExecution(): void;
  dispose(): void;
}

/** 空态：没有数据、没有读取，更不伪造计数。 */
export function emptyScheduleListState(): ScheduleListState {
  return {
    phase: 'IDLE',
    items: [],
    page: 1,
    pageSize: TASK_CENTER_PAGE_SIZE,
    total: 0,
    errorCode: null,
    badges: null,
    badgesErrorCode: null,
    filters: { agentId: null, status: null, mode: null }
  };
}

function emptyFireHistory(): FireHistoryState {
  return {
    phase: 'IDLE',
    items: [],
    dstEvents: [],
    retention: null,
    page: 1,
    pageSize: SCHEDULE_FIRE_PAGE_SIZE,
    total: 0,
    errorCode: null
  };
}

function emptyExecution(): ExecutionState {
  return { phase: 'IDLE', executionId: null, detail: null, errorCode: null };
}

export function emptyScheduleDetailState(): ScheduleDetailState {
  return {
    scheduleId: null,
    agentId: null,
    phase: 'IDLE',
    detail: null,
    errorCode: null,
    fires: emptyFireHistory(),
    execution: emptyExecution()
  };
}

/** 分页参数只发送调用方已知的值：运行列表首屏固定 6 条，翻页沿用服务端返回的 page_size。 */
function pageQuery(page: number, pageSize: number): WebchatPageQuery {
  const query: WebchatPageQuery = { page };
  if (pageSize > 0) query.page_size = pageSize;
  return query;
}

function agentQuery(agentId: string | null): { agent_id?: string } {
  return agentId === null ? {} : { agent_id: agentId };
}

/** 该错误码是否表示「不存在或无权访问」——页面显示 404 态，不静默回列表。 */
const NOT_FOUND_CODES: ReadonlySet<string> = new Set<string>([
  'SCHEDULE_NOT_FOUND',
  'COMMON_NOT_FOUND',
  'TASK_ARTIFACT_NOT_FOUND'
]);

export function isNotFoundCode(code: string | null): boolean {
  return code !== null && NOT_FOUND_CODES.has(code);
}

interface ListRuntime {
  port: ScheduleListPort;
  state: ScheduleListState;
  epoch: number;
  listeners: Set<(state: ScheduleListState) => void>;
}

function publishList(runtime: ListRuntime): void {
  const snapshot = runtime.state;
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function setListState(runtime: ListRuntime, patch: Partial<ScheduleListState>): void {
  runtime.state = { ...runtime.state, ...patch };
  publishList(runtime);
}

function applyListPage(runtime: ListRuntime, page: ScheduleListPage): void {
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
  // 有旧内容时保持 READY（轻量 loading），首屏才显示骨架。
  setListState(runtime, { phase: runtime.state.items.length > 0 ? 'READY' : 'LOADING' });

  const listTask = runtime.port
    .listSchedules({
      ...agentQuery(filters.agentId),
      ...(filters.status === null ? {} : { status: filters.status }),
      ...(filters.mode === null ? {} : { mode: filters.mode }),
      ...pageQuery(page, pageSize)
    })
    .then(
      (result) => {
        if (runtime.epoch !== epoch) return;
        applyListPage(runtime, result);
      },
      (error: unknown) => {
        if (runtime.epoch !== epoch) return;
        // 刷新失败保留有效旧内容；只有一条都没有时才是错误态。
        setListState(runtime, {
          phase: runtime.state.items.length > 0 ? 'READY' : 'ERROR',
          errorCode: taskCenterErrorCode(error)
        });
      }
    );

  // 徽标与列表同一 Agent 范围；徽标失败不阻塞列表，也不清空旧徽标。
  const badgeTask = runtime.port.getBadges(agentQuery(filters.agentId)).then(
    (badges) => {
      if (runtime.epoch !== epoch) return;
      setListState(runtime, { badges, badgesErrorCode: null });
    },
    (error: unknown) => {
      if (runtime.epoch !== epoch) return;
      setListState(runtime, { badgesErrorCode: taskCenterErrorCode(error) });
    }
  );

  await Promise.all([listTask, badgeTask]);
}

/** 列表控制器：单一筛选/分页状态，徽标与列表共享同一 Agent 范围。 */
export function createScheduleListController(deps: { port: ScheduleListPort }): ScheduleListController {
  const runtime: ListRuntime = {
    port: deps.port,
    state: emptyScheduleListState(),
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
  port: ScheduleDetailPort;
  state: ScheduleDetailState;
  detailEpoch: number;
  firesEpoch: number;
  executionEpoch: number;
  listeners: Set<(state: ScheduleDetailState) => void>;
}

function publishDetail(runtime: DetailRuntime): void {
  const snapshot = runtime.state;
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function setDetailState(
  runtime: DetailRuntime,
  patch: Partial<Omit<ScheduleDetailState, 'fires' | 'execution'>>
): void {
  runtime.state = { ...runtime.state, ...patch };
  publishDetail(runtime);
}

function setFiresState(runtime: DetailRuntime, patch: Partial<FireHistoryState>): void {
  runtime.state = { ...runtime.state, fires: { ...runtime.state.fires, ...patch } };
  publishDetail(runtime);
}

function setExecutionState(runtime: DetailRuntime, patch: Partial<ExecutionState>): void {
  runtime.state = { ...runtime.state, execution: { ...runtime.state.execution, ...patch } };
  publishDetail(runtime);
}

async function requestFires(runtime: DetailRuntime, page: number): Promise<void> {
  const scheduleId = runtime.state.scheduleId;
  if (scheduleId === null) return;
  const epoch = ++runtime.firesEpoch;
  const pageSize = runtime.state.fires.pageSize;
  setFiresState(runtime, {
    phase: runtime.state.fires.items.length > 0 ? 'READY' : 'LOADING',
    page
  });
  try {
    const result = await runtime.port.listFires(scheduleId, pageQuery(page, pageSize));
    if (runtime.firesEpoch !== epoch) return;
    setFiresState(runtime, {
      phase: 'READY',
      items: result.items,
      dstEvents: result.dst_events,
      retention: result.retention,
      page: result.page,
      pageSize: result.page_size,
      total: result.total,
      errorCode: null
    });
  } catch (error) {
    if (runtime.firesEpoch !== epoch) return;
    setFiresState(runtime, {
      phase: runtime.state.fires.items.length > 0 ? 'READY' : 'ERROR',
      errorCode: taskCenterErrorCode(error)
    });
  }
}

async function requestDetail(runtime: DetailRuntime): Promise<void> {
  const scheduleId = runtime.state.scheduleId;
  if (scheduleId === null) return;
  const epoch = ++runtime.detailEpoch;
  const agentId = runtime.state.agentId;
  setDetailState(runtime, {
    phase: runtime.state.detail === null ? 'LOADING' : 'READY',
    errorCode: null
  });
  try {
    const detail = await runtime.port.getSchedule(scheduleId, agentQuery(agentId));
    if (runtime.detailEpoch !== epoch) return;
    setDetailState(runtime, { phase: 'READY', detail, errorCode: null });
    // 详情到手后读第一页 Fire：这是用户打开该 Schedule 的直接结果，不是后台轮询。
    const previousExecutionId = runtime.state.execution.executionId;
    await requestFires(runtime, 1);
    const items = runtime.state.fires.items;
    const previous =
      previousExecutionId === null
        ? undefined
        : items.find((fire) => fire.execution_id === previousExecutionId);
    const selected = previous ?? items.find((fire) => fire.execution_id !== null);
    if (selected?.execution_id) {
      await requestExecution(runtime, selected.execution_id);
    }
  } catch (error) {
    if (runtime.detailEpoch !== epoch) return;
    setDetailState(runtime, {
      phase: runtime.state.detail === null ? 'ERROR' : 'READY',
      errorCode: taskCenterErrorCode(error)
    });
  }
}

async function requestExecution(runtime: DetailRuntime, executionId: string): Promise<void> {
  const epoch = ++runtime.executionEpoch;
  setExecutionState(runtime, {
    phase: 'LOADING',
    executionId,
    detail: null,
    errorCode: null
  });
  try {
    const detail = await runtime.port.getExecution(executionId);
    if (runtime.executionEpoch !== epoch) return;
    setExecutionState(runtime, { phase: 'READY', detail, errorCode: null });
  } catch (error) {
    if (runtime.executionEpoch !== epoch) return;
    setExecutionState(runtime, { phase: 'ERROR', errorCode: taskCenterErrorCode(error) });
  }
}

/** 详情控制器：详情、Fire 分页、Execution 各自独立 epoch，互不覆盖。 */
export function createScheduleDetailController(deps: {
  port: ScheduleDetailPort;
}): ScheduleDetailController {
  const runtime: DetailRuntime = {
    port: deps.port,
    state: emptyScheduleDetailState(),
    detailEpoch: 0,
    firesEpoch: 0,
    executionEpoch: 0,
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
    async open(scheduleId, agentId = null) {
      const id = scheduleId.trim();
      if (id === '') return;
      const sameSchedule = runtime.state.scheduleId === id;
      if (sameSchedule && runtime.state.detail !== null) return;
      // 换 Schedule 时丢弃旧目标的在途响应与旧内容；同一目标的重开保留旧内容。
      if (!sameSchedule) {
        runtime.detailEpoch += 1;
        runtime.firesEpoch += 1;
        runtime.executionEpoch += 1;
        runtime.state = {
          ...emptyScheduleDetailState(),
          scheduleId: id,
          agentId: agentId === null ? null : agentId.trim() === '' ? null : agentId.trim()
        };
        publishDetail(runtime);
      } else {
        runtime.state = { ...runtime.state, agentId };
        publishDetail(runtime);
      }
      await requestDetail(runtime);
    },
    async refresh() {
      if (runtime.state.scheduleId === null) return;
      await requestDetail(runtime);
      const executionId = runtime.state.execution.executionId;
      if (executionId !== null) await requestExecution(runtime, executionId);
    },
    async loadFires(page) {
      await requestFires(runtime, Math.max(1, Math.trunc(page)));
    },
    async openExecution(executionId) {
      const id = executionId.trim();
      if (id === '') return;
      await requestExecution(runtime, id);
    },
    closeExecution() {
      runtime.executionEpoch += 1;
      setExecutionState(runtime, emptyExecution());
    },
    dispose() {
      runtime.detailEpoch += 1;
      runtime.firesEpoch += 1;
      runtime.executionEpoch += 1;
      runtime.listeners.clear();
    }
  };
}
