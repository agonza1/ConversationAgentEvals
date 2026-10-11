'use client';

import { useEffect, useState } from 'react';
import { getApiBase } from '@/lib/api';
import styles from './SpeechProviderSettings.module.css';

type Selection = {
  stt_provider: 'rtc-asr' | 'deepgram' | 'cartesia';
  tts_provider: 'kokoro' | 'deepgram' | 'cartesia';
  stt_model: string; tts_model: string; tester_voice: string; target_voice: string;
  language: string; cartesia_version: string;
};
type Defaults = Record<string, Pick<Selection, 'stt_model' | 'tts_model' | 'tester_voice' | 'target_voice'>>;

export function SpeechProviderSettings({ onSaved }: { onSaved: () => void }) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [defaults, setDefaults] = useState<Defaults>({});
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let active = true;
    fetch(`${getApiBase()}/api/execution/speech-settings`, { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('Speech settings unavailable.');
        return response.json();
      }).then((data) => { if (active) { setSelection(data.selection); setDefaults(data.defaults); } })
      .catch((error) => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, []);

  function patch(update: Partial<Selection>) {
    setSelection((previous) => previous ? { ...previous, ...update } : previous);
    setDirty(true); setMessage('Apply these settings before queueing a new run.');
  }

  async function save() {
    setSaving(true);
    try {
      const response = await fetch(`${getApiBase()}/api/execution/speech-settings`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(selection),
      });
      if (!response.ok) {
        const data = await response.json();
        const detail = Array.isArray(data.detail) ? data.detail.map((item: { msg: string }) => item.msg).join(' ') : data.detail;
        throw new Error(detail || 'Could not save speech settings.');
      }
      setDirty(false); setMessage('Saved for new built-in voice runs. Readiness refreshed.'); onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not save speech settings.'); }
    finally { setSaving(false); }
  }

  return <details className="card" style={{ padding: 16 }}>
    <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Speech providers · STT / TTS</summary>
    <p style={{ color: 'var(--muted)' }}>Configure the built-in caller and reference voice agent. New runs use these deployment-wide settings.</p>
    {selection ? <>
      <div className={styles.fields}>
        <label>Speech recognition (STT)<select aria-label="STT provider" value={selection.stt_provider}
          onChange={(event) => {
            const provider = event.target.value as Selection['stt_provider'];
            patch({ stt_provider: provider, stt_model: defaults[provider === 'rtc-asr' ? 'local' : provider].stt_model });
          }}>
          <option value="rtc-asr">Local · rtc-asr</option><option value="deepgram">Deepgram</option><option value="cartesia">Cartesia</option>
        </select></label>
        <label>Speech synthesis (TTS)<select aria-label="TTS provider" value={selection.tts_provider}
          onChange={(event) => {
            const provider = event.target.value as Selection['tts_provider'];
            const values = defaults[provider === 'kokoro' ? 'local' : provider];
            patch({ tts_provider: provider, tts_model: values.tts_model, tester_voice: values.tester_voice, target_voice: values.target_voice });
          }}>
          <option value="kokoro">Local · Kokoro</option><option value="deepgram">Deepgram</option><option value="cartesia">Cartesia</option>
        </select></label>
        {selection.stt_provider !== 'rtc-asr' ? <label>STT model<input aria-label="STT model" value={selection.stt_model} onChange={(e) => patch({ stt_model: e.target.value })} /></label> : null}
        {selection.tts_provider !== 'deepgram' ? <label>TTS model<input aria-label="TTS model" value={selection.tts_model} onChange={(e) => patch({ tts_model: e.target.value })} /></label> : null}
        <label>{selection.tts_provider === 'deepgram' ? 'Caller Aura model' : 'Caller voice ID'}<input aria-label="Caller voice ID" value={selection.tester_voice} onChange={(e) => patch({ tester_voice: e.target.value })} /></label>
        <label>{selection.tts_provider === 'deepgram' ? 'Agent Aura model' : 'Agent voice ID'}<input aria-label="Agent voice ID" value={selection.target_voice} onChange={(e) => patch({ target_voice: e.target.value })} /></label>
        <label>Language<input aria-label="Speech language" value={selection.language} onChange={(e) => patch({ language: e.target.value })} /></label>
      </div>
      {(selection.stt_provider === 'deepgram' || selection.tts_provider === 'deepgram') ? <p>Set <code>DEEPGRAM_API_KEY</code> in the Pipecat server environment.</p> : null}
      {(selection.stt_provider === 'cartesia' || selection.tts_provider === 'cartesia') ? <p>Set <code>CARTESIA_API_KEY</code> in the Pipecat server environment.{selection.tts_provider === 'cartesia' ? ' Use voice IDs from your Cartesia voice library.' : ''}</p> : null}
      <button type="button" className="secondary-link" disabled={saving || !dirty} onClick={() => void save()}>{saving ? 'Saving…' : 'Apply speech settings'}</button>
    </> : null}
    {message ? <p role="status">{message}</p> : null}
  </details>;
}
