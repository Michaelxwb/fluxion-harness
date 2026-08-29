/**
 * X407 记忆与个人资料（TASK-010 / FEAT-P4-07）：Profile 编辑 + Personal Memory
 * 管理 + 自动学习开关容器。纠正/删除失败 → 字段级错误 + 重试，列表保持原状（E-01）。
 */
import { useEffect, useState } from "react";

import { Button, Skeleton, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../components/ErrorBanner";
import { MemoryList } from "../components/MemoryList";
import { ProfileForm } from "../components/ProfileForm";
import type {
  ChatApi,
  PersonalMemoryItem,
  UserProfile
} from "../types/chat";

interface MemoryProfilePageProps {
  readonly api: ChatApi;
}

interface PendingAction {
  readonly kind: "correct" | "delete";
  readonly memoryId: string;
  readonly corrected?: string;
}

export function MemoryProfilePage({ api }: MemoryProfilePageProps) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [items, setItems] = useState<readonly PersonalMemoryItem[] | null>(null);
  const [learningEnabled, setLearningEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  /** E-01：失败动作暂存，重试按钮重新执行。 */
  const [retryAction, setRetryAction] = useState<PendingAction | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void Promise.all([api.getProfile(), api.listMemory()])
      .then(([loadedProfile, memoryItems]) => {
        if (!active) return;
        setProfile(loadedProfile);
        setItems(memoryItems);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  // P2（review）：挂载时读取真实学习开关状态（不再首次恒显 true）；⛳ 端点未就绪
  // 或读取失败容错回退 true，不阻塞页面。
  useEffect(() => {
    let active = true;
    void api
      .getAutoLearn()
      .then((enabled) => {
        if (active) setLearningEnabled(enabled);
      })
      .catch(() => {
        if (active) setLearningEnabled(true);
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  async function saveProfile(next: UserProfile): Promise<void> {
    try {
      const saved = await api.updateProfile(next);
      setProfile(saved);
      setFeedback("资料已保存");
    } catch (cause) {
      setError(`资料保存失败：${cause instanceof Error ? cause.message : "未知错误"}`);
    }
  }

  async function correct(memoryId: string, corrected: string): Promise<void> {
    try {
      await api.correctMemory(memoryId, corrected);
      setItems(await api.listMemory());
      setFeedback("已纠正");
      setRetryAction(null);
    } catch (cause) {
      setRetryAction({ corrected, kind: "correct", memoryId });
      setFeedback(`纠正失败：${cause instanceof Error ? cause.message : "未知错误"}`);
    }
  }

  async function remove(memoryId: string): Promise<void> {
    try {
      await api.deleteMemory(memoryId);
      setItems(await api.listMemory());
      setFeedback("已删除");
      setRetryAction(null);
    } catch (cause) {
      setRetryAction({ kind: "delete", memoryId });
      setFeedback(`删除失败：${cause instanceof Error ? cause.message : "未知错误"}`);
    }
  }

  async function retry(): Promise<void> {
    if (retryAction === null) return;
    if (retryAction.kind === "correct") {
      await correct(retryAction.memoryId, retryAction.corrected ?? "");
    } else {
      await remove(retryAction.memoryId);
    }
  }

  async function toggleLearning(enabled: boolean): Promise<void> {
    try {
      await api.setAutoLearn(enabled);
      setLearningEnabled(enabled);
    } catch (cause) {
      setFeedback(`学习开关设置失败：${cause instanceof Error ? cause.message : "未知错误"}`);
    }
  }

  return (
    <section aria-label="记忆" className="memory-profile-page">
      <Typography.Title heading={3}>记忆</Typography.Title>
      {feedback !== null ? (
        <Typography.Text role="status">{feedback}</Typography.Text>
      ) : null}
      {retryAction !== null ? (
        <Button onClick={() => void retry()} size="small">
          重试
        </Button>
      ) : null}
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : profile === null || items === null ? (
        <div aria-label="记忆页加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <>
          <ProfileForm profile={profile} onSave={(next) => void saveProfile(next)} />
          <MemoryList
            items={items}
            learningEnabled={learningEnabled}
            onCorrect={(id, corrected) => void correct(id, corrected)}
            onDelete={(id) => void remove(id)}
            onToggleLearning={(enabled) => void toggleLearning(enabled)}
          />
        </>
      )}
    </section>
  );
}
