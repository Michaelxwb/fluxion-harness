import {
  IconBranch,
  IconBriefcase,
  IconCalendar,
  IconClock,
  IconHistogram,
  IconLayers,
  IconPuzzle,
  IconSearch,
  IconServer,
  IconUserGroup
} from '@douyinfe/semi-icons';
import { Button, Dropdown, Layout, Nav, Select } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { ThemeButton } from '../components/common/ThemeButton';
import { menuItems } from '../config/menu';
import { changeLocale, currentLocale, type SupportedLocale } from '../i18n';
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
  '/audits': <IconSearch size="large" />
};

export function AppLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { account, logout } = useAuth();
  const theme = useThemeMode();
  const visibleItems = menuItems.filter((item) => !item.adminOnly || account?.role === 'ADMIN');

  return (
    <Layout className="app-shell">
      <Sider className="app-sider" style={{ width: 'var(--app-sider-width, 208px)' }}>
        <div className="app-brand">
          <span className="app-brand-mark">{t('app.brand')}</span>
          <span className="app-brand-title">{t('app.brandSuffix')}</span>
        </div>
        <Nav
          className="app-nav"
          selectedKeys={[location.pathname]}
          items={visibleItems.map((item) => ({
            itemKey: item.path,
            text: t(item.key),
            icon: MENU_ICONS[item.path]
          }))}
          onSelect={({ itemKey }) => navigate(String(itemKey))}
          style={{ maxWidth: '100%' }}
        />
        <div className="app-user">
          <Dropdown
            trigger="click"
            position="topLeft"
            render={
              <Dropdown.Menu>
                <Dropdown.Item
                  onClick={() => {
                    void logout();
                  }}
                >
                  {t('auth.logout')}
                </Dropdown.Item>
              </Dropdown.Menu>
            }
          >
            <Button
              theme="borderless"
              type="tertiary"
              className="app-user-button"
              data-testid="account-menu"
            >
              <span className="app-user-avatar">
                {(account?.display_name ?? '?').slice(0, 1).toUpperCase()}
              </span>
              <span className="app-user-name">{account?.display_name ?? t('common.loading')}</span>
            </Button>
          </Dropdown>
        </div>
      </Sider>
      <Layout>
        <Header className="app-topbar">
          <ThemeButton mode={theme.mode} onToggle={theme.toggle} />
          <Select
            size="small"
            value={currentLocale()}
            style={{ width: 118 }}
            onChange={(value) => changeLocale(value as SupportedLocale)}
            optionList={[
              { value: 'zh-CN', label: t('common.language.zh') },
              { value: 'en-US', label: t('common.language.en') }
            ]}
          />
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
