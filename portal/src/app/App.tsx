import { useEffect, useState } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { createMemoryRouter, Navigate, RouterProvider, type RouteObject } from 'react-router';
import { makeQueryClient } from '../api/hooks';
import { onApiConfig } from '../api/config';
import { Shell } from './Shell';
import { hashToPath, readHash, writeHash } from './hashSync';
import { PipelinesPage } from '../features/pipelines/PipelinesPage';
import { BuilderPage } from '../features/builder/BuilderPage';
import { ReviewQueuePage } from '../features/review/ReviewQueuePage';
import { ReviewWorkspace } from '../features/review/ReviewWorkspace';
import { RunsPage } from '../features/runs/RunsPage';
import { ModelsPage } from '../features/models/ModelsPage';
import { SkillsPage } from '../features/skills/SkillsPage';
import { McpPage } from '../features/mcp/McpPage';
import { WorkersPage } from '../features/workers/WorkersPage';
import { PoliciesPage } from '../features/policies/PoliciesPage';
import { ConnectionsPage } from '../features/connections/ConnectionsPage';
import { SettingsPage } from '../features/settings/SettingsPage';

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/pipelines" replace /> },
      { path: 'pipelines', element: <PipelinesPage /> },
      { path: 'builder/:id', element: <BuilderPage /> },
      { path: 'review', element: <ReviewQueuePage /> },
      { path: 'review/:id', element: <ReviewWorkspace /> },
      { path: 'runs', element: <RunsPage /> },
      { path: 'models', element: <ModelsPage /> },
      { path: 'skills', element: <SkillsPage /> },
      { path: 'tools', element: <McpPage /> },
      { path: 'workers', element: <WorkersPage /> },
      { path: 'policies', element: <PoliciesPage /> },
      { path: 'connections', element: <ConnectionsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <Navigate to="/pipelines" replace /> },
    ],
  },
];

export function createAppRouter(initial = hashToPath(readHash())) {
  const router = createMemoryRouter(routes, { initialEntries: [initial] });
  router.subscribe((s) => writeHash(s.location.pathname));
  const onHash = () => {
    const p = hashToPath(readHash());
    if (p !== router.state.location.pathname) void router.navigate(p);
  };
  try {
    window.addEventListener('hashchange', onHash);
    window.addEventListener('popstate', onHash);
  } catch {
    /* ignore */
  }
  return router;
}

export function App({ queryClient }: { queryClient?: QueryClient }) {
  const [qc] = useState(() => queryClient ?? makeQueryClient());
  const [router] = useState(() => createAppRouter());
  useEffect(() => onApiConfig(() => void qc.resetQueries()), [qc]);
  return (
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
