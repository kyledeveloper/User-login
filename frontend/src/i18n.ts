import english from '../locales/en-US/common.json';
import chinese from '../locales/zh-CN/common.json';

export type LocaleKey = keyof typeof english;
export function resolveLocale(language: string) {
  return language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}
export function translate(language: string, key: LocaleKey) {
  return resolveLocale(language) === 'zh-CN' ? chinese[key] : english[key];
}
export function localeKeysMatch() {
  return Object.keys(english).sort().join('|') === Object.keys(chinese).sort().join('|');
}
