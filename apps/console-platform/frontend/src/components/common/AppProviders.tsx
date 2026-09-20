import { ConfigProvider } from '@douyinfe/semi-ui';
import enUS from '@douyinfe/semi-ui/lib/es/locale/source/en_US';
import zhCN from '@douyinfe/semi-ui/lib/es/locale/source/zh_CN';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Semi built-in copy (Modal ok/cancel, Popconfirm, table empty state, ...) comes from the
 * ConfigProvider locale, so it must follow the UI language. A hardcoded locale would leave
 * Chinese built-in text on an English UI.
 */
const SEMI_LOCALES = {
  'zh-CN': zhCN,
  'en-US': enUS
} as const;

export type SemiLocaleName = keyof typeof SEMI_LOCALES;
export type SemiLocale = (typeof SEMI_LOCALES)[SemiLocaleName];

export function semiLocaleFor(language: string | undefined): SemiLocale {
  return language?.toLowerCase().startsWith('en') ? SEMI_LOCALES['en-US'] : SEMI_LOCALES['zh-CN'];
}

export function AppProviders({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation();
  const semiLocale = semiLocaleFor(i18n.resolvedLanguage ?? i18n.language);
  return <ConfigProvider locale={semiLocale}>{children}</ConfigProvider>;
}
