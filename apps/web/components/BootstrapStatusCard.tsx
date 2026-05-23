import { BootstrapStatus } from '@/lib/types';

export function BootstrapStatusCard({ bootstrap }: { bootstrap: BootstrapStatus | null }) {
  if (!bootstrap) {
    return (
      <div className="card" style={{ padding: 20 }}>
        <h3 style={{ marginTop: 0 }}>Bootstrap status</h3>
        <p style={{ margin: 0, color: 'var(--muted)' }}>Bootstrap has not run yet.</p>
      </div>
    );
  }

  const tone = bootstrap.status === 'scaffolded' || bootstrap.realtime?.enabled
    ? 'var(--accent)'
    : bootstrap.status === 'partial'
      ? '#b45309'
      : 'var(--error-text)';

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Bootstrap status</h3>
        <strong style={{ color: tone, textTransform: 'capitalize' }}>{bootstrap.status}</strong>
      </div>

      <div style={{ display: 'grid', gap: 12 }}>
        <div>
          <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Realtime</p>
          <p style={{ margin: 0, lineHeight: 1.5 }}>{bootstrap.realtime?.status ?? 'unknown'}</p>
        </div>

        {bootstrap.voice ? (
          <div>
            <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Voice seam</p>
            <p style={{ margin: 0, lineHeight: 1.5 }}>
              {bootstrap.voice.status}
              {bootstrap.voice.mode ? ` · ${bootstrap.voice.mode}` : ''}
            </p>
          </div>
        ) : null}

        {bootstrap.pipecatPlan ? (
          <div>
            <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Pipecat plan</p>
            <p style={{ margin: 0, lineHeight: 1.5 }}>
              {String(bootstrap.pipecatPlan.transport ?? 'openai-realtime')} via {String(bootstrap.pipecatPlan.orchestrator ?? 'pipecat')}
            </p>
            {Array.isArray(bootstrap.pipecatPlan.steps) ? (
              <ul style={{ margin: '8px 0 0 18px', padding: 0, color: 'var(--muted)' }}>
                {bootstrap.pipecatPlan.steps.map((step, index) => (
                  <li key={index} style={{ marginBottom: 4, lineHeight: 1.4 }}>{String(step)}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {bootstrap.reason ? (
          <div>
            <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Reason</p>
            <p style={{ margin: 0, lineHeight: 1.5, color: 'var(--muted)' }}>{bootstrap.reason}</p>
          </div>
        ) : null}

        {bootstrap.nextStep ? (
          <div>
            <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Next step</p>
            <p style={{ margin: 0, lineHeight: 1.5 }}>{bootstrap.nextStep}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
