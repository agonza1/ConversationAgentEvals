'use client';

import { FormEvent, useState } from 'react';

interface QuestionInputProps {
  busy: boolean;
  voiceActive?: boolean;
  voiceStatus?: string;
  showTestingControls?: boolean;
  onSubmit: (question: string) => Promise<void>;
  onSimulateVoice?: (question: string) => Promise<void>;
  onStartVoice?: () => Promise<void>;
  onStopVoice?: () => Promise<void>;
}

export function QuestionInput({ busy, voiceActive, voiceStatus, showTestingControls = true, onSubmit, onSimulateVoice, onStartVoice, onStopVoice }: QuestionInputProps) {
  const [question, setQuestion] = useState('');
  const trimmedQuestion = question.trim();
  const canAsk = Boolean(trimmedQuestion) && !busy;

  async function submitQuestion(mode: 'ask' | 'voice') {
    const trimmed = trimmedQuestion;
    if (!trimmed) return;
    if (mode === 'voice' && onSimulateVoice) {
      await onSimulateVoice(trimmed);
    } else {
      await onSubmit(trimmed);
    }
    setQuestion('');
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitQuestion('ask');
  }

  return (
    <form onSubmit={handleSubmit} style={{ marginTop: 20 }}>
      <label style={{ display: 'block', marginBottom: 8, color: 'var(--muted)' }}>
        {showTestingControls ? 'Ask a question' : 'Live voice'}
        {voiceStatus ? <span style={{ marginLeft: 10, fontSize: 12, color: voiceActive ? '#047857' : 'var(--muted)' }}>· {voiceStatus}</span> : null}
      </label>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {showTestingControls ? (
          <>
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="How do you compare to competitors?"
              style={{
                flex: 1,
                minHeight: 52,
                borderRadius: 14,
                border: '1px solid rgba(15,23,42,0.12)',
                background: '#ffffff',
                color: 'var(--text)',
                padding: '0 16px',
                minWidth: 260,
              }}
            />
            <button
              type="submit"
              disabled={!canAsk}
              style={{
                border: 'none',
                borderRadius: 14,
                minWidth: 120,
                background: 'linear-gradient(135deg, #2563eb, #0ea5e9)',
                color: '#ffffff',
                fontWeight: 700,
                minHeight: 52,
              }}
            >
              {busy ? 'Working…' : 'Ask'}
            </button>
            <button
              type="button"
              disabled={!canAsk || !onSimulateVoice}
              onClick={() => void submitQuestion('voice')}
              style={{
                borderRadius: 14,
                minWidth: 180,
                minHeight: 52,
                border: '1px solid rgba(37,99,235,0.25)',
                background: '#eff6ff',
                color: '#1d4ed8',
                fontWeight: 700,
              }}
            >
              Simulate voice question
            </button>
          </>
        ) : null}
        <button
          type="button"
          disabled={busy || voiceActive || !onStartVoice}
          onClick={() => void onStartVoice?.()}
          style={{
            borderRadius: 14,
            minWidth: 180,
            minHeight: 52,
            border: '1px solid rgba(16,185,129,0.3)',
            background: '#ecfdf5',
            color: '#047857',
            fontWeight: 700,
          }}
        >
          {voiceActive ? 'Voice live' : voiceStatus === 'Voice disconnected' ? 'Restart live voice' : 'Start live voice'}
        </button>
        <button
          type="button"
          disabled={busy || !voiceActive || !onStopVoice}
          onClick={() => void onStopVoice?.()}
          style={{
            borderRadius: 14,
            minWidth: 140,
            minHeight: 52,
            border: '1px solid rgba(239,68,68,0.25)',
            background: '#fef2f2',
            color: '#b91c1c',
            fontWeight: 700,
          }}
        >
          End live voice
        </button>
      </div>
    </form>
  );
}
