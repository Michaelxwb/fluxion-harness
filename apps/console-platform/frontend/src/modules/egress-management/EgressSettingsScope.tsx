import { createContext, useContext, type ReactNode } from 'react';
import { useBeforeUnload, useLocation } from 'react-router-dom';

import { useAuth } from '../../auth/AuthContext';
import { usePlatformSettings, type PlatformSettingsState } from '../settings/hooks/usePlatformSettings';

const EgressSettingsContext = createContext<PlatformSettingsState | null>(null);

function AdminEgressSettings({ children }: { children: ReactNode }) {
  const { pathname, search } = useLocation();
  const rulesOpen = pathname === '/egress' && new URLSearchParams(search).get('tab') === 'rules';
  const state = usePlatformSettings(rulesOpen);
  useBeforeUnload((event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  return <EgressSettingsContext.Provider value={state}>{children}</EgressSettingsContext.Provider>;
}

export function EgressSettingsScope({ children }: { children: ReactNode }) {
  const { account } = useAuth();
  if (account?.role !== 'ADMIN') {
    return children;
  }
  return <AdminEgressSettings key={account.id}>{children}</AdminEgressSettings>;
}

export function useEgressSettings(): PlatformSettingsState {
  const state = useContext(EgressSettingsContext);
  if (state === null) {
    throw new Error('Egress settings require an administrator');
  }
  return state;
}
