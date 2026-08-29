/** 展示组件：Profile 编辑表单（props 只读，保存事件上抛）。 */
import { useState } from "react";

import { Button, Input, Typography } from "@douyinfe/semi-ui";

import type { UserProfile } from "../types/chat";

interface ProfileFormProps {
  readonly profile: UserProfile;
  readonly onSave: (profile: UserProfile) => void;
}

export function ProfileForm({ profile, onSave }: ProfileFormProps) {
  const [displayName, setDisplayName] = useState(profile.displayName);
  const [email, setEmail] = useState(profile.email ?? "");

  return (
    <section aria-label="个人资料" className="profile-form">
      <Typography.Title heading={5}>个人资料</Typography.Title>
      <div className="profile-field">
        <Typography.Text>昵称</Typography.Text>
        <Input aria-label="昵称" onChange={setDisplayName} value={displayName} />
      </div>
      <div className="profile-field">
        <Typography.Text>邮箱</Typography.Text>
        <Input aria-label="邮箱" onChange={setEmail} value={email} />
      </div>
      <Button
        onClick={() => onSave({ ...profile, displayName, email: email || undefined })}
        theme="solid"
        type="primary"
      >
        保存资料
      </Button>
    </section>
  );
}
