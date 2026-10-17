'use client';

import { useCallback, useEffect, useState } from 'react';
import { getDraftModelOptions, getSpecGenerationSettings, saveSpecGenerationSettings, type SpecGenerationSettings as Settings } from '@/lib/api';
import { useHostedAuth } from './AuthGate';

export function SpecGenerationSettings() {
  const { enabled: hosted } = useHostedAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [model, setModel] = useState('');
  const [ids, setIds] = useState<string[]>([]);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async (preserveSelection = false) => {
    setBusy(true);
    setError(null);
    try {
      const next = await getSpecGenerationSettings();
      setSettings(next);
      if (!preserveSelection) setModel(next.model || '');
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
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const refresh = () => { void load(true); };
    window.addEventListener('cae:credentials-changed', refresh);
    return () => window.removeEventListener('cae:credentials-changed', refresh);
  }, [load]);

  async function save(selection = model.trim() || null) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await saveSpecGenerationSettings(selection);
      setSettings(next);
      setModel(next.model || '');
      setLoaded(true);
      setMessage(next.available ? `Saved. Next draft will use ${next.effective_model}.` : hosted ? `Saved model selection: ${next.effective_model}. Add your API key above before generating drafts.` : `Saved model selection: ${next.effective_model}. Connect Codex or configure an OpenAI API key before generating drafts.`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save settings.'); }
    finally { setBusy(false); }
  }

  const changed = loaded && model !== (settings?.model || '');
  return <section id="draft-generation" className="card settings-card" aria-label="Draft generation settings" aria-busy={busy}>
    <header className="settings-card-header">
      <div><p className="eyebrow">Test design</p><h2>Draft-generation model</h2></div>
      <span className="settings-badge" data-tone={settings?.available ? 'success' : 'neutral'}>{settings ? settings.available ? 'Ready' : 'Needs connection' : error ? 'Unavailable' : 'Checking…'}</span>
    </header>
    <p className="settings-description">Creates draft behaviors and cases for your test sets. It does not change agent responses or evaluation judging.</p>
    {settings ? <dl className="settings-summary">
      <div><dt>{settings.available ? 'Active model' : 'Configured model'}</dt><dd><code>{settings.effective_model}</code></dd></div>
      <div><dt>Source</dt><dd>{settings.source === 'console' ? 'Saved selection' : 'Deployment default'}</dd></div>
    </dl> : null}
    <label className="settings-field">Draft model
      <select aria-label="Draft model choices" value={model && !ids.includes(model) ? '__custom__' : model} disabled={busy || !loaded} onChange={(event) => { setMessage(null); setModel(event.target.value === '__custom__' ? '__custom__' : event.target.value); }}>
        <option value="">Use deployment default{settings && !settings.model ? ` (${settings.effective_model})` : ''}</option>
        {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        <option value="__custom__">Custom model ID</option>
      </select>
    </label>
    {model && !ids.includes(model) ? <label className="settings-field">Custom model ID
      <input aria-label="Custom draft model ID" disabled={busy || !loaded} maxLength={160} placeholder="Enter a model supported by your account" value={model === '__custom__' ? '' : model} onChange={(event) => { setMessage(null); setModel(event.target.value || '__custom__'); }} />
    </label> : null}
    {changed ? <p className="settings-help settings-unsaved">Unsaved selection · the active model stays unchanged until you save.</p> : null}
    {settings && !settings.available ? <p className="settings-help">{hosted ? <>Add <a href="#agent-execution">your OpenAI API key</a> before generating drafts.</> : <>No generation provider is connected. <a href="#agent-execution">Connect OpenAI for agent execution</a>, or configure an API key on the server.</>}</p> : null}
    <div className="settings-actions">
      <button type="button" className="primary-link" disabled={busy || !loaded || model === '__custom__'} onClick={() => void save()}>{busy ? 'Working…' : 'Save draft model'}</button>
      <button type="button" className="secondary-link" disabled={busy} onClick={() => void load(loaded)}>Refresh model list</button>
      {!loaded && error ? <button type="button" className="secondary-link" disabled={busy} onClick={() => void save(null)}>Reset to deployment default</button> : null}
    </div>
    {message ? <p className="settings-feedback" role="status">{message}</p> : null}
    {error ? <p className="settings-feedback" role="alert">{error}</p> : null}
    <details className="settings-details">
      <summary>Model compatibility and provider details</summary>
      <p>Only text models supported by your active OpenAI provider are usable. Saving a selection does not verify access or make a model call.</p>
      {settings ? <p>Provider: <code>{settings.provider}</code>{settings.model && settings.model !== settings.effective_model ? <> · Mapped from <code>{settings.model}</code></> : null}.</p> : null}
      {hint ? <p>{hint}</p> : null}
    </details>
  </section>;
}
