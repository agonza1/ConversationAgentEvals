import { SessionLiveState } from '@/lib/types';

export function LiveOpsCard({ live }: { live: SessionLiveState | null }) {
  if (!live) {
    return (
      <div className="card" style={{ padding: 20 }}>
        <h3 style={{ marginTop: 0 }}>Live ops</h3>
        <p style={{ margin: 0, color: 'var(--muted)' }}>Loading live presentation state…</p>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 20, display: 'grid', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>Live ops</h3>
        <span style={{ color: 'var(--muted)', fontSize: 13 }}>
          Slide {live.progress.current_slide_number} / {live.progress.slide_count}
        </span>
      </div>

      <div className="card" style={{ padding: 14, background: 'var(--panel-alt)' }}>
        <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Progress</p>
        <p style={{ margin: 0, lineHeight: 1.6 }}>
          {live.progress.has_started ? 'Presentation has started.' : 'Presentation has not started yet.'} {live.progress.remaining_slides} slides remaining.
        </p>
      </div>

      <div>
        <p style={{ margin: '0 0 8px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Upcoming slides</p>
        <div style={{ display: 'grid', gap: 10 }}>
          {live.upcoming_slides.length ? live.upcoming_slides.map((slide) => (
            <div key={slide.id} className="card" style={{ padding: 12, background: '#f8fbff' }}>
              <p style={{ margin: '0 0 4px', color: 'var(--accent)', fontSize: 13 }}>Slide {slide.index + 1}</p>
              <p style={{ margin: '0 0 4px', fontWeight: 700 }}>{slide.title}</p>
              <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.5 }}>{slide.summary}</p>
            </div>
          )) : (
            <p style={{ margin: 0, color: 'var(--muted)' }}>No upcoming slides left.</p>
          )}
        </div>
      </div>

      <div>
        <p style={{ margin: '0 0 8px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Recent events</p>
        <div style={{ display: 'grid', gap: 8 }}>
          {live.recent_events.slice(-5).reverse().map((event) => (
            <div key={event.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ fontSize: 13 }}>{event.type.replaceAll('_', ' ')}</span>
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                {new Date(event.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
