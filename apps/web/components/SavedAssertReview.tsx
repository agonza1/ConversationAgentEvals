'use client';
import type { AssertReviewView } from '@/lib/assertReview';
import type { AssertReviewFreshness } from '@/lib/execution';

export function SavedAssertReview({ review, freshness, onRetry, onApply }: {
  review: AssertReviewView; freshness: AssertReviewFreshness | null; onRetry: () => void; onApply: () => void;
}) {
  const label = freshness?.status === 'current' ? 'Current' : freshness?.status === 'stale' ? 'Stale' : freshness?.status === 'cannot_verify' ? 'Cannot verify' : 'Checking freshness…';
  const confidence = (value: AssertReviewView['behaviors'][number]['confidence']) =>
    value === null ? 'Unavailable' : typeof value === 'string' && ['high', 'medium', 'low'].includes(value) ? value : `${String(value)} (unsupported recorded label)`;
  return <section className="saved-assert-review" aria-label="Saved ASSERT assessment">
    <header><h3>Selected ASSERT assessment</h3><p>{review.id} · {review.statusLabel}</p><p>{review.dateLabel}</p>
      <p>{review.model || 'Model unavailable'} · {review.version ? `ASSERT ${review.version}` : 'ASSERT version unavailable'} · {review.evidenceLabel}</p></header>
    <div className={`assert-freshness is-${freshness?.status || 'loading'}`} role="status" aria-label="Review freshness">
      <strong>{label}</strong><p>{freshness?.message || 'Checking saved evidence against current judging inputs.'}</p>
      {freshness?.status === 'cannot_verify' ? <button type="button" className="secondary-link" onClick={onRetry}>Check freshness again</button> : null}
      {freshness?.status === 'stale' ? <a href="#assert-review-action">Review changed evidence</a> : null}
    </div>
    {review.rationale ? <details><summary>Semantic rationale</summary><p>{review.rationale}</p></details> : null}
    <section aria-label="ASSERT evaluation details"><h4>Recorded dimensions</h4>
      {review.dimensions.length ? review.dimensions.map((dimension) => <article key={dimension.name} className="assert-dimension-card">
        <strong>{dimension.name.replaceAll('_', ' ')}</strong><p>{dimension.outcome}{dimension.value !== null && typeof dimension.value !== 'boolean' ? ` · ${String(dimension.value)}` : ''}
          {dimension.value !== null && typeof dimension.value !== 'boolean' ? ` · ${dimension.ordinalLabel || 'Label unavailable'}` : ''}</p>
        <p>{dimension.justification || 'Justification unavailable'}</p>
        {dimension.hasScale ? <details><summary>Rubric for {dimension.name.replaceAll('_', ' ')}</summary>
          {dimension.rubric.length ? <ul>{dimension.rubric.map((entry, index) => <li key={index}>{typeof entry.value}: {String(entry.value)} — {entry.label}</li>)}</ul> : <p>Rubric unavailable</p>}</details> : null}
      </article>) : <p>Dimension results unavailable</p>}
    </section>
    <section aria-label="Behavior breakdown"><h4>Behavior breakdown</h4>
      {review.behaviors.length ? review.behaviors.map((node, index) => <details key={index}>
        <summary>{node.name} · {node.outcome}</summary><p>Relevant: {node.relevant === null ? 'Unavailable' : String(node.relevant)} · Violated: {node.violated === null ? 'Unavailable' : String(node.violated)}</p>
        <p>Confidence: {confidence(node.confidence)}</p><p>{node.reasoning || 'Reasoning unavailable'}</p>
        {node.references.map((reference, i) => <p key={i}>{reference} — unresolved reference</p>)}
        <p className="scenarios-muted">Evidence references unresolved: recorded judge turns cannot be mapped reliably to CAE transcript or tool identities.</p>
      </details>) : <p>Behavior judgments unavailable</p>}
    </section>
    <details><summary>Technical provenance</summary><dl>{review.technical.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value || 'Unavailable'}</dd></div>)}</dl></details>
    {review.citations.length ? <details><summary>Recorded evidence references</summary><ul>{review.citations.map((citation, index) => <li key={index}>{citation} — unresolved reference</li>)}</ul></details> : null}
    {review.proposal ? <details><summary>Selected review proposal</summary><p>{review.proposal.verdict.replaceAll('_', ' ')} — {review.proposal.summary}</p>
      <p>Corrected findings</p><ul>{review.proposal.corrected_findings.map((finding, index) => <li key={index}>{finding}</li>)}</ul><p>Remaining gaps</p>
      <ul>{review.proposal.remaining_gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></details> : null}
    {review.proposal && review.status === 'pending_confirmation' ? <button type="button" className="primary-cta" disabled={freshness?.status !== 'current'} onClick={onApply}>Apply proposed evaluation</button> : null}
    <p className="scenarios-muted">This selected assessment is separate from the deterministic verdict and currently applied adjudication.</p>
  </section>;
}
