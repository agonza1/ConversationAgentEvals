'use client';

import { FormEvent, useState } from 'react';

import { runVoiceEval } from '@/lib/api';
import { EvalRunResponse } from '@/lib/types';

const SAMPLE_TRANSCRIPT = `Agent: Thanks for calling Bright Dental. How can I help today?
Caller: I need to book a cleaning.
Agent: I can help with that. Can I get your name, email, and preferred day?
Caller: Sure, it is Jamie, jamie@example.com, Thursday afternoon.
Agent: Great. I booked you for Thursday at 3 PM and sent a confirmation email.`;

const SAMPLE_CRITERIA = `Agent greets the caller
Agent identifies the caller's intent
Agent collects name and email
Agent books or confirms the appointment
Agent avoids unsupported claims`;

export function EvalBuilder() {
  const [title, setTitle] = useState('Appointment setter QA');
  const [conversation, setConversation] = useState(SAMPLE_TRANSCRIPT);
  const [criteria, setCriteria] = useState(SAMPLE_CRITERIA);
  const [result, setResult] = useState<EvalRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsRunning(true);
    setError(null);

    try {
      const nextResult = await runVoiceEval({ title, conversation, criteria });
      setResult(nextResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Eval failed');
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <section style={{ display: 'grid', gap: 20 }}>
      <form onSubmit={onSubmit} className="card" style={{ padding: 24, display: 'grid', gap: 16 }}>
        <div>
          <p style={{ color: 'var(--accent)', margin: '0 0 8px' }}>Voice AI evals</p>
          <h2 style={{ margin: 0, fontSize: 28 }}>Run a QA report from any call record.</h2>
        </div>

        <label style={{ display: 'grid', gap: 8 }}>
          <span style={{ fontWeight: 700 }}>Eval name</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12 }}
          />
        </label>

        <label style={{ display: 'grid', gap: 8 }}>
          <span style={{ fontWeight: 700 }}>Transcript, call JSON, or vCon JSON</span>
          <textarea
            value={conversation}
            onChange={(event) => setConversation(event.target.value)}
            rows={10}
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
          />
        </label>

        <label style={{ display: 'grid', gap: 8 }}>
          <span style={{ fontWeight: 700 }}>Eval criteria</span>
          <textarea
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
            rows={6}
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
          />
        </label>

        <button
          type="submit"
          disabled={isRunning}
          style={{
            justifySelf: 'start',
            border: 0,
            borderRadius: 8,
            background: 'var(--accent)',
            color: 'white',
            padding: '12px 18px',
            fontWeight: 800,
            opacity: isRunning ? 0.65 : 1,
          }}
        >
          {isRunning ? 'Running eval...' : 'Run eval'}
        </button>

        {error ? <p style={{ color: 'var(--error-text)', margin: 0 }}>{error}</p> : null}
      </form>

      {result ? (
        <section className="card" style={{ padding: 24, display: 'grid', gap: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <div>
              <p style={{ margin: '0 0 6px', color: 'var(--muted)' }}>{result.source_format} input</p>
              <h2 style={{ margin: 0, fontSize: 28 }}>{result.title}</h2>
            </div>
            <div style={{ fontSize: 40, fontWeight: 900, color: result.verdict === 'pass' ? 'var(--success-text)' : 'var(--danger)' }}>
              {result.overall_score}
            </div>
          </div>

          <div style={{ display: 'grid', gap: 12 }}>
            {result.checks.map((check) => (
              <article key={check.name} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <strong>{check.name}</strong>
                  <span style={{ color: check.status === 'pass' ? 'var(--success-text)' : 'var(--danger)', fontWeight: 800 }}>
                    {check.status} · {check.score}
                  </span>
                </div>
                <p style={{ color: 'var(--muted)', margin: '8px 0 0', fontSize: 14 }}>
                  Layer: {check.layer}
                  {check.root_cause_tag !== 'none' ? ` · Root cause: ${check.root_cause_tag}` : ''}
                </p>
                <p style={{ color: 'var(--muted)', marginBottom: 0 }}>{check.reason}</p>
                {check.evidence.length ? (
                  <ul style={{ marginBottom: 0 }}>
                    {check.evidence.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </article>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
            <div>
              <h3 style={{ marginTop: 0 }}>Risk flags</h3>
              <ul>
                {(result.risk_flags.length ? result.risk_flags : ['No major risk flags from this first pass.']).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 style={{ marginTop: 0 }}>Suggested fixes</h3>
              <ul>
                {result.suggested_fixes.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </div>

          <details>
            <summary style={{ cursor: 'pointer', fontWeight: 800 }}>vCon-compatible export JSON</summary>
            <pre style={{ overflowX: 'auto', background: '#0f172a', color: '#e2e8f0', borderRadius: 8, padding: 16 }}>
              {JSON.stringify(result.vcon_export, null, 2)}
            </pre>
          </details>
        </section>
      ) : null}
    </section>
  );
}
