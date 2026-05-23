'use client';

import { SessionStatus } from '@/lib/types';

const INTERVAL_OPTIONS = [4, 6, 8, 12, 16];

interface Props {
  status: SessionStatus;
  currentSlideIndex: number;
  slideCount: number;
  busy: boolean;
  autoplayEnabled: boolean;
  autoplayIntervalSeconds: number;
  onStart: () => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onEnd: () => Promise<void>;
  onPrev: () => Promise<void>;
  onNext: () => Promise<void>;
  onGoto: (index: number) => Promise<void>;
  onAutoplayToggle: (enabled: boolean) => Promise<void>;
  onAutoplayIntervalChange: (seconds: number) => Promise<void>;
}

export function ControlBar(props: Props) {
  const {
    status,
    currentSlideIndex,
    slideCount,
    busy,
    autoplayEnabled,
    autoplayIntervalSeconds,
    onStart,
    onPause,
    onResume,
    onEnd,
    onPrev,
    onNext,
    onGoto,
    onAutoplayToggle,
    onAutoplayIntervalChange,
  } = props;

  return (
    <div className="card" style={{ marginTop: 16, padding: 12, display: 'grid', gap: 10, opacity: 0.82 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Manual fallback
          </span>
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>
            Slide {currentSlideIndex + 1} / {slideCount}
          </span>
          <span style={{ color: autoplayEnabled ? '#047857' : 'var(--muted)', fontSize: 12 }}>
            {autoplayEnabled ? `Auto · ${autoplayIntervalSeconds}s` : 'Voice-first controls'}
          </span>
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <ActionButton disabled={busy || status !== 'idle'} onClick={onStart}>Start</ActionButton>
          <ActionButton disabled={busy || status !== 'presenting'} onClick={onPause}>Pause</ActionButton>
          <ActionButton disabled={busy || (status !== 'paused' && status !== 'answering')} onClick={onResume}>Resume</ActionButton>
          <ActionButton disabled={busy || status === 'ended'} onClick={onEnd}>End</ActionButton>
          <ActionButton disabled={busy || status === 'ended' || currentSlideIndex === 0} onClick={onPrev}>Prev</ActionButton>
          <ActionButton disabled={busy || status === 'ended' || currentSlideIndex >= slideCount - 1} onClick={onNext}>Next</ActionButton>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }} aria-label="Jump to slide">
          {Array.from({ length: slideCount }).map((_, index) => (
            <button
              key={index}
              onClick={() => void onGoto(index)}
              disabled={busy || status === 'ended'}
              aria-label={`Jump to slide ${index + 1}`}
              style={{
                width: 24,
                height: 24,
                borderRadius: 999,
                border: index === currentSlideIndex ? 'none' : '1px solid rgba(15,23,42,0.12)',
                background: index === currentSlideIndex ? 'linear-gradient(135deg, #2563eb, #0ea5e9)' : '#ffffff',
                color: index === currentSlideIndex ? '#ffffff' : 'var(--muted)',
                fontSize: 11,
                fontWeight: 700,
              }}
            >
              {index + 1}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ color: 'var(--muted)', fontSize: 12 }}>
            Pace
            <select
              value={autoplayIntervalSeconds}
              disabled={busy}
              onChange={(event) => void onAutoplayIntervalChange(Number(event.target.value))}
              style={{
                marginLeft: 6,
                borderRadius: 8,
                border: '1px solid rgba(15,23,42,0.12)',
                background: '#ffffff',
                color: 'var(--text)',
                padding: '5px 8px',
                fontSize: 12,
              }}
            >
              {INTERVAL_OPTIONS.map((seconds) => (
                <option key={seconds} value={seconds}>
                  {seconds}s
                </option>
              ))}
            </select>
          </label>
          <ActionButton
            disabled={busy || status === 'ended' || (status === 'idle' && autoplayEnabled)}
            onClick={() => onAutoplayToggle(!autoplayEnabled)}
          >
            {autoplayEnabled ? 'Stop auto' : 'Auto'}
          </ActionButton>
        </div>
      </div>
    </div>
  );
}

function ActionButton({ children, disabled, onClick }: { children: React.ReactNode; disabled?: boolean; onClick: () => Promise<void> }) {
  return (
    <button
      type="button"
      onClick={() => void onClick()}
      disabled={disabled}
      style={{
        borderRadius: 999,
        padding: '6px 10px',
        border: '1px solid rgba(15,23,42,0.10)',
        background: disabled ? 'rgba(148,163,184,0.10)' : '#ffffff',
        color: disabled ? 'var(--muted)' : 'var(--text)',
        fontSize: 12,
        lineHeight: 1.1,
      }}
    >
      {children}
    </button>
  );
}
