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
    try {
      await logoutRequest();
    } catch {
      // 退出失败（如 CSRF 令牌缺失 → 403）时保持登录态：会话仍有效，误清账号会伪装成已登出。
      // 失败文案已由 ApiClient 响应拦截器统一 Toast；这里吞掉 rejection 是为了不留下未捕获异常。
      return;
    }
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
