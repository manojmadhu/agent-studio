export interface NavItem {
  key: string;
  label: string;
  icon: string;
  to: string;
  badge?: 'review';
}
export const NAV: { h: string; items: NavItem[] }[] = [
  {
    h: 'Operate',
    items: [
      { key: 'pipelines', label: 'Pipelines', icon: 'flow', to: '/pipelines' },
      { key: 'review', label: 'Review queue', icon: 'task--approved', to: '/review', badge: 'review' },
      { key: 'runs', label: 'Runs', icon: 'recently-viewed', to: '/runs' },
    ],
  },
  {
    h: 'Build',
    items: [
      { key: 'models', label: 'Models', icon: 'machine-learning-model', to: '/models' },
      { key: 'skills', label: 'Skills', icon: 'book', to: '/skills' },
      { key: 'tools', label: 'MCP servers', icon: 'plug', to: '/tools' },
      { key: 'workers', label: 'Workers', icon: 'chip', to: '/workers' },
    ],
  },
  {
    h: 'Govern',
    items: [
      { key: 'policies', label: 'Policies', icon: 'security', to: '/policies' },
      { key: 'connections', label: 'Connections', icon: 'password', to: '/connections' },
    ],
  },
  { h: '', items: [{ key: 'settings', label: 'Settings', icon: 'settings', to: '/settings' }] },
];
