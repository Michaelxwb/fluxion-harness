import {
  IconArrowLeft,
  IconArrowRight,
  IconBranch,
  IconBriefcase,
  IconCalendar,
  IconClock,
  IconHistogram,
  IconLayers,
  IconPuzzle,
  IconSearch,
  IconServer,
  IconSetting,
  IconUserGroup
} from '@douyinfe/semi-icons';
import { Button, Dropdown, Layout, Nav, Tooltip } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { LocaleSwitch } from '../components/common/LocaleSwitch';
import { ThemeButton } from '../components/common/ThemeButton';
import { menuItems } from '../config/menu';
import { useThemeMode } from '../theme';

const { Sider, Header, Content } = Layout;

const MENU_ICONS: Record<string, JSX.Element> = {
  '/': <IconHistogram size="large" />,
  '/agents': <IconBriefcase size="large" />,
  '/skills': <IconPuzzle size="large" />,
  '/mcp': <IconServer size="large" />,
  '/models': <IconLayers size="large" />,
  '/users': <IconUserGroup size="large" />,
  '/platforms': <IconBranch size="large" />,
  '/tasks': <IconClock size="large" />,
  '/schedules': <IconCalendar size="large" />,
  '/audits': <IconSearch size="large" />,
  '/settings': <IconSetting size="large" />
};

function SidebarAccount(props: { collapsed: boolean; onToggle(): void }) {
  const { t } = useTranslation();
  const { account, logout } = useAuth();
  return (
    <div className="app-user">
      <Dropdown trigger="click" position="topLeft" render={
        <Dropdown.Menu>
          <Dropdown.Item onClick={() => { void logout(); }}>{t('auth.logout')}</Dropdown.Item>
        </Dropdown.Menu>
      }>
        <Button theme="borderless" type="tertiary" className="app-user-button"
          data-testid="account-menu" aria-label={account?.display_name ?? t('common.loading')}
          title={account?.display_name}>
          <span className="app-user-avatar">
            {(account?.display_name ?? '?').slice(0, 1).toUpperCase()}
          </span>
          <span className="app-user-name">{account?.display_name ?? t('common.loading')}</span>
          <span className="app-user-role" data-testid="account-role">
            {account === null ? '' : t(`auth.role.${account.role.toLowerCase()}`)}
          </span>
        </Button>
      </Dropdown>
      <SidebarToggle collapsed={props.collapsed} onToggle={props.onToggle} />
    </div>
  );
}

function SidebarToggle(props: { collapsed: boolean; onToggle(): void }) {
  const { t } = useTranslation();
  const label = t(props.collapsed ? 'app.sidebar.expand' : 'app.sidebar.collapse');
  return (
    <div className="app-sidebar-footer">
      <Tooltip content={label}>
        <Button theme="borderless" type="tertiary" className="app-sidebar-toggle"
          data-testid="sidebar-toggle" aria-label={label} aria-controls="app-navigation"
          aria-expanded={!props.collapsed} onClick={props.onToggle}
          icon={props.collapsed ? <IconArrowRight size="small" /> : <IconArrowLeft size="small" />} />
      </Tooltip>
    </div>
  );
}

function Sidebar() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { account } = useAuth();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('muad.sidebar.collapsed') === 'true');
  useEffect(() => { localStorage.setItem('muad.sidebar.collapsed', String(collapsed)); }, [collapsed]);
  const visibleItems = menuItems.filter((item) => !item.adminOnly || account?.role === 'ADMIN');
  return (
    <Sider className={`app-sider${collapsed ? ' app-sider-collapsed' : ''}`}
      style={{ width: collapsed ? 64 : 'var(--app-sider-width, 184px)' }}>
      <div className="app-brand">
        <span className="app-brand-mark" title={t('app.brand')}>
          {collapsed ? t('app.brand').slice(0, 1).toUpperCase() : t('app.brand')}
        </span>
        {!collapsed && <span className="app-brand-title">{t('app.brandSuffix')}</span>}
      </div>
      <Nav id="app-navigation" className="app-nav" isCollapsed={collapsed}
        selectedKeys={[location.pathname]} items={visibleItems.map((item) => ({
          itemKey: item.path, text: t(item.key), icon: MENU_ICONS[item.path]
        }))} onSelect={({ itemKey }) => navigate(String(itemKey))}
        style={{ maxWidth: '100%' }} />
      <SidebarAccount collapsed={collapsed} onToggle={() => setCollapsed((value) => !value)} />
    </Sider>
  );
}

export function AppLayout() {
  const theme = useThemeMode();
  return (
    <Layout className="app-shell">
      <Sidebar />
      <Layout>
        <Header className="app-topbar">
          <ThemeButton mode={theme.mode} onToggle={theme.toggle} />
          <LocaleSwitch />
        </Header>
        <Content className="app-content"><Outlet /></Content>
      </Layout>
    </Layout>
  );
}
