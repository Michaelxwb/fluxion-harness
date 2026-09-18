import { useCallback, useEffect, useState } from 'react';

export type ThemeMode = 'dark' | 'light';

const THEME_KEY = 'muad.theme';

function readTheme(): ThemeMode {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : 'dark';
  } catch {
    return 'dark';
  }
}

export function useThemeMode(): { mode: ThemeMode; toggle(): void } {
  const [mode, setMode] = useState<ThemeMode>(readTheme);

  useEffect(() => {
    document.body.setAttribute('theme-mode', mode);
    document.documentElement.setAttribute('theme-mode', mode);
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((previous) => {
      const next: ThemeMode = previous === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        // 忽略持久化失败，仅本次会话生效
      }
      return next;
    });
  }, []);

  return { mode, toggle };
}
