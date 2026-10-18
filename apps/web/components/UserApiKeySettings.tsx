'use client';

import { useEffect, useState } from 'react';

export function UserApiKeySettings() {
  const [configured, setConfigured] = useState(false);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    void fetch('/api/auth/openai-key', { cache: 'no-store' }).then(async (response) => {
      if (!response.ok) throw new Error('Could not check your API key.');
      setConfigured((await response.json()).configured);
    }).catch((err) => setError(err.message));
  }, []);

  async function update(remove = false) {
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch('/api/auth/openai-key', { method: remove ? 'DELETE' : 'PUT',
        headers: { 'Content-Type': 'application/json' }, ...(remove ? {} : { body: JSON.stringify({ api_key: key }) }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Could not update your API key.');
      setConfigured(result.configured); setKey('');
      window.dispatchEvent(new Event('cae:credentials-changed'));
      setMessage(remove ? 'Your key was removed. AI operations are disabled.' : 'Your key was saved encrypted. No model call was made.');
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not update your API key.'); }
    finally { setBusy(false); }
  }

  return <section id="agent-execution" className="card settings-card" aria-label="Your OpenAI API key">
    <header className="settings-card-header"><h2>Your OpenAI API key</h2><span className="settings-badge">{configured ? 'Key saved' : 'AI disabled'}</span></header>
    <p>Agent tests, AI test drafting, and optional ASSERT reviews use this key. Usage is billed to your OpenAI API account. Your ChatGPT subscription is separate.</p>
    <p>No shared service key or GPT account is used. Saving a key does not make a model call.</p>
    <form onSubmit={(event) => { event.preventDefault(); void update(); }}>
      <label className="settings-field">{configured ? 'Replace API key' : 'OpenAI API key'}<input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(event) => setKey(event.target.value)} placeholder="sk-…" required /></label>
      <div className="settings-actions"><button className="primary-link" disabled={busy || !key.trim()} type="submit">Save key</button>
        {configured ? <button className="secondary-link settings-danger" disabled={busy} type="button" onClick={() => void update(true)}>Remove key</button> : null}</div>
    </form>
    {message ? <p role="status">{message}</p> : null}{error ? <p role="alert">{error}</p> : null}
  </section>;
}
