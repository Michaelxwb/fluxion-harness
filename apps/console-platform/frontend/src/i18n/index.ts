import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import enUS from '../locales/en-US.json';
import zhCN from '../locales/zh-CN.json';

export type SupportedLocale = 'zh-CN' | 'en-US';

const saved = localStorage.getItem('muad.locale') as SupportedLocale | null;
const browserLocale: SupportedLocale =
  navigator.language.toLowerCase().startsWith('en') ? 'en-US' : 'zh-CN';

void i18n.use(initReactI18next).init({
  resources: {
    'zh-CN': { translation: zhCN },
    'en-US': { translation: enUS }
  },
  lng: saved ?? browserLocale,
  fallbackLng: 'zh-CN',
  interpolation: { escapeValue: false }
});

export function changeLocale(locale: SupportedLocale): void {
  localStorage.setItem('muad.locale', locale);
  document.documentElement.lang = locale;
  void i18n.changeLanguage(locale);
}

export function currentLocale(): SupportedLocale {
  return (i18n.resolvedLanguage || i18n.language || 'zh-CN') as SupportedLocale;
}

export default i18n;
