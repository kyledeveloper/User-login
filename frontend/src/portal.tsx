import { useEffect, useState } from 'react';
import { translate } from './i18n';

type Account = { sub: string; email: string | null };

export function AccountApp() {
  const language = navigator.language;
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/me', { credentials: 'same-origin' })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => setAccount(data))
      .catch(() => setError(translate(language, 'serviceError')))
      .finally(() => setLoading(false));
  }, []);

  async function signOut() {
    const csrf = await fetch('/api/csrf', { credentials: 'same-origin' });
    if (!csrf.ok) return setError(translate(language, 'expiredError'));
    const { token } = await csrf.json();
    const response = await fetch('/auth/logout', {
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': token },
    });
    if (!response.ok) return setError(translate(language, 'logoutError'));
    const { logout_url: logoutUrl } = await response.json();
    window.location.assign(logoutUrl);
  }

  if (loading) return <main className="panel" aria-busy="true">{translate(language, 'loading')}</main>;
  return <main className="panel">
    <p className="eyebrow">{translate(language, 'eyebrow')}</p>
    <h1>{translate(language, 'title')}</h1>
    {error && <p role="alert" className="error">{error}</p>}
    {account ? <>
      <p className="intro">{translate(language, 'signedInIntro')}</p>
      <dl><dt>{translate(language, 'verifiedEmail')}</dt><dd>{account.email ?? translate(language, 'notShared')}</dd></dl>
      <button onClick={signOut}>{translate(language, 'signOut')}</button>
    </> : <>
      <p className="intro">{translate(language, 'anonymousIntro')}</p>
      <a className="button" href="/auth/login">{translate(language, 'signIn')}</a>
    </>}
    <p className="privacy">{translate(language, 'privacy')}</p>
  </main>;
}
