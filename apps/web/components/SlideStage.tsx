import { Slide } from '@/lib/types';

export function SlideStage({ slide }: { slide?: Slide }) {
  if (!slide) {
    return <div className="card" style={{ padding: 24 }}>No slide available.</div>;
  }

  return (
    <div className="card" style={{ padding: 20, background: '#ffffff' }}>
      <div style={{ aspectRatio: '16 / 9', borderRadius: 18, overflow: 'hidden', border: '1px solid rgba(15,23,42,0.08)', background: 'linear-gradient(180deg, #ffffff, #eff6ff)' }}>
        {slide.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={slide.image_url} alt={slide.title} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <div style={{ display: 'grid', placeItems: 'center', width: '100%', height: '100%', padding: 32 }}>
            <div style={{ maxWidth: 720 }}>
              <p style={{ color: 'var(--accent)', marginBottom: 10 }}>Slide {slide.index + 1}</p>
              <h2 style={{ fontSize: 38, marginTop: 0 }}>{slide.title}</h2>
              <p style={{ lineHeight: 1.7, color: 'var(--muted)' }}>{slide.summary}</p>
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
        <div>
          <p style={{ color: 'var(--muted)', marginBottom: 4 }}>Summary</p>
          <p style={{ margin: 0 }}>{slide.summary}</p>
        </div>
        <div>
          <p style={{ color: 'var(--muted)', marginBottom: 4 }}>Talk track</p>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{slide.talk_track}</p>
        </div>
      </div>
    </div>
  );
}
