'use client';

import { useEffect, useState } from 'react';
import { getDraftModelOptions, getSpecGenerationSettings, saveSpecGenerationSettings, type SpecGenerationSettings as Settings } from '@/lib/api';

export function SpecGenerationSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [model, setModel] = useState('');
  const [ids, setIds] = useState<string[]>([]);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      const next = await getSpecGenerationSettings();
      setSettings(next);
      setModel(next.model || '');
      setLoaded(true);
      try {
        const options = await getDraftModelOptions();
        setIds(options.ids);
        setHint(options.message);
      } catch {
        setHint('Model discovery unavailable. You can enter an account-supported text model ID.');
      }
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not load settings.'); }
    finally { setBusy(false); }
  }

  useEffect(() => { void load(); }, []);

  async function save(selection = model.trim() || null) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await saveSpecGenerationSettings(selection);
      setSettings(next);
      setModel(next.model || '');
      setLoaded(true);
      setMessage(`Saved. Next draft will use ${next.effective_model}.`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save settings.'); }
    finally { setBusy(false); }
  }

  return <section className="card" aria-label="Draft generation settings">
    <h2>Draft-generation model</h2>
    <p>Used by both draft buttons in Evaluation design. Applies to this CAE deployment, not voice agents or evaluation judges.</p>
    <label>Model
      <select aria-label="Draft model choices" value={model && !ids.includes(model) ? '__custom__' : model} disabled={busy} onChange={(event) => { setMessage(null); setModel(event.target.value === '__custom__' ? '__custom__' : event.target.value); }}>
        <option value="">Use deployment default{settings ? ` (${settings.default_model})` : ''}</option>
        {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        <option value="__custom__">Custom model ID</option>
      </select>
    </label>
    {model && !ids.includes(model) ? <label>Custom model ID
      <input aria-label="Custom draft model ID" maxLength={160} value={model === '__custom__' ? '' : model} onChange={(event) => { setMessage(null); setModel(event.target.value || '__custom__'); }} />
    </label> : null}
    {settings ? <p>Active: {settings.effective_model} · {settings.provider} · {settings.source === 'console' ? 'Console setting' : 'Deployment default'}{settings.model && settings.model !== settings.effective_model ? ` (mapped from ${settings.model})` : ''}</p> : null}
    <p>Only OpenAI text models supported by your active provider are usable. Saving does not verify model access or make a model call.</p>
    {hint ? <p>{hint}</p> : null}
    <button type="button" className="primary-link" disabled={busy || !loaded || model === '__custom__'} onClick={() => void save()}>{busy ? 'Working…' : 'Save draft model'}</button>{' '}
    <button type="button" className="secondary-link" disabled={busy} onClick={() => void load()}>Refresh model list</button>
    {!loaded && error ? <button type="button" className="secondary-link" disabled={busy} onClick={() => void save(null)}>Reset to deployment default</button> : null}
    {message ? <p role="status">{message}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
