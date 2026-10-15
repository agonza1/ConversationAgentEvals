import { evaluationEvidence, reviewEvidenceStatus, type EvidencePresence } from '@/lib/evaluationEvidence';
import styles from './EvaluationEvidenceSummary.module.css';

export function EvaluationEvidenceSummary({ report, review, judging = false }: { report: unknown; review?: unknown; judging?: boolean }) {
  const summary = evaluationEvidence(report);
  const label = (presence: EvidencePresence) => presence === 'present' ? 'Supplied' : presence === 'missing' ? 'Not supplied' : 'Not recorded';
  return <section className={styles.summary} aria-label="Evaluation evidence summary">
    <header><div><p className="eyebrow">{summary.sourceLabel} · {summary.coverageLabel}</p>
      <h2 id="evidence-evaluation-result" tabIndex={-1}>{summary.headline}</h2></div>
      <span className={summary.failed ? styles.failure : styles.badge}>Evaluator: {summary.verdictLabel}</span></header>
    <p className={styles.explanation}>{summary.explanation}</p>
    <dl className={styles.coverage}>
      <div><dt>Conversation</dt><dd>{label(summary.transcript)}<small>What participants said</small></dd></div>
      <div><dt>Tool / action trace</dt><dd>{label(summary.trace)}<small>Requests and reported results</small></dd></div>
      <div><dt>Resulting state</dt><dd>{label(summary.state)}<small>Recorded backend outcome</small></dd></div>
      <div><dt>LLM judgment</dt><dd>{reviewEvidenceStatus(review, judging)}<small>Separate review, not execution proof</small></dd></div>
    </dl>
    <p className={styles.note}>Missing evidence means unverified, not automatically failed. Imported scores and analyses are historical, not proof of execution.</p>
  </section>;
}

export function EvidenceEvaluationFlow({ loaded, evaluated, review, judging = false }: { loaded: boolean; evaluated: boolean; review?: unknown; judging?: boolean }) {
  const reviewStatus = reviewEvidenceStatus(review, judging);
  const reviewed = reviewStatus === 'Review recorded separately';
  const current = !loaded ? 0 : !evaluated ? 1 : !reviewed ? 2 : 3;
  return <section className={styles.flow} aria-label="Imported evidence evaluation flow">
    <div><strong>Analyze an existing conversation</strong><p>No CAE test run or new call is required.</p></div>
    <ol>{['Import or paste evidence', 'Check against a scenario', 'Request LLM review'].map((step, index) =>
      <li key={step} aria-current={current === index ? 'step' : undefined} className={index < current ? styles.done : undefined}>
        <span aria-hidden="true">{index < current ? '✓' : index + 1}</span><div>{step}<small>{index === 0 ? 'Extract available evidence' : index === 1 ? 'Explicit, deterministic checks' : judging ? 'Review in progress' : 'Optional · explicit model request'}</small></div>
      </li>)}</ol>
  </section>;
}
