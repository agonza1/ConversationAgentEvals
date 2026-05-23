import { BootstrapStatus, RealtimeClientConfig, SessionSnapshot, VoicePipelineStatus } from '@/lib/types';

function describeMode(realtime: RealtimeClientConfig | null, voice: VoicePipelineStatus | null) {
  const hasEphemeral = Boolean(voice?.transport && (voice.transport as Record<string, unknown>).client_secret);
  if (hasEphemeral) return 'Live OpenAI Realtime voice is active.';
  if (realtime?.status === 'live_ready') return 'Live OpenAI Realtime is configured through the realtime bridge.';
  if (realtime?.status === 'browser_direct_ready') return 'Live OpenAI Realtime can start directly from this page via browser WebRTC.';
  if (realtime?.enabled) return 'Live OpenAI Realtime is partially configured.';
  return 'Offline proof mode: manual controls plus grounded text and simulated voice questions.';
}

export function DemoReadinessCard({ snapshot, bootstrap, voice }: { snapshot: SessionSnapshot; bootstrap: BootstrapStatus | null; voice: VoicePipelineStatus | null }) {
  const realtime = bootstrap?.realtime ?? snapshot.realtime;
  const readySlides = snapshot.slides.length > 0;
  const readySession = Boolean(snapshot.session.public_token);
  const liveVoiceReady = realtime?.status === 'live_ready' || realtime?.status === 'browser_direct_ready';

  const checks = [
    { label: 'Slides processed', ok: readySlides, detail: readySlides ? `${snapshot.slides.length} slides loaded` : 'No processed slides yet' },
    { label: 'Public session ready', ok: readySession, detail: readySession ? 'Presentation URL is live' : 'Session link missing' },
    {
      label: 'Live voice path',
      ok: liveVoiceReady,
      detail: realtime?.status === 'live_ready'
        ? `Realtime bridge configured for ${realtime?.model}`
        : realtime?.status === 'browser_direct_ready'
          ? `Browser-direct WebRTC ready for ${realtime?.model}`
          : 'Text/simulated-voice proof only',
    },
    { label: 'Presenter mode', ok: true, detail: 'Pipecat HeyGen avatar mode: server-owned avatar video transport' },
  ];

  const readyCount = checks.filter((item) => item.ok).length;
  const headline = readyCount >= 3 ? 'Demo-ready with live seams' : readyCount >= 2 ? 'Ready for text proof' : 'Partially ready';
  const tone = readyCount >= 3 ? 'var(--accent)' : readyCount >= 2 ? '#b45309' : 'var(--error-text)';

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Demo readiness</h3>
        <strong style={{ color: tone }}>{headline}</strong>
      </div>

      <p style={{ marginTop: 0, color: 'var(--muted)', lineHeight: 1.6 }}>{describeMode(realtime, voice)}</p>

      <div style={{ display: 'grid', gap: 10 }}>
        {checks.map((item) => (
          <div key={item.label} className="card" style={{ padding: 12, background: 'var(--panel-alt)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <strong>{item.label}</strong>
              <span style={{ color: item.ok ? 'var(--accent)' : 'var(--muted)', fontSize: 13 }}>{item.ok ? 'Ready' : 'Pending'}</span>
            </div>
            <p style={{ margin: '6px 0 0', color: 'var(--muted)', fontSize: 13, lineHeight: 1.5 }}>{item.detail}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
