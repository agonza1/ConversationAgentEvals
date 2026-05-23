'use client';

interface HeyGenAvatarPanelProps {
  talkTrack: string;
  speaking: boolean;
  voiceActive?: boolean;
}

export function HeyGenAvatarPanel({ talkTrack, speaking, voiceActive }: HeyGenAvatarPanelProps) {
  const statusLabel = voiceActive ? (speaking ? 'Speaking' : 'WebRTC ready') : 'Start live voice';

  return (
    <div className="card" style={{ padding: 20 }}>
      <h3 style={{ marginTop: 0 }}>Video avatar presenter</h3>
      <div
        style={{
          aspectRatio: '16 / 9',
          borderRadius: 18,
          background: 'linear-gradient(180deg, #020617, #0f172a)',
          border: '1px solid rgba(15,23,42,0.08)',
          display: 'grid',
          placeItems: 'center',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        <video
          id="heygen-avatar-video"
          autoPlay
          playsInline
          style={{ width: '100%', height: '100%', objectFit: 'cover', background: '#020617' }}
        />
        {!voiceActive ? (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center', padding: 24, color: '#e2e8f0' }}>
            <div>
              <strong>Pipecat HeyGen avatar</strong>
              <p style={{ margin: '8px 0 0', color: '#94a3b8', lineHeight: 1.5 }}>
                Click “Start live voice” to open the simple WebRTC session. Pipecat will return HeyGen avatar video on this stream.
              </p>
            </div>
          </div>
        ) : null}
        <div
          style={{
            position: 'absolute',
            top: 16,
            right: 16,
            padding: '6px 10px',
            borderRadius: 999,
            background: speaking ? 'rgba(37,99,235,0.84)' : 'rgba(15,23,42,0.68)',
            color: '#ffffff',
            fontSize: 12,
            fontWeight: 700,
          }}
        >
          {statusLabel}
        </div>
      </div>
      <div style={{ marginTop: 14 }}>
        <p style={{ marginBottom: 6, color: 'var(--muted)' }}>Current talk track</p>
        <p style={{ margin: 0, lineHeight: 1.5 }}>{talkTrack}</p>
      </div>
    </div>
  );
}
