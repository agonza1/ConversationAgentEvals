'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
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
  const profileId = useRef<string | undefined>();

  const refresh = useCallback(async (preserveCatalog = false) => {
    const value = await request<Status>('status');
    setStatus(value);
    setModel(value.judge_model?.replace(/^chatgpt_plan\//, '') || '');
    if (!preserveCatalog || value.active_profile_id !== profileId.current || !value.sharing) setModels([]);
    profileId.current = value.active_profile_id;
    return value;
  }, []);

  useEffect(() => { void refresh().catch((err) => setError(String(err.message))); }, [refresh]);
  useEffect(() => {
    if (!connecting) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      void refresh().then((next) => {
        if (!next.pending || Date.now() - started > 600_000) setConnecting(false);
      }).catch((err) => { setError(String(err.message)); setConnecting(false); });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [connecting, refresh]);

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

  const connectionLabel = connecting ? 'Connecting…' : !status ? error ? 'Unavailable' : 'Checking…'
    : !status.enabled ? 'Not enabled' : status.status === 'expired' ? 'Expired'
      : status.sharing ? 'Authorized' : status.status === 'connected' ? 'Authorization needed' : 'Not connected';
  const activeModel = status?.judge_model?.replace(/^chatgpt_plan\//, '');
  return <section id="evaluation-judge" className="card settings-card" aria-label="ChatGPT plan judge settings" aria-busy={busy || connecting}>
    <header className="settings-card-header">
      <div><p className="eyebrow">Second-opinion reviews · local only</p><h2>Judge with your ChatGPT plan</h2></div>
      <span className="settings-badge" data-tone={status?.status === 'expired' ? 'warning' : status?.sharing ? 'success' : 'neutral'}>{connectionLabel}</span>
    </header>
    <p className="settings-description">Use your ChatGPT plan for ASSERT reviews of uploaded conversations and completed agent runs, without an API key.</p>
    {status ? <p className="settings-help">{status.message}</p> : null}
    {status?.status === 'expired' ? <p className="settings-help">The saved sign-in session has expired. Reconnect if model discovery or reviews fail.</p> : null}
    {status?.enabled ? <>
      {status.profiles.length ? <label className="settings-field">ChatGPT account
        <select aria-label="ChatGPT account" value={status.active_profile_id || ''} disabled={busy || connecting}
          onChange={(event) => void act(async () => {
            await request('account', { profile_id: event.target.value }); await refresh();
          })}>
          <option value="" disabled>Select an account</option>
          {status.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
        </select>
      </label> : null}
      <div className="settings-actions">
        {!status.sharing || status.status === 'expired' ? <button type="button" className="primary-link" disabled={busy || connecting}
          onClick={() => void connect(status.active_profile_id || null)}>{connecting ? 'Waiting for ChatGPT…' : 'Continue with ChatGPT'}</button> : null}
        <button type="button" className="secondary-link" disabled={busy}
          onClick={() => void act(async () => { await refresh(); setConnecting(false); })}>Refresh connection</button>
      </div>
      {status.sharing ? <>
        <div className="settings-model-section">
          <h3>Review model</h3>
          <dl className="settings-summary">
            <div><dt>Active judge</dt><dd>{activeModel && activeModel !== 'select-model' ? <code>{activeModel}</code> : activeModel === 'select-model' ? 'Choose a model to enable reviews' : 'Deployment default (not using this ChatGPT connection)'}</dd></div>
          </dl>
          <label className="settings-field">ChatGPT judge model
          <select aria-label="ChatGPT judge model" value={model} disabled={busy || !models.length} onChange={(event) => setModel(event.target.value)}>
            <option value="">Choose a model</option>
            {model && !models.some((item) => item.id === model) ? <option value={model}>{model} (refresh catalog)</option> : null}
            {models.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
          </select>
          </label>
          {!models.length ? <p className="settings-help">Load the models available to this account before choosing one.</p> : null}
          <div className="settings-actions">
            <button type="button" className={models.length ? 'primary-link' : 'secondary-link'} disabled={busy || !models.some((item) => item.id === model)}
              onClick={() => void act(async () => { await request('judge-model', { model }); await refresh(true); setMessage('Saved. New ASSERT reviews use this ChatGPT model. Reload an evaluation or run analysis to refresh readiness.'); })}>Save judge model</button>
            <button type="button" className={models.length ? 'secondary-link' : 'primary-link'} disabled={busy || connecting}
              onClick={() => void act(async () => { const next = await request<{ models: Model[] }>('models'); setModels(next.models); })}>Load account models</button>
          </div>
        </div>
      </> : null}
      <details className="settings-details">
        <summary>Account options</summary>
        <div className="settings-actions">
          <button type="button" className="secondary-link" disabled={busy || connecting}
            onClick={() => void connect(null)}>Add ChatGPT account</button>
          {status.sharing && status.status !== 'expired' ? <button type="button" className="secondary-link" disabled={busy || connecting}
            onClick={() => void connect(status.active_profile_id || null)}>Continue with ChatGPT</button> : null}
          {status.status === 'connected' || status.status === 'expired' ? <button type="button" className="secondary-link settings-danger" disabled={busy}
            onClick={() => void act(async () => { const next = await request<Status>('disconnect', {}); setStatus(next); setModels([]); setModel(''); setConnecting(false); })}>Sign out of selected ChatGPT account</button> : null}
          {status.active_profile_id ? <button type="button" className="secondary-link" disabled={busy || connecting}
            onClick={() => void act(async () => { await request('judge-model', { model: null }); await refresh(); setMessage('Restored deployment judge settings. No model was called.'); })}>Restore deployment judge</button> : null}
        </div>
      </details>
    </> : null}
    {(!status || !status.enabled) ? <div className="settings-actions"><button type="button" className="secondary-link" disabled={busy} onClick={() => void act(async () => { await refresh(); })}>Refresh connection</button></div> : null}
    {message ? <p className="settings-feedback" role="status">{message}</p> : null}
    {error ? <p className="settings-feedback" role="alert">{error}</p> : null}
    <p className="settings-help">Plan eligibility and usage limits apply. Errors never switch automatically to paid API usage.</p>
    <details className="settings-details">
      <summary>Privacy and local setup</summary>
      <p>This connection does not sign you into CAE or grant access to ChatGPT conversations. Model discovery and saving do not run inference.</p>
      <p>Available only on a loopback-only local installation with <code>CHATGPT_PLAN_LOCAL_ENABLED=1</code>.</p>
    </details>
  </section>;
}
