import { TranscriptEvent } from '@/lib/types';

export function TranscriptPanel({ transcript }: { transcript: TranscriptEvent[] }) {
  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Transcript</h3>
        <span style={{ color: 'var(--muted)', fontSize: 13 }}>{transcript.length} events</span>
      </div>
      <div style={{ display: 'grid', gap: 12, maxHeight: 420, overflow: 'auto' }}>
        {transcript.map((item) => (
          <div key={item.id} style={{ padding: 12, borderRadius: 14, background: '#f8fbff', border: '1px solid rgba(15,23,42,0.06)' }}>
            <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>{item.role}</p>
            <p style={{ margin: 0, lineHeight: 1.5 }}>{item.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
