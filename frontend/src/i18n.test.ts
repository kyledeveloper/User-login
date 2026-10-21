import { describe, expect, it } from 'vitest';
import { localeKeysMatch, resolveLocale, translate } from './i18n';

describe('locale packs', () => {
  it('keeps English and Chinese keys synchronized', () => {
    expect(localeKeysMatch()).toBe(true);
  });
  it('selects the Chinese pack for Chinese browser locales', () => {
    expect(resolveLocale('zh-Hans-CN')).toBe('zh-CN');
    expect(translate('zh-CN', 'title')).toBe('你的账户');
  });
});
