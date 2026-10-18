'use client';

import { createContext, useContext, useEffect, useState } from 'react';
import { initializeApp, getApps } from 'firebase/app';
import { getAuth, GoogleAuthProvider, inMemoryPersistence, setPersistence, signInWithPopup, signOut } from 'firebase/auth';

type Account = { uid: string; email: string | null };
type Config = { enabled: boolean; firebase: { apiKey: string; authDomain: string; projectId: string; appId: string } | null };
const HostedAuth = createContext<{ enabled: boolean; account: Account | null }>({ enabled: false, account: null });
export const useHostedAuth = () => useContext(HostedAuth);

function bindLocalIdentity(account: Account) {
  const previous = window.localStorage.getItem('conversation-evals-demo-user');
  if (previous !== account.uid) {
    for (const key of Object.keys(window.localStorage)) {
      if (key.startsWith('conversation-evals-')) window.localStorage.removeItem(key);
    }
  }
  window.localStorage.setItem('conversation-evals-demo-user', account.uid);
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    async function load() {
      const response = await fetch('/api/auth/config', { cache: 'no-store' });
      if (!response.ok) throw new Error('Login configuration is unavailable.');
      const value: Config = await response.json();
      if (!active) return;
      setConfig(value);
      document.documentElement.dataset.caeHosted = String(value.enabled);
      if (value.enabled) {
        const session = await fetch('/api/auth/me', { cache: 'no-store' });
        if (session.ok) {
          const user: Account = await session.json();
          if (!active) return;
          bindLocalIdentity(user); setAccount(user);
        } else if (session.status !== 401) {
          throw new Error('Could not check your session. Retry shortly.');
        }
      }
      if (active) setReady(true);
    }
    void load().catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  async function login() {
    if (!config?.firebase) return;
    setBusy(true); setError('');
    try {
      const app = getApps().find((item) => item.name === 'cae-login') || initializeApp(config.firebase, 'cae-login');
      const auth = getAuth(app);
      await setPersistence(auth, inMemoryPersistence);
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: 'select_account' });
      const credential = await signInWithPopup(auth, provider);
      try {
        const response = await fetch('/api/auth/session', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id_token: await credential.user.getIdToken() }) });
        if (!response.ok) throw new Error((await response.json()).detail || 'Could not start your session.');
        const user: Account = await response.json();
        bindLocalIdentity(user); setAccount(user);
      } finally { await signOut(auth); }
    } catch (err) { setError(err instanceof Error ? err.message : 'Google sign-in failed.'); }
    finally { setBusy(false); }
  }

  async function logout() {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/auth/session', { method: 'DELETE' });
      if (!response.ok) throw new Error('Could not sign out. Retry shortly.');
      setAccount(null);
      window.location.reload();
    } catch (err) { setError(err instanceof Error ? err.message : 'Sign-out failed.'); }
    finally { setBusy(false); }
  }

  if (!ready || (config?.enabled && !account)) return <main className="page-shell auth-shell">
    <section className="card settings-card">
      <p className="eyebrow">VoiceAI QA</p><h1>{ready ? 'Sign in to your workspace' : 'Loading…'}</h1>
      {ready ? <><p>Anyone with a Google account can sign in. Your tests, agents, results, and API key stay private to your account.</p>
        <p>Sample evaluations need no API key. AI features use only your own API key and are billed to your provider account.</p>
        <button className="primary-link" disabled={busy} onClick={() => void login()}>{busy ? 'Signing in…' : 'Continue with Google'}</button></> : null}
      {error ? <p role="alert">{error} <button onClick={() => window.location.reload()}>Retry</button></p> : null}
    </section>
  </main>;

  return <HostedAuth.Provider value={{ enabled: Boolean(config?.enabled), account }}>
    {config?.enabled ? <div className="auth-account"><span>{account?.email}</span><button disabled={busy} onClick={() => void logout()}>Sign out</button>{error ? <span role="alert">{error}</span> : null}</div> : null}
    {children}
  </HostedAuth.Provider>;
}
