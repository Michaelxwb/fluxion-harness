/**
 * X409 设置页（TASK-003 / remediation §15.1）：主题/语言/通知偏好
 * （UserPreference 契约，in-memory/localStorage 先行，后续对齐 Phase 2 preference 契约）。
 */
import { useEffect, useState } from "react";

import { Select, Switch, Typography } from "@douyinfe/semi-ui";

import type { UserPreference } from "../types/chat";

const PREFERENCE_KEY = "fluxion.user-preference";

const DEFAULT_PREFERENCE: UserPreference = {
  language: "zh-CN",
  notifications: false,
  theme: "system"
};

export function loadUserPreference(): UserPreference {
  try {
    const raw = window.localStorage.getItem(PREFERENCE_KEY);
    if (!raw) return DEFAULT_PREFERENCE;
    const parsed = JSON.parse(raw) as Partial<UserPreference>;
    return { ...DEFAULT_PREFERENCE, ...parsed };
  } catch {
    return DEFAULT_PREFERENCE;
  }
}

export function saveUserPreference(preference: UserPreference): void {
  try {
    window.localStorage.setItem(PREFERENCE_KEY, JSON.stringify(preference));
  } catch {
    // localStorage 不可用（隐私模式等）时仅本次会话生效
  }
}

export function SettingsPage() {
  const [preference, setPreference] = useState<UserPreference>(DEFAULT_PREFERENCE);

  useEffect(() => {
    setPreference(loadUserPreference());
  }, []);

  const update = (patch: Partial<UserPreference>): void => {
    const next = { ...preference, ...patch };
    setPreference(next);
    saveUserPreference(next);
  };

  return (
    <section className="settings-page" aria-label="设置">
      <Typography.Title heading={3}>设置</Typography.Title>
      <div className="settings-field">
        <Typography.Text>界面主题</Typography.Text>
        <Select
          aria-label="界面主题"
          onChange={(value) => update({ theme: value as UserPreference["theme"] })}
          optionList={[
            { label: "跟随系统", value: "system" },
            { label: "亮色", value: "light" },
            { label: "暗色", value: "dark" }
          ]}
          value={preference.theme}
        />
      </div>
      <div className="settings-field">
        <Typography.Text>界面语言</Typography.Text>
        <Select
          aria-label="界面语言"
          onChange={(value) => update({ language: value as UserPreference["language"] })}
          optionList={[
            { label: "简体中文", value: "zh-CN" },
            { label: "English", value: "en-US" }
          ]}
          value={preference.language}
        />
      </div>
      <div className="settings-field">
        <Typography.Text>通知偏好</Typography.Text>
        <Switch
          aria-label="通知偏好"
          checked={preference.notifications}
          onChange={(checked) => update({ notifications: checked })}
        />
      </div>
    </section>
  );
}
