/**
 * CMP-01：系统设置页容器（前端设计 §3.2/§3.3/§3.5/§3.6）。
 *
 * 取数、表单状态、保存、错误映射到字段、版本冲突处理都在这里（经 `usePlatformSettings`）。
 * UI 四态：loading（分组骨架） / empty（`revision=0` 提示使用平台内置默认） / error（只渲染
 * 错误态 + 重试，**不渲染任何值**，避免把"没读到"伪装成"当前值"） / success（分组表单 + 版本信息）。
 *
 * 布局是「左侧分组导航 + 右侧单分组面板」：一次只展开一个分组，页面纵向深度恒定（不随分组数
 * 增长）；版本历史用 SideSheet（详情形态）。本页不是列表页，不套用 RULE-ui-001 的列表页形态。
 * 保存失败的字段错误若落在非当前面板，自动切换到第一个出错分组（E-11 的错误必须可见）。
 */

import { Banner, Button, Popover, Skeleton } from '@douyinfe/semi-ui';
import { IconHelpCircle } from '@douyinfe/semi-icons';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../../components/common/ConsolePage';
import { ErrorState } from '../../../components/common/ErrorState';
import { ReadonlyNotesPanel } from '../components/ReadonlyNotesPanel';
import { SettingsGroupPanel } from '../components/SettingsGroupPanel';
import { SettingsHeader } from '../components/SettingsHeader';
import { SettingsNotice } from '../components/SettingsNotice';
import { VersionHistoryPanel } from '../components/VersionHistoryPanel';
import { usePlatformSettings } from '../hooks/usePlatformSettings';
import type { SettingsGroupMeta } from '../types';

function SettingsSkeleton() {
  return (
    <div className="settings-skeleton" data-testid="settings-loading">
      <Skeleton placeholder={<Skeleton.Title style={{ width: 160 }} />} loading />
      <Skeleton placeholder={<Skeleton.Paragraph rows={2} />} loading />
      <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading />
    </div>
  );
}

/** 左侧分组导航：一次一屏的关键。分组来自 API 元数据，页面不持有分组清单。 */
function SettingsGroupNav({
  groups,
  activeKey,
  onSelect
}: {
  groups: SettingsGroupMeta[];
  activeKey: string;
  onSelect(key: string): void;
}) {
  const { t } = useTranslation();
  return (
    <nav className="settings-nav" data-testid="settings-nav" aria-label={t('settings.title')}>
      {groups.map((group) => (
        <button
          key={group.key}
          type="button"
          className={
            group.key === activeKey
              ? 'settings-nav-item settings-nav-item-selected'
              : 'settings-nav-item'
          }
          data-testid={`settings-nav-${group.key}`}
          aria-current={group.key === activeKey || undefined}
          onClick={() => onSelect(group.key)}
        >
          {t(group.labelKey)}
        </button>
      ))}
    </nav>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const state = usePlatformSettings();

  // 单面板布局下，保存失败的字段错误必须可见：自动切到第一个出错字段所在的分组。
  useEffect(() => {
    const firstPath = Object.keys(state.errors)[0];
    if (!firstPath) {
      return;
    }
    const owner = state.groups.find((group) =>
      group.fields.some((field) => field.path === firstPath)
    );
    if (owner) {
      setActiveKey(owner.key);
    }
  }, [state.errors, state.groups]);

  const activeGroup =
    state.groups.find((group) => group.key === activeKey) ?? state.groups[0] ?? null;

  // 「不在此页管理的设置」收进页头帮助气泡：常驻卡片太占纵向空间（loading/failed 时不渲染入口）。
  const readonlyHelp = (
    <Popover
      trigger="click"
      position="bottomRight"
      content={<ReadonlyNotesPanel notes={state.readonlyNotes} />}
    >
      <Button
        theme="borderless"
        type="tertiary"
        icon={<IconHelpCircle />}
        data-testid="settings-readonly-trigger"
        aria-label={t('settings.readonly.title')}
      />
    </Popover>
  );

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
          emptyHint={state.revision === 0 ? t('settings.emptyDefault') : null}
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
        <div className="settings-layout">
          <SettingsGroupNav
            groups={state.groups}
            activeKey={activeGroup?.key ?? ''}
            onSelect={setActiveKey}
          />
          {activeGroup ? (
            <SettingsGroupPanel
              key={activeGroup.key}
              group={activeGroup}
              values={state.values}
              errors={state.errors}
              disabled={state.saving}
              onFieldChange={state.setField}
            />
          ) : null}
        </div>
      </>
    );
  })();

  return (
    <div className="settings-page" data-testid="settings-page">
      <PageHeader
        title={t('settings.title')}
        description={t('settings.subtitle')}
        extra={state.loading || state.failed ? undefined : readonlyHelp}
      />
      <PageSection>{body}</PageSection>
      <VersionHistoryPanel
        visible={historyOpen}
        currentRevision={state.revision}
        onClose={() => setHistoryOpen(false)}
        onRestored={state.reload}
      />
    </div>
  );
}
