'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { AssertReviewView } from '@/lib/assertReview';
import styles from './AssertEvidenceReport.module.css';

const humanize = (value: string | null | undefined) => value
  ? value.replaceAll('_', ' ').replace(/\b\w/, (letter) => letter.toUpperCase()) : 'Not reported';

export function AssertEvidenceReport({ review, automaticVerdict: fallbackVerdict, automaticScore: fallbackScore, technical, children }: {
  review: AssertReviewView;
  automaticVerdict?: string | null;
  automaticScore?: number | null;
  technical?: ReactNode;
  children?: ReactNode;
}) {
  const automaticVerdict = review.automaticSnapshot ? review.automaticSnapshot.verdict : fallbackVerdict;
  const automaticScore = review.automaticSnapshot ? review.automaticSnapshot.score : fallbackScore;
  const [activeMessage, setActiveMessage] = useState<string | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [allReferences, setAllReferences] = useState(false);
  const messageElements = useRef(new Map<string, HTMLLIElement>());
  const outcomeUnverified = review.dimensions.some((dimension) =>
    dimension.name === 'unverified_operational_outcome' && dimension.outcome === 'Flagged');
  const proposal = review.proposal?.verdict;
  const comparableVerdict = (value: string) => value.trim().toLowerCase() === 'failed' ? 'fail' : value.trim().toLowerCase();
  const sameDisplayedVerdict = automaticVerdict && proposal
    ? comparableVerdict(automaticVerdict) === comparableVerdict(proposal) : null;
  const flagged = review.dimensions.filter((dimension) => dimension.outcome === 'Flagged');
  const flaggedBehaviors = review.behaviors.filter((behavior) => behavior.outcome === 'Flagged');
  const flaggedBehaviorCount = flaggedBehaviors.length;
  const hasFlags = flagged.length > 0 || flaggedBehaviorCount > 0;
  const tone = outcomeUnverified || proposal === 'needs_review' || (proposal === 'pass' && hasFlags) ? 'review'
    : proposal === 'fail' ? 'fail' : proposal === 'pass' ? 'clear' : 'unknown';
  const title = outcomeUnverified ? 'Outcome unverified' : proposal === 'fail' ? 'Issues found'
    : hasFlags ? 'Issues flagged' : proposal === 'pass' ? 'No semantic issues flagged'
      : proposal === 'needs_review' ? 'Needs review' : 'Review recorded';
  const references = allReferences ? review.evidenceReferences : review.evidenceReferences.slice(0, 6);

  useEffect(() => {
    setActiveMessage(null);
    setTranscriptOpen(false);
    setAllReferences(false);
  }, [review.id]);

  useEffect(() => {
    if (!transcriptOpen || !activeMessage) return;
    const element = messageElements.current.get(activeMessage);
    element?.focus({ preventScroll: true });
    element?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  }, [activeMessage, transcriptOpen]);

  return <section className={styles.report} data-outcome={tone} aria-label="ASSERT evidence report">
    <header className={styles.header}>
      <div>
        <p className={styles.eyebrow}>ASSERT · Second opinion</p>
        <h3>{title}</h3>
        <p className={styles.subtitle}>A review of the evidence—not a new agent action.</p>
      </div>
      {typeof automaticScore === 'number' && Number.isFinite(automaticScore) ? <div className={styles.score}>
        <strong>{automaticScore}<small>/100</small></strong>
        <span>Automatic criteria score</span>
      </div> : null}
    </header>

    <dl className={styles.verdicts} aria-label="Automatic and ASSERT verdicts">
      <div><dt>Automatic</dt><dd>{humanize(automaticVerdict)}</dd></div>
      <div><dt>ASSERT proposal</dt><dd>{humanize(proposal)}</dd></div>
      <div><dt>Application</dt><dd>{review.status === 'applied' ? 'Applied'
        : review.status === 'superseded' ? 'Superseded'
          : ['pending_confirmation', 'completed'].includes(review.status) ? 'Not applied' : 'Not recorded'}</dd></div>
    </dl>
    <p className={styles.footnote}>Criteria score—not a resolution percentage.
      {sameDisplayedVerdict === true ? ' Both assessments have the same verdict; this is not independent proof of completion.'
        : sameDisplayedVerdict === false ? ' ASSERT proposes a different verdict. The automatic result stays preserved.' : ''}</p>

    <section className={styles.coverage} aria-label="Evidence coverage">
      {([
        ['Transcript', review.coverage.transcript],
        ['Tool trace', review.coverage.toolTrace],
        ['Final state', review.coverage.finalState],
      ] as const).map(([label, available]) => <div key={label} data-available={String(available)}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" />
          {available === true ? <path d="m8 12 3 3 5-6" /> : available === false ? <path d="M8 12h8" /> : <path d="M10 9a2 2 0 0 1 4 0c0 2-2 2-2 4m0 3h.01" />}</svg>
        <span>{label}<strong>{available === true ? 'Available' : available === false ? 'Not captured' : 'Unknown'}</strong></span>
      </div>)}
    </section>
    <p className={styles.footnote}>Available evidence is not proof that a business action succeeded.</p>

    {review.legacyEvidence ? <aside className={styles.notice} aria-label="Historical evidence mapping">
      <strong>Historical evidence mapping</strong>
      <p>This review does not retain complete, verified evidence mapping metadata. Keep it as history; request a new review after checking freshness.</p>
    </aside> : null}
    {review.evidenceDetailsStatus === 'limited' || review.evidenceDetailsStatus === 'invalid' ? <aside className={styles.notice} aria-label="Evidence mapping unavailable">
      <strong>Evidence mapping unavailable</strong>
      <p>{review.evidenceDetailsMessage || 'The retained evidence cannot be safely mapped in this view. No transcript links are inferred.'}</p>
    </aside> : null}
    {review.evidenceDetailsStatus === 'complete' && review.evidenceDetailsMessage ? <aside className={styles.notice} aria-label="Citation warnings">
      <strong>Citation warnings</strong><p>{review.evidenceDetailsMessage}</p>
    </aside> : null}

    {hasFlags ? <section className={styles.findings} aria-label="What needs attention">
      <h4>What needs attention</h4>
      {flagged.map((dimension) => <article key={dimension.name}>
        <span className={styles.issueMarker} aria-hidden="true">!</span>
        <div><strong>{dimension.name === 'unverified_operational_outcome' ? 'A claimed outcome has no matching proof' : humanize(dimension.name)}</strong>
          <p>{dimension.justification || 'Inspect the recorded evidence for this finding.'}</p></div>
      </article>)}
      {flaggedBehaviors.map((behavior, index) => <article key={`behavior-${index}`}>
        <span className={styles.issueMarker} aria-hidden="true">!</span>
        <div><strong>{humanize(behavior.name)}</strong><p>{behavior.reasoning || 'This behavior was flagged in the recorded assessment.'}</p></div>
      </article>)}
    </section> : <p className={styles.summary}>{review.proposal?.summary || review.rationale || 'The recorded review has no summary.'}</p>}

    <section className={styles.nextAction} aria-label="Suggested next step">
      <span>Next step</span>
      <p>{outcomeUnverified
        ? 'Attach the matching tool result or final business-state receipt, then evaluate the evidence again.'
        : review.nextAction || 'Inspect the evidence and proposal before deciding whether to apply it.'}</p>
    </section>

    {review.evidenceReferences.length ? <section className={styles.evidence} aria-label="Evidence returned by ASSERT">
      <div className={styles.sectionHeading}><h4>Evidence behind the review</h4><span>{review.evidenceReferences.length} references</span></div>
      {references.map((reference, index) => <article className={styles.reference} key={`${reference.index}-${index}`}>
        <h5><span>{String(reference.index)}</span>{reference.description || 'Recorded evidence reference'}</h5>
        {reference.parts.map((part, partIndex) => <div className={styles.quote} key={partIndex}>
          {part.quote ? <blockquote>{part.quote}</blockquote> : <p>No quote retained in this reference.</p>}
          <div className={styles.quoteActions}>
            <span className={styles.badge} data-status={part.status}>{part.status === 'matched' ? 'Matched' : part.status === 'ambiguous' ? 'Ambiguous' : 'Unresolved'}</span>
            {part.status === 'matched' && part.messageId ? <button type="button" onClick={() => {
              const messageId = part.messageId;
              if (!messageId) return;
              setTranscriptOpen(true); setActiveMessage(messageId);
              // Refocus on repeated clicks, too. The effect handles the first disclosure.
              if (transcriptOpen && activeMessage === messageId) {
                messageElements.current.get(messageId)?.focus();
              }
            }}>View transcript turn <span aria-hidden="true">↗</span></button> : null}
          </div>
          {part.status !== 'matched' ? <p className={styles.referenceDetail}>{part.detail || 'This reference cannot be safely linked to a single transcript turn.'}</p> : null}
        </div>)}
      </article>)}
      {review.evidenceReferences.length > 6 ? <button type="button" className={styles.disclosureButton} onClick={() => setAllReferences((value) => !value)}>
        {allReferences ? 'Show fewer references' : `Show all ${review.evidenceReferences.length} references`}
      </button> : null}
    </section> : <p className={styles.footnote}>No structured evidence references were retained. See the recorded rationale below.</p>}

    {review.evidenceMessages.length ? <details className={styles.transcript} open={transcriptOpen}
      onToggle={(event) => setTranscriptOpen(event.currentTarget.open)}>
      <summary>Transcript reviewed <span>{review.evidenceMessages.length} messages</span></summary>
      <p className={styles.footnote}>The exact message snapshot retained with this review. It is not the editable evidence above.</p>
      <ol>{review.evidenceMessages.map((message, index) => <li key={message.id} tabIndex={-1}
        data-active={activeMessage === message.id} ref={(element) => {
          if (element) messageElements.current.set(message.id, element); else messageElements.current.delete(message.id);
        }}>
        <span className={styles.turnNumber}>{index + 1}</span>
        <div><strong>{message.speakerLabel}</strong><p>{message.text}</p></div>
      </li>)}</ol>
    </details> : null}

    {review.rationale ? <details className={styles.details}><summary>Semantic rationale</summary><p>{review.rationale}</p></details> : null}
    <details className={styles.details}>
      <summary>All checks <span>{flagged.length} flagged {flagged.length === 1 ? 'dimension' : 'dimensions'} · {flaggedBehaviorCount}/{review.behaviors.length} flagged behaviors</span></summary>
      <section aria-label="ASSERT evaluation details"><h4>Recorded dimensions</h4>
        <p className={styles.footnote}>{review.evidenceLabel}</p>
        {review.dimensions.length ? review.dimensions.map((dimension) => <article className={styles.check} key={dimension.name}>
          <div><strong>{humanize(dimension.name)}</strong><span className={styles.badge}>{dimension.outcome}</span></div>
          {dimension.value !== null && typeof dimension.value !== 'boolean' ? <p>{String(dimension.value)} · {dimension.ordinalLabel || 'Label unavailable'}</p> : null}
          <p>{dimension.justification || 'Justification unavailable'}</p>
          {dimension.hasScale ? <details><summary>Rubric for {dimension.name.replaceAll('_', ' ')}</summary>
            {dimension.rubric.length ? <ul>{dimension.rubric.map((entry, index) => <li key={index}>{typeof entry.value}: {String(entry.value)} — {entry.label}</li>)}</ul> : <p>Rubric unavailable</p>}
          </details> : null}
        </article>) : <p>Dimension results unavailable</p>}
      </section>
      <section aria-label="Behavior breakdown"><h4>Behavior breakdown</h4>
        {review.behaviors.length ? review.behaviors.map((node, index) => <details className={styles.behavior} key={index}>
          <summary>{node.name} · {node.outcome}</summary>
          <p>Confidence: {node.confidence === null ? 'Unavailable'
            : typeof node.confidence === 'string' && ['high', 'medium', 'low'].includes(node.confidence) ? node.confidence : `${String(node.confidence)} (unsupported recorded label)`}</p>
          <p>{node.reasoning || 'Reasoning unavailable'}</p>
          {node.references.length ? <p>{node.references.join(', ')} — unresolved reference</p> : null}
        </details>) : <p>Behavior judgments unavailable</p>}
      </section>
      {review.proposal ? <details><summary>Selected review proposal</summary><p>{humanize(review.proposal.verdict)} — {review.proposal.summary}</p>
        {review.proposal.corrected_findings.length ? <><p>Corrected findings</p><ul>{review.proposal.corrected_findings.map((finding, index) => <li key={index}>{finding}</li>)}</ul></> : null}
        {review.proposal.remaining_gaps.length ? <><p>Remaining gaps</p><ul>{review.proposal.remaining_gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></> : null}
      </details> : null}
    </details>
    <details className={styles.details}><summary>Technical details</summary>
      <dl className={styles.technical}>
        <div><dt>Engine</dt><dd>ASSERT{review.version ? ` ${review.version}` : ''}</dd></div>
        <div><dt>Model connection</dt><dd>{review.model || 'Not recorded'}</dd></div>
        <div><dt>Recorded</dt><dd>{review.dateLabel}</dd></div>
        <div><dt>Review time</dt><dd>{review.latencyMs === null ? 'Not recorded' : `${(review.latencyMs / 1000).toFixed(1)} seconds`}</dd></div>
        <div><dt>Transcript adapter</dt><dd>{review.adapterVersion || 'Not recorded'}</dd></div>
        <div><dt>Recorded agreement</dt><dd>{review.agrees === null ? 'Not recorded' : review.agrees ? 'Agrees' : 'Disagrees'}</dd></div>
        {review.technical.filter((entry) => entry.value !== null && !['ASSERT version', 'Judge model', 'Transcript adapter'].includes(entry.label))
          .map((entry) => <div key={entry.label}><dt>{entry.label}</dt><dd>{entry.value}</dd></div>)}
      </dl>
      {review.nextAction ? <p><strong>Recorded next action:</strong> {review.nextAction}</p> : null}
      {technical}
    </details>
    {children ? <footer className={styles.actions}>{children}</footer> : null}
  </section>;
}
