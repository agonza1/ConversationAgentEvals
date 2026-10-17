'use client';

import { useEffect, useState } from 'react';
import { getApiBase } from '@/lib/api';
import { ApiAwareLink } from './ApiAwareLink';
import styles from './SpeechProviderSettings.module.css';

type Selection = {
  stt_provider: 'rtc-asr' | 'deepgram' | 'cartesia';
  tts_provider: 'kokoro' | 'deepgram' | 'cartesia';
  stt_model: string; tts_model: string; tester_voice: string; target_voice: string;
  language: string; cartesia_version: string;
};
type Defaults = Record<string, Pick<Selection, 'stt_model' | 'tts_model' | 'tester_voice' | 'target_voice'>>;
type Settings = { selection: Selection; defaults: Defaults };

function parseSettings(data: Settings): Settings {
  const selection = data?.selection;
  if (!selection || !['rtc-asr', 'deepgram', 'cartesia'].includes(selection.stt_provider)
      || !['kokoro', 'deepgram', 'cartesia'].includes(selection.tts_provider)
      || ['stt_model', 'tts_model', 'tester_voice', 'target_voice', 'language', 'cartesia_version'].some((key) => typeof selection[key as keyof Selection] !== 'string')
      || !['local', 'deepgram', 'cartesia'].every((key) => data.defaults?.[key]
        && ['stt_model', 'tts_model', 'tester_voice', 'target_voice'].every((field) => typeof data.defaults[key][field as keyof Defaults[string]] === 'string'))) {
    throw new Error('Speech settings unavailable. Try reloading the saved settings.');
  }
  return data;
}

async function readSettings(signal?: AbortSignal): Promise<Settings> {
  const response = await fetch(`${getApiBase()}/api/execution/speech-settings`, { cache: 'no-store', signal });
  if (!response.ok) throw new Error('Speech settings unavailable. Try reloading the saved settings.');
  return parseSettings(await response.json());
}

function modelLabels(selection: Selection) {
  return {
    stt: selection.stt_provider === 'rtc-asr' ? 'Managed by rtc-asr service' : selection.stt_model,
    tts: selection.tts_provider === 'deepgram' ? 'Aura model per voice' : selection.tts_model,
  };
}

export function SpeechProviderSummary() {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let controller = new AbortController();
    function load() {
      controller.abort();
      const request = new AbortController();
      controller = request;
      void readSettings(request.signal).then((data) => {
        if (!request.signal.aborted) { setSelection(data.selection); setError(false); }
      }).catch(() => { if (!request.signal.aborted) { setSelection(null); setError(true); } });
    }
    load();
    window.addEventListener('focus', load);
    return () => { controller.abort(); window.removeEventListener('focus', load); };
  }, []);
  const models = selection ? modelLabels(selection) : null;
  return <section className={`card ${styles.summary}`} aria-label="Speech settings summary">
    <strong>Tester and built-in voice speech</strong>
    <p>{selection && models
      ? <>STT: {selection.stt_provider} · {models.stt} · TTS: {selection.tts_provider} · {models.tts}</>
      : error ? 'Speech settings unavailable.' : 'Loading speech settings…'}</p>
    {selection ? <small>Caller: {selection.tester_voice} · Agent: {selection.target_voice}</small> : null}
    <ApiAwareLink href="/settings#speech">Configure speech in Settings →</ApiAwareLink>
  </section>;
}

