/**
 * 任务产物下载（design frontend §3.4、§3.6、§3.9 API-25；TASK-027）。
 *
 * 产物是**不可变版本引用**：下载必须同时带 `artifact_id + version + checksum`，由 Owner
 * 解析具体版本；版本/校验不匹配是 `ARTIFACT_VERSION_CONFLICT`，绝不退回“最新版”。
 *
 * 三条硬约束：
 *
 * 1. **Blob URL 不泄漏**：`saveArtifact` 在 `finally` 里释放 URL——点击抛错也会释放；控制器
 *    `reset/dispose` 再兜底释放登记过的 URL（身份切换或组件卸载后不会留下悬空 URL）。
 * 2. **失败是失败**：下载出错只进入显式失败态并给出稳定错误码，不产生“已保存”的假象。
 * 3. **同一产物在途单飞**：重复点击不重复发请求。
 *
 * 传输只经注入的 typed 端口（任务中心 client 的 `downloadArtifact`），本模块不拼 URL。
 */

import { taskCenterErrorCode } from '../services/taskCenterApi';
import type { TaskArtifact } from '../types';

export interface ArtifactUrlScope {
  create(blob: Blob): string;
  revoke(url: string): void;
}

export interface ArtifactSaveTarget {
  href: string;
  download: string;
  click(): void;
}

export interface ArtifactDownloadEnvironment {
  scope: ArtifactUrlScope;
  /** 一次点击的保存目标；由环境提供，便于在无 DOM 的测试里注入。 */
  target(blobUrl: string, filename: string): ArtifactSaveTarget;
}

/** 浏览器默认环境：`URL.createObjectURL` + 隐藏 `<a download>`。 */
export function browserArtifactEnvironment(): ArtifactDownloadEnvironment {
  return {
    scope: {
      create: (blob) => URL.createObjectURL(blob),
      revoke: (url) => URL.revokeObjectURL(url)
    },
    target(blobUrl, filename) {
      const anchor = document.createElement('a');
      anchor.href = blobUrl;
      anchor.download = filename;
      return anchor;
    }
  };
}

export type ArtifactPhase = 'IDLE' | 'DOWNLOADING' | 'SAVED' | 'FAILED';

export interface ArtifactDownloadEntry {
  phase: ArtifactPhase;
  errorCode: string | null;
}

export const IDLE_ARTIFACT_DOWNLOAD: ArtifactDownloadEntry = { phase: 'IDLE', errorCode: null };

export interface ArtifactDownloadState {
  entries: Readonly<Record<string, ArtifactDownloadEntry>>;
}

export interface ArtifactDownloadPort {
  downloadArtifact(
    artifactId: string,
    version: string,
    checksum: string,
    options?: { signal?: AbortSignal }
  ): Promise<Blob>;
}

export interface ArtifactDownloadController {
  snapshot(): ArtifactDownloadState;
  subscribe(listener: (state: ArtifactDownloadState) => void): () => void;
  entry(artifactId: string): ArtifactDownloadEntry;
  download(artifact: TaskArtifact): void;
  /** 身份切换：丢弃全部条目并兜底释放登记的 URL。 */
  reset(): void;
  dispose(): void;
}

interface ArtifactRuntime {
  port: ArtifactDownloadPort;
  environment: ArtifactDownloadEnvironment;
  entries: Map<string, ArtifactDownloadEntry>;
  outstanding: Set<string>;
  generation: number;
  listeners: Set<(state: ArtifactDownloadState) => void>;
  cached: ArtifactDownloadState | null;
}

function snapshotOf(runtime: ArtifactRuntime): ArtifactDownloadState {
  if (runtime.cached !== null) return runtime.cached;
  const entries: Record<string, ArtifactDownloadEntry> = {};
  for (const [artifactId, entry] of runtime.entries) entries[artifactId] = entry;
  runtime.cached = { entries };
  return runtime.cached;
}

