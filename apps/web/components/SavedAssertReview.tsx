'use client';

import type { AssertReviewView } from '@/lib/assertReview';
import type { AssertReviewFreshness } from '@/lib/execution';
import { AssertEvidenceReport } from './AssertEvidenceReport';

export function SavedAssertReview({ review, freshness, onRetry, onApply, automaticVerdict, automaticScore }: {
  review: AssertReviewView; freshness: AssertReviewFreshness | null; onRetry: () => void; onApply: () => void;
  automaticVerdict?: string | null; automaticScore?: number | null;
}) {
  const label = freshness?.status === 'current' ? 'Current' : freshness?.status === 'stale' ? 'Stale'
    : freshness?.status === 'cannot_verify' ? 'Cannot verify' : 'Checking freshness…';
  return <section className="saved-assert-review" aria-label="Saved ASSERT assessment">
    <header><h3>Selected ASSERT assessment</h3><p>{review.statusLabel} · {review.dateLabel}</p></header>
    <div className={`assert-freshness is-${freshness?.status || 'loading'}`} role="status" aria-label="Review freshness">
      <strong>{label}</strong><p>{freshness?.message || 'Checking saved evidence against current judging inputs.'}</p>
      {freshness?.status === 'cannot_verify' ? <button type="button" className="secondary-link" onClick={onRetry}>Check freshness again</button> : null}
      {freshness?.status === 'stale' ? <a href="#assert-review-action">Review changed evidence</a> : null}
    </div>
    <AssertEvidenceReport key={review.id} review={review} automaticVerdict={automaticVerdict} automaticScore={automaticScore}>
      {review.proposal && review.status === 'pending_confirmation' ? <button type="button" className="primary-cta"
        disabled={freshness?.status !== 'current'} onClick={onApply}>Apply proposed evaluation</button> : null}
    </AssertEvidenceReport>
  </section>;
}
