/**
 * CMP-01：系统设置页容器（前端设计 §3.2/§3.3/§3.5/§3.6）。
 *
 * 取数、表单状态、保存、错误映射到字段、版本冲突处理都在这里（经 `usePlatformSettings`）。
 * UI 四态：loading（分组骨架） / empty（`revision=0` 提示使用平台内置默认） / error（只渲染
 * 错误态 + 重试，**不渲染任何值**，避免把"没读到"伪装成"当前值"） / success（分组表单 + 版本信息）。
 *
 * 本页不是列表页：不套用「左上操作 + 右上筛选 + 列表 + 右下分页」形态（RULE-ui-001 的
 * 「RemoteTable 只约束列表页」）；版本历史用 SideSheet（详情形态）。
 */

import { Banner, Button, Skeleton } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { ErrorState } from '../../../components/common/ErrorState';
import { ReadonlyNotesPanel } from '../components/ReadonlyNotesPanel';
import { SettingsGroupPanel } from '../components/SettingsGroupPanel';
import { SettingsHeader } from '../components/SettingsHeader';
import { SettingsNotice } from '../components/SettingsNotice';
import { VersionHistoryPanel } from '../components/VersionHistoryPanel';
import { usePlatformSettings } from '../hooks/usePlatformSettings';

function SettingsSkeleton() {
  return (
    <div className="settings-skeleton" data-testid="settings-loading">
      <Skeleton placeholder={<Skeleton.Title style={{ width: 160 }} />} loading />
      <Skeleton placeholder={<Skeleton.Paragraph rows={2} />} loading />
      <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
    </div>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const [historyOpen, setHistoryOpen] = useState(false);
  const state = usePlatformSettings();

  const body = (() => {
    if (state.loading) {
      return <SettingsSkeleton />;
    }
    if (state.failed) {
      // E-13：错误态不渲染任何分组/字段值。
      return <ErrorState description={t('settings.error.loadFailed')} onRetry={state.reload} />;
    }
    return (
      <>
        <SettingsHeader
          revision={state.revision}
          updatedBy={state.updatedBy}
          updatedAt={state.updatedAt}
          dirty={state.dirty}
          saving={state.saving}
          onSave={() => { void state.save(); }}
          onReset={state.reset}
          onOpenHistory={() => setHistoryOpen(true)}
        />
        {state.conflict ? (
          <div className="settings-conflict" data-testid="settings-conflict">
            <Banner
              type="danger"
              closeIcon={null}
              description={t('settings.error.conflict')}
            />
            <Button
              theme="solid"
              data-testid="settings-reload"
              onClick={state.reload}
            >
              {t('settings.error.reload')}
            </Button>
          </div>
        ) : null}
        <SettingsNotice />
        {state.revision === 0 ? (
          <Banner
            type="warning"
            closeIcon={null}
            className="settings-empty"
            data-testid="settings-empty"
            description={t('settings.emptyDefault')}
          />
        ) : null}
        {state.groups.map((group) => (
          <SettingsGroupPanel
            key={group.key}
            group={group}
            values={state.values}
            errors={state.errors}
            disabled={state.saving}
            onFieldChange={state.setField}
          />
        ))}
      </>
    );
  })();

  return (
    <div className="settings-page" data-testid="settings-page">
      <PageHeader title={t('settings.title')} description={t('settings.subtitle')} />
      <PageSection>{body}</PageSection>
      {!state.loading && !state.failed ? (
        <PageSection>
          <ReadonlyNotesPanel notes={state.readonlyNotes} />
        </PageSection>
      ) : null}
      <VersionHistoryPanel
        visible={historyOpen}
        currentRevision={state.revision}
        onClose={() => setHistoryOpen(false)}
        onRestored={state.reload}
      />
    </div>
  );
}
