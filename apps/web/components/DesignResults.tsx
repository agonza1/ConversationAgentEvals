type Row = { id: string; label: string; status: string; reason: string; supported?: boolean;
  citations?: Array<{ source: string; path: string; text: string }> };

export function DesignResults({ findings, semanticReview }: {
  findings?: Record<string, unknown> | null;
  semanticReview?: { verdict?: string; citations?: string[] };
}) {
  const groups = [
    ['Focused behavior', 'behavior_results'],
    ['Programmatic checks', 'programmatic_check_results'],
    ['Required evidence', 'evidence_requirement_results'],
  ];
  if (!groups.some(([, key]) => Array.isArray(findings?.[key]) && (findings?.[key] as unknown[]).length)) return null;
  return <section className="card" aria-label="Evaluation design results">
    <h3>Evaluation design results</h3>
    <p>Only this case’s focused behavior is evaluated. Literal checks and artifact presence do not prove semantic compliance.</p>
    {groups.map(([title, key]) => {
      const rows = (Array.isArray(findings?.[key]) ? findings?.[key] : []) as Row[];
      if (!rows.length) return null;
      return <div key={key}>
        <h4>{title}</h4>
        <ul>{rows.map((row) => {
          const approved = key === 'behavior_results' && semanticReview?.verdict === 'pass';
          const status = approved ? 'pass' : row.status;
          return <li key={row.id}>
            <strong>{row.label}</strong> · {status === 'insufficient_evidence' ? 'Insufficient evidence' : status === 'pass' ? 'Pass' : 'Fail'}
            {row.supported === false ? ' · Unsupported' : ''}
            <p>{approved ? 'Confirmed by an explicitly applied semantic review; original automatic evidence remains below.' : row.reason}</p>
            {row.citations?.length ? <ul aria-label={`Evidence for ${row.label}`}>{row.citations.map((citation, index) => <li key={index}>
              <code>{citation.source} {citation.path}</code>: {citation.text}
            </li>)}</ul> : <p>No supporting citation recorded.</p>}
            {approved && semanticReview?.citations?.length ? <ul aria-label="Applied semantic review evidence">{semanticReview.citations.map((text, index) => <li key={index}>{text}</li>)}</ul> : null}
          </li>;
        })}</ul>
      </div>;
    })}
  </section>;
}
