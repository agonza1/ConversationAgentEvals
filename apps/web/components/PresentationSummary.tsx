import { PresentationSession, Slide, TranscriptEvent } from '@/lib/types';

export function PresentationSummary({ slide, transcript, session }: { slide?: Slide; transcript: TranscriptEvent[]; session: PresentationSession }) {
  const latestAgentLine = [...transcript].reverse().find((item) => item.role === 'agent');
  const latestUserQuestion = [...transcript].reverse().find((item) => item.role === 'user');
  const latestAudienceQuestion = latestUserQuestion?.text?.replace(/^\[Simulated voice\]\s*/i, '') ?? null;

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Live narrative</h3>
        <span style={{ color: 'var(--muted)', fontSize: 13 }}>{transcript.length} transcript events</span>
      </div>

      <div style={{ marginTop: -4, marginBottom: 14 }}>
        <span style={{ color: session.autoplay_enabled ? '#86efac' : 'var(--muted)', fontSize: 13 }}>
          {session.autoplay_enabled
            ? `Presenter mode is pacing the deck at ${session.autoplay_interval_seconds}s per slide.`
            : 'Manual pacing is active.'}
        </span>
      </div>

      <div style={{ display: 'grid', gap: 14 }}>
        <div>
          <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Current slide focus</p>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{slide?.summary || 'No active slide summary yet.'}</p>
        </div>

        <div>
          <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Presenter voice</p>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{latestAgentLine?.text || 'The presenter will speak once the session starts.'}</p>
        </div>

        <div>
          <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Latest audience question</p>
          <p style={{ margin: 0, lineHeight: 1.6, color: latestAudienceQuestion ? 'var(--text)' : 'var(--muted)' }}>
            {latestAudienceQuestion || 'No questions yet.'}
          </p>
          {latestUserQuestion?.text?.startsWith('[Simulated voice]') ? (
            <p style={{ margin: '6px 0 0', color: 'var(--accent)', fontSize: 12 }}>Captured through the voice handoff simulation.</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