export function SpeechProviderSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setMessage('');
    void readSettings(controller.signal).then((data) => {
      if (!controller.signal.aborted) { setSettings(data); setSelection(data.selection); }
    }).catch((err) => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); };
  }, [revision]);
  const dirty = !!selection && JSON.stringify(selection) !== JSON.stringify(settings?.selection);
  function patch(update: Partial<Selection>) {
    setSelection((previous) => previous ? { ...previous, ...update } : previous);
    setMessage(''); setError('');
  }
  async function save() {
    setSaving(true); setError(''); setMessage('');
    try {
      const response = await fetch(`${getApiBase()}/api/execution/speech-settings`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(selection),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.map((item: { msg: string }) => item.msg).join(' ') : data.detail;
        throw new Error(detail || 'Could not save speech settings.');
      }
      const next = parseSettings(data);
      setSettings(next); setSelection(next.selection);
      setMessage('Saved for new voice runs. Existing runs and remote agents are unchanged.');
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save speech settings.'); }
    finally { setSaving(false); }
  }
  const busy = loading || saving;
  const models = settings ? modelLabels(settings.selection) : null;
  return <section id="speech" className="card settings-card" aria-label="Speech provider settings" aria-busy={busy}>
    <header className="settings-card-header">
      <div><p className="eyebrow">Voice runs</p><h2>Speech recognition and synthesis</h2></div>
      <span className="settings-badge" data-tone="neutral">{loading ? 'Loading…' : settings ? 'Configured' : 'Unavailable'}</span>
    </header>
    <p className="settings-description">Choose local or cloud speech for CAE. TTS controls the tester’s generated voice for built-in, Steven/Waylo, Daily and SignalWire tests, including follow-up turns. Switch between Local · Kokoro and Cloud · Deepgram here.</p>
    <p className="settings-help">Remote agents keep their own voices. STT and the agent voice below apply only to built-in voice runs; external-target audio capture still uses local rtc-asr. Uploaded audio fixtures are unchanged.</p>
    {settings && models ? <dl className="settings-summary">
      <div><dt>Configured STT</dt><dd>{settings.selection.stt_provider} · {models.stt}</dd></div>
      <div><dt>Configured TTS</dt><dd>{settings.selection.tts_provider} · {models.tts}</dd></div>
    </dl> : null}
    {selection && settings ? <fieldset disabled={busy} className={styles.fields}>
      <legend className={styles.legend}>Speech providers and models</legend>
      <label>Speech recognition (STT)<select aria-label="STT provider" value={selection.stt_provider}
        onChange={(event) => {
          const provider = event.target.value as Selection['stt_provider'];
          patch({ stt_provider: provider, stt_model: settings.defaults[provider === 'rtc-asr' ? 'local' : provider].stt_model });
        }}>
        <option value="rtc-asr">Local · rtc-asr</option><option value="deepgram">Deepgram</option><option value="cartesia">Cartesia</option>
      </select></label>
      <label>Speech synthesis (TTS)<select aria-label="TTS provider" value={selection.tts_provider}
        onChange={(event) => {
          const provider = event.target.value as Selection['tts_provider'];
          const values = settings.defaults[provider === 'kokoro' ? 'local' : provider];
          patch({ tts_provider: provider, tts_model: values.tts_model, tester_voice: values.tester_voice, target_voice: values.target_voice });
        }}>
        <option value="kokoro">Local · Kokoro</option><option value="deepgram">Cloud · Deepgram</option><option value="cartesia">Cloud · Cartesia</option>
      </select></label>
      <label>STT model<input aria-label="STT model" disabled={selection.stt_provider === 'rtc-asr'} value={selection.stt_provider === 'rtc-asr' ? 'Managed by rtc-asr service' : selection.stt_model} onChange={(event) => patch({ stt_model: event.target.value })} /></label>
      {selection.tts_provider !== 'deepgram' ? <label>TTS model<input aria-label="TTS model" value={selection.tts_model} onChange={(event) => patch({ tts_model: event.target.value })} /></label> : null}
      <label>{selection.tts_provider === 'deepgram' ? 'Caller Aura model / voice ID' : 'Caller voice ID'}<input aria-label="Caller voice ID" value={selection.tester_voice} onChange={(event) => patch({ tester_voice: event.target.value })} /></label>
      <label>{selection.tts_provider === 'deepgram' ? 'Agent Aura model / voice ID' : 'Agent voice ID'}<input aria-label="Agent voice ID" value={selection.target_voice} onChange={(event) => patch({ target_voice: event.target.value })} /></label>
      <label>Language<input aria-label="Speech language" value={selection.language} onChange={(event) => patch({ language: event.target.value })} /></label>
    </fieldset> : null}
    {selection?.stt_provider === 'rtc-asr' ? <p className="settings-help">The local STT model is selected by the rtc-asr service deployment. This console does not reload or switch that service’s model.</p> : null}
    {selection?.tts_provider === 'deepgram' ? <p className="settings-help">Deepgram’s Aura voice ID selects the TTS model; choose different caller and agent models/voices.</p> : null}
    {selection && (selection.stt_provider === 'deepgram' || selection.tts_provider === 'deepgram') ? <p className="settings-help">Deepgram requires <code>DEEPGRAM_API_KEY</code> in the Pipecat worker’s server environment.</p> : null}
    {selection && (selection.stt_provider === 'cartesia' || selection.tts_provider === 'cartesia') ? <p className="settings-help">Cartesia requires <code>CARTESIA_API_KEY</code> in the Pipecat worker’s server environment and voice IDs from your account’s voice library.</p> : null}
    <p className="settings-help">Saving does not verify provider access, credits or service health. <ApiAwareLink href="/runs">Check voice readiness in Run agent</ApiAwareLink> before queueing a voice run; saving here never starts a call. Cloud speech sends tester text to the selected provider and may incur usage charges.</p>
    {dirty ? <p className="settings-help settings-unsaved">Unsaved selection · new runs keep the saved settings until you save.</p> : null}
    <div className="settings-actions">
      <button type="button" className="primary-link" disabled={busy || !dirty} onClick={() => void save()}>{saving ? 'Saving…' : 'Save speech settings'}</button>
      <button type="button" className="secondary-link" disabled={busy} onClick={() => setRevision((value) => value + 1)}>Reload saved settings</button>
    </div>
    {message ? <p className="settings-feedback" role="status">{message}</p> : null}
    {error ? <p className="settings-feedback" role="alert">{error}</p> : null}
  </section>;
}
