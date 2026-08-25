import { useEffect, useState } from "react";

export type ThemeMode = "light" | "dark";

const THEME_STORAGE_KEY = "fluxion-theme-mode";

function initialMode(): ThemeMode {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  if (saved === "light" || saved === "dark") {
    return saved;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useThemeMode(): { mode: ThemeMode; toggle: () => void } {
  const [mode, setMode] = useState<ThemeMode>(initialMode);

  useEffect(() => {
    document.body.setAttribute("theme-mode", mode);
    localStorage.setItem(THEME_STORAGE_KEY, mode);
  }, [mode]);

  return {
    mode,
    toggle: () => setMode((current) => (current === "light" ? "dark" : "light"))
  };
}
