import { Tabs } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

export type OverviewView = 'overview' | 'egress';

export function OverviewViewTabs({ activeKey }: { activeKey: OverviewView }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <Tabs
      type="line"
      activeKey={activeKey}
      className="overview-view-tabs"
      onChange={(key) => navigate(key === 'egress' ? '/overview/egress' : '/')}
    >
      <Tabs.TabPane itemKey="overview" tab={t('overview.views.operations')} />
      <Tabs.TabPane itemKey="egress" tab={t('overview.views.egress')} />
    </Tabs>
  );
}
