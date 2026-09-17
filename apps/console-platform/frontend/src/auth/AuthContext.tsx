import { Spin } from '@douyinfe/semi-ui';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode
} from 'react';
import { Navigate } from 'react-router-dom';

import {
  fetchCurrentAccount,
  login as loginRequest,
  logout as logoutRequest,
  type ConsoleAccount,
  type ConsoleRole
} from '../api/auth';

interface AuthContextValue {
  account: ConsoleAccount | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<ConsoleAccount | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetchCurrentAccount()
      .then((value) => {
        if (active) setAccount(value);
      })
      .catch(() => {
        if (active) setAccount(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setAccount(await loginRequest(username, password));
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest();
    setAccount(null);
  }, []);

  const value = useMemo(
    () => ({ account, loading, login, logout }),
    [account, loading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}

function AuthLoading() {
  return <Spin style={{ display: 'block', margin: '20vh auto' }} size="large" />;
}

export function RequireAuth({ children }: { children: ReactElement }) {
  const { account, loading } = useAuth();
  if (loading) {
    return <AuthLoading />;
  }
  if (account === null) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

export function RequireRole({ role, children }: { role: ConsoleRole; children: ReactElement }) {
  const { account, loading } = useAuth();
  if (loading) {
    return <AuthLoading />;
  }
  if (account === null) {
    return <Navigate to="/login" replace />;
  }
  if (account.role !== role) {
    return <Navigate to="/" replace />;
  }
  return children;
}