function publish(runtime: ArtifactRuntime): void {
  runtime.cached = null;
  const snapshot = snapshotOf(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function setEntry(
  runtime: ArtifactRuntime,
  artifactId: string,
  entry: ArtifactDownloadEntry
): void {
  runtime.entries.set(artifactId, entry);
  publish(runtime);
}

function releaseOutstanding(runtime: ArtifactRuntime): void {
  for (const url of runtime.outstanding) runtime.environment.scope.revoke(url);
  runtime.outstanding.clear();
}

/**
 * 保存一次产物字节：创建 URL → 触发下载 → 无论点击是否成功都释放 URL。
 * 任何时刻最多一个 URL 存在，且生命周期止于本次调用；`outstanding` 只用于 reset/dispose 兜底。
 */
function saveArtifact(runtime: ArtifactRuntime, blob: Blob, filename: string): void {
  const url = runtime.environment.scope.create(blob);
  runtime.outstanding.add(url);
  try {
    runtime.environment.target(url, filename).click();
  } finally {
    runtime.environment.scope.revoke(url);
    runtime.outstanding.delete(url);
  }
}

function download(runtime: ArtifactRuntime, artifact: TaskArtifact): void {
  const artifactId = artifact.artifact_id.trim();
  if (artifactId === '') return;
  const current = runtime.entries.get(artifactId) ?? IDLE_ARTIFACT_DOWNLOAD;
  if (current.phase === 'DOWNLOADING') return;
  setEntry(runtime, artifactId, { phase: 'DOWNLOADING', errorCode: null });
  const generation = runtime.generation;
  runtime.port.downloadArtifact(artifactId, artifact.version, artifact.checksum).then(
    (blob) => {
      if (runtime.generation !== generation) return;
      try {
        saveArtifact(runtime, blob, artifact.name);
      } catch (error) {
        // 保存阶段失败（浏览器拒绝触发下载）不是成功：URL 已在 finally 释放。
        setEntry(runtime, artifactId, {
          phase: 'FAILED',
          errorCode: 'ARTIFACT_SAVE_FAILED'
        });
        return;
      }
      setEntry(runtime, artifactId, { phase: 'SAVED', errorCode: null });
    },
    (error: unknown) => {
      if (runtime.generation !== generation) return;
      setEntry(runtime, artifactId, {
        phase: 'FAILED',
        errorCode: taskCenterErrorCode(error)
      });
    }
  );
}

/** 构造产物下载状态机：在途单飞、失败显式、URL 释放有兜底。 */
export function createArtifactDownloadController(deps: {
  port: ArtifactDownloadPort;
  environment?: ArtifactDownloadEnvironment;
}): ArtifactDownloadController {
  const runtime: ArtifactRuntime = {
    port: deps.port,
    environment: deps.environment ?? browserArtifactEnvironment(),
    entries: new Map(),
    outstanding: new Set(),
    generation: 0,
    listeners: new Set(),
    cached: null
  };
  return {
    snapshot: () => snapshotOf(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    entry: (artifactId) => runtime.entries.get(artifactId) ?? IDLE_ARTIFACT_DOWNLOAD,
    download: (artifact) => download(runtime, artifact),
    reset() {
      runtime.generation += 1;
      releaseOutstanding(runtime);
      runtime.entries.clear();
      publish(runtime);
    },
    dispose() {
      runtime.generation += 1;
      releaseOutstanding(runtime);
      runtime.entries.clear();
      runtime.listeners.clear();
      runtime.cached = null;
    }
  };
}

/** 稳定码 → 本地化词条键；未知码由组件追加原始 code（可读兜底，不静默）。 */
export const ARTIFACT_ERROR_KEYS: Record<string, string> = {
  TASK_ARTIFACT_NOT_FOUND: 'chat.taskCenter.artifact.error.notFound',
  COMMON_NOT_FOUND: 'chat.taskCenter.artifact.error.notFound',
  ARTIFACT_VERSION_CONFLICT: 'chat.taskCenter.artifact.error.versionConflict',
  FORBIDDEN: 'chat.taskCenter.artifact.error.forbidden',
  UNAUTHORIZED: 'chat.taskCenter.artifact.error.forbidden',
  NETWORK_ERROR: 'chat.taskCenter.artifact.error.network',
  ARTIFACT_SAVE_FAILED: 'chat.taskCenter.artifact.error.failed',
  ARTIFACT_ID_REQUIRED: 'chat.taskCenter.artifact.error.invalid',
  VERSION_REQUIRED: 'chat.taskCenter.artifact.error.invalid',
  CHECKSUM_REQUIRED: 'chat.taskCenter.artifact.error.invalid',
  UNKNOWN: 'chat.taskCenter.artifact.error.failed'
};

export function artifactErrorKey(code: string | null): string {
  if (code === null) return 'chat.taskCenter.artifact.error.failed';
  return ARTIFACT_ERROR_KEYS[code] ?? 'chat.taskCenter.artifact.error.failed';
}

/** 未知码是否需要在通用文案旁显示原始 code。 */
export function artifactErrorNeedsCode(code: string | null): boolean {
  return code !== null && ARTIFACT_ERROR_KEYS[code] === undefined;
}
