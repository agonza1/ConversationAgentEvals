'use client';

import { useEffect, useState } from 'react';
import { getApiBase } from '@/lib/api';

type Status = {
  enabled: boolean; status: string; sharing?: boolean; pending?: boolean; message: string;
  active_profile_id?: string; judge_model?: string;
  profiles: Array<{ id: string; label: string }>;
};
type Model = { id: string; display_name: string };

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${getApiBase()}/api/product/providers/chatgpt/${path}`, body === undefined
    ? { cache: 'no-store' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CAE-Local-Control': '1' }, body: JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || 'ChatGPT account control failed.');
  return value as T;
}

export function ChatGPTJudgeSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(false);

  async function refresh() {
    const value = await request<Status>('status');
    setStatus(value);
    setModel(value.judge_model?.replace(/^chatgpt_plan\//, '') || '');
    setModels([]);
    return value;
  }

  useEffect(() => { void refresh().catch((err) => setError(String(err.message))); }, []);
  useEffect(() => {
    if (!connecting) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      void refresh().then((next) => {
        if (!next.pending || Date.now() - started > 600_000) setConnecting(false);
      }).catch((err) => { setError(String(err.message)); setConnecting(false); });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [connecting]);

  async function act(action: () => Promise<void>) {
    setBusy(true); setError(null); setMessage(null);
    try { await action(); } catch (err) { setError(err instanceof Error ? err.message : 'ChatGPT operation failed.'); }
    finally { setBusy(false); }
  }

  async function connect(profileId: string | null) {
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    await act(async () => {
      try {
        const result = await request<{ authorize_url: string }>('oauth/start', { profile_id: profileId });
        if (popup) popup.location.href = result.authorize_url;
        else window.location.assign(result.authorize_url);
        setConnecting(true);
        setMessage('Complete ChatGPT sign-in and authorize plan use, then return here and refresh.');
      } catch (err) { popup?.close(); throw err; }
    });
  }

  return <section className="card" aria-label="ChatGPT plan judge settings">
    <h2>Judge with your ChatGPT plan</h2>
    <p>Local installation only. Uses ASSERT&apos;s existing judge and review workflow without an API key. This connection does not sign you into CAE or grant access to ChatGPT conversations.</p>
    {status ? <p role="status">{status.message}</p> : null}
    {status?.enabled ? <>
      <label>ChatGPT account
        <select aria-label="ChatGPT account" value={status.active_profile_id || ''} disabled={busy || connecting}
          onChange={(event) => void act(async () => {
            await request('account', { profile_id: event.target.value }); await refresh();
          })}>
          <option value="" disabled>Select an account</option>
          {status.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
        </select>
      </label>
      <button type="button" className="primary-link" disabled={busy || connecting}
        onClick={() => void connect(status.active_profile_id || null)}>{connecting ? 'Waiting for ChatGPT…' : 'Continue with ChatGPT'}</button>{' '}
      <button type="button" className="secondary-link" disabled={busy || connecting}
        onClick={() => void connect(null)}>Add ChatGPT account</button>{' '}
      <button type="button" className="secondary-link" disabled={busy}
        onClick={() => void act(async () => { await refresh(); setConnecting(false); })}>Refresh connection</button>
      {status.status === 'connected' || status.status === 'expired' ? <button type="button" className="secondary-link" disabled={busy}
        onClick={() => void act(async () => { const next = await request<Status>('disconnect', {}); setStatus(next); setModels([]); setModel(''); setConnecting(false); })}>Sign out of selected ChatGPT account</button> : null}
      {status.sharing ? <>
        <p>Active judge: {status.judge_model || 'Deployment default (not using this ChatGPT connection)'}. Selecting a model below applies to both uploaded evidence and completed live runs.</p>
        <button type="button" className="secondary-link" disabled={busy || connecting}
          onClick={() => void act(async () => { const next = await request<{ models: Model[] }>('models'); setModels(next.models); })}>Load account models</button>
        <label>ChatGPT judge model
          <select aria-label="ChatGPT judge model" value={model} disabled={busy || !models.length} onChange={(event) => setModel(event.target.value)}>
            <option value="">Choose a model</option>
            {model && !models.some((item) => item.id === model) ? <option value={model}>{model} (refresh catalog)</option> : null}
            {models.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
          </select>
        </label>
        <button type="button" className="primary-link" disabled={busy || !models.some((item) => item.id === model)}
          onClick={() => void act(async () => { await request('judge-model', { model }); await refresh(); setMessage('Saved. New ASSERT reviews use your selected ChatGPT plan model. Reload Eval or the run analysis to refresh readiness.'); })}>Use ChatGPT model for ASSERT reviews</button>{' '}
      </> : null}
      {status.active_profile_id ? <button type="button" className="secondary-link" disabled={busy || connecting}
        onClick={() => void act(async () => { await request('judge-model', { model: null }); await refresh(); setMessage('Restored deployment judge settings. No model was called.'); })}>Restore deployment judge</button> : null}
      <p>Model discovery and saving do not run inference. Plan eligibility and usage limits still apply. Errors never switch automatically to paid API usage.</p>
    </> : null}
    {message ? <p role="status">{message}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
