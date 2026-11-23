import { Banner, Button, Modal, Skeleton, Tabs } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { Navigate, useSearchParams } from 'react-router-dom';

import { useAuth } from '../../auth/AuthContext';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { ErrorState } from '../../components/common/ErrorState';
import { EgressMonitor } from '../overview-dashboard/pages/EgressPage';
import { SettingsGroupPanel } from '../settings/components/SettingsGroupPanel';
import { SettingsHeader } from '../settings/components/SettingsHeader';
import { SettingsNotice } from '../settings/components/SettingsNotice';
import { useEgressSettings } from './EgressSettingsScope';

function EgressRules() {
  const { t } = useTranslation();
  const state = useEgressSettings();
  const group = state.groups.find((item) => item.key === 'egress');
  const reload = () => {
    if (!state.dirty) {
      state.reload();
      return;
    }
    Modal.confirm({ content: t('egress.rules.discard'), onOk: state.reload });
  };

  if (state.loading) {
    return <Skeleton active placeholder={<Skeleton.Paragraph rows={4} />} />;
  }
  if (state.failed || !group) {
    return <ErrorState description={t('settings.error.loadFailed')} onRetry={reload} />;
  }
  return (
    <>
      <SettingsHeader
        revision={state.revision} updatedBy={state.updatedBy} updatedAt={state.updatedAt}
        dirty={state.dirty} saving={state.saving}
        onSave={() => { void state.save(); }} onReset={state.reset}
      />
      {state.conflict && (
        <div className="settings-conflict" data-testid="settings-conflict">
          <Banner type="danger" closeIcon={null} description={t('settings.error.conflict')} />
          <Button onClick={reload} data-testid="settings-reload">{t('settings.error.reload')}</Button>
        </div>
      )}
      <SettingsNotice />
      <SettingsGroupPanel
        group={group} values={state.values} errors={state.errors}
        disabled={state.saving} onFieldChange={state.setField}
      />
    </>
  );
}

export function EgressManagementPage() {
  const { t } = useTranslation();
  const { account } = useAuth();
  const [params, setParams] = useSearchParams();
  const isAdmin = account?.role === 'ADMIN';
  const rulesOpen = params.get('tab') === 'rules';
  if (rulesOpen && !isAdmin) {
    return <Navigate to="/egress" replace />;
  }
  return (
    <div className="settings-page" data-testid="egress-management-page">
      <PageHeader title={t('nav.egress')} />
      <Tabs type="line" activeKey={rulesOpen ? 'rules' : 'monitor'}
        onChange={(key) => setParams(key === 'rules' ? { tab: 'rules' } : {})}>
        <Tabs.TabPane itemKey="monitor" tab={t('overview.views.egress')}>
          {!rulesOpen && <EgressMonitor />}
        </Tabs.TabPane>
        {isAdmin && (
          <Tabs.TabPane itemKey="rules" tab={t('egress.tabs.rules')}>
            {rulesOpen && <PageSection><EgressRules /></PageSection>}
          </Tabs.TabPane>
        )}
      </Tabs>
    </div>
  );
}
