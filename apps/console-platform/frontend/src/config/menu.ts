export interface MenuItem {
  path: string;
  key: string;
  adminOnly?: boolean;
}

export const menuItems: MenuItem[] = [
  { path: '/', key: 'nav.overview' },
  { path: '/agents', key: 'nav.agent' },
  { path: '/skills', key: 'nav.skill' },
  { path: '/mcp', key: 'nav.mcp' },
  { path: '/models', key: 'nav.model' },
  { path: '/users', key: 'nav.user', adminOnly: true },
  { path: '/platforms', key: 'nav.platform' },
  { path: '/tasks', key: 'nav.task' },
  { path: '/schedules', key: 'nav.schedule' },
  { path: '/audits', key: 'nav.audit' }
];
