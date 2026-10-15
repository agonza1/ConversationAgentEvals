'use client';

import { useEffect, useRef, useState } from 'react';
import { ApiAwareLink } from './ApiAwareLink';
import type { EditableAssertSpec } from '@/lib/types';
import { authoringChanges, requestAuthoringDraft, type AuthoringContext, type AuthoringDraft, type AuthoringScope } from '@/lib/assistedAuthoring';
import styles from './AssistedTestBuilder.module.css';

const starters = [
  ['Appointment changes', 'Test a caller changing their requested appointment time twice. Check the agent confirms the latest request without claiming an unconfirmed booking.'],
  ['Billing address', 'Test a caller correcting their billing address. Check the agent repeats the final address accurately and does not invent a successful account update.'],
  ['Account access', 'Test a caller who cannot access their account. Check the agent gives a useful next step without asking for passwords or inventing an identity-verification policy.'],
] as const;

type DraftState = { result: AuthoringDraft; request: string; answers: Record<string, string> };

export function AssistedTestBuilder({ scope, context, initialRequest = '', onApprove, onManual, onCancel }: {
  scope: AuthoringScope;
  context?: AuthoringContext;
  initialRequest?: string;
  onApprove: (spec: EditableAssertSpec) => Promise<void>;
  onManual: (spec?: EditableAssertSpec) => void;
  onCancel?: () => void;
}) {
  const [request, setRequest] = useState(initialRequest);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [previous, setPrevious] = useState<DraftState | null>(null);
  const [questions, setQuestions] = useState<AuthoringDraft['questions']>([]);
  const [changes, setChanges] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<{ id: number; controller: AbortController } | null>(null);
  const serial = useRef(0);
  const accepting = useRef(false);
  const resultRef = useRef<HTMLHeadingElement>(null);
  const spec = draft?.result.spec;
  const outdated = Boolean(draft && (draft.request !== request || JSON.stringify(draft.answers) !== JSON.stringify(answers)));
  const validDraft = Boolean(spec && spec.title.trim().length >= 3 && spec.objective.trim().length >= 10 && spec.permissible_behavior?.trim()
    && spec.required_behaviors.length && [...spec.required_behaviors, ...spec.forbidden_behaviors].every((rule) => rule.label.trim())
    && spec.scenarios.length && spec.scenarios.every((item) => item.steps?.[0]?.trim() && item.expected_outcome?.trim()));

  useEffect(() => () => { active.current?.controller.abort(); active.current = null; }, []);

  function cancelGeneration() {
    active.current?.controller.abort(); active.current = null;
    setBusy(false);
  }

  async function generate() {
    if (active.current || saving || request.trim().length < 10) return;
    const id = ++serial.current;
    const controller = new AbortController();
    active.current = { id, controller };
    setBusy(true); setError(null);
    try {
      const result = await requestAuthoringDraft({ request: request.trim(), scope, context, answers, current_spec: spec || undefined }, controller.signal);
      if (active.current?.id !== id) return;
      if (result.questions.length) {
        setQuestions(result.questions);
      } else {
        setPrevious(draft);
        setChanges(spec && result.spec ? authoringChanges(spec, result.spec) : []);
        setDraft({ result, request, answers: { ...answers } }); setQuestions([]);
      }
      window.requestAnimationFrame(() => resultRef.current?.focus());
    } catch (err) {
      if (active.current?.id === id) setError(err instanceof Error ? err.message : 'Could not generate this draft.');
    } finally {
      if (active.current?.id === id) { active.current = null; setBusy(false); }
    }
  }

  function editSpec(next: EditableAssertSpec) {
    if (draft) setDraft({ ...draft, result: { ...draft.result, spec: next } });
  }

  async function approve() {
    if (!spec || !validDraft || busy || outdated || questions.length || accepting.current) return;
    accepting.current = true; setSaving(true); setError(null);
    try { await onApprove(spec); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not save. Your draft is still here.'); }
    finally { accepting.current = false; setSaving(false); }
  }

  return <section className={styles.builder} aria-label="Assisted test builder" aria-busy={busy || saving}>
    <div className={styles.intro}>
      <span className={styles.spark} aria-hidden="true">✦</span>
      <div><p className="eyebrow">{scope === 'case' ? 'One quick case' : 'A small test set'} · assisted drafting</p>
        <h2>What should your agent get right?</h2>
        <p>Describe it once. We’ll suggest the caller, checks and expected outcome for you to review.</p></div>
    </div>
    {context ? <details className={styles.context}><summary>Using context: {context.label}</summary><p>{context.text}</p></details>
      : <p className={styles.note}>No policy attached. Missing business rules won’t be treated as known facts.</p>}
    <label className={styles.composer}>What would you like to test?
      <textarea rows={3} maxLength={8000} value={request} disabled={busy || saving}
        onChange={(event) => { setRequest(event.target.value); setAnswers({}); setQuestions([]); }}
        placeholder="e.g. A caller changes their mind twice. Make sure the agent follows the latest request." />
    </label>
    {!draft ? <div className={styles.chips} aria-label="Test starters">{starters.map(([label, text]) =>
      <button key={label} type="button" disabled={busy || saving} onClick={() => { setRequest(text); setAnswers({}); setQuestions([]); }}>{label}</button>)}</div> : null}
    {questions.length ? <section className={styles.questions} aria-label="Clarify the test">
      <h3 ref={resultRef} tabIndex={-1}>Just {questions.length === 1 ? 'one detail' : 'two details'} before drafting</h3>
      <p>These answers change what counts as correct.</p>
      {questions.map((question) => <fieldset key={question.id} disabled={busy || saving}>
        <legend>{question.question}</legend>
        {question.options.length ? <div className={styles.chips}>{question.options.map((option) =>
          <button key={option} type="button" aria-pressed={answers[question.id] === option}
            onClick={() => setAnswers((current) => ({ ...current, [question.id]: option }))}>{option}</button>)}</div> : null}
        <label>Or answer briefly<input maxLength={1000} value={answers[question.id] || ''}
          onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))} /></label>
      </fieldset>)}
    </section> : null}
    <div className={styles.actions}>
      <button className="primary-link" type="button" onClick={() => void generate()}
        disabled={busy || saving || request.trim().length < 10 || questions.some((question) => !answers[question.id]?.trim())}>
        {busy ? 'Drafting…' : questions.length ? 'Use answers and draft' : draft ? 'Update draft' : scope === 'case' ? 'Draft my case' : 'Draft my test set'}
      </button>
      {busy ? <button className="secondary-link" type="button" onClick={cancelGeneration}>Cancel drafting</button> : null}
      <button className="secondary-link" type="button" disabled={busy || saving} onClick={() => onManual(spec || undefined)}>Use detailed editor</button>
      {onCancel ? <button className="secondary-link" type="button" disabled={saving} onClick={onCancel}>Cancel</button> : null}
    </div>
    {busy ? <p role="status" className={styles.pending}><span aria-hidden="true">✦</span> Turning your request into a small, reviewable draft. No call is starting.</p> : null}
    {error ? <div className={styles.error} role="alert"><p>{error}</p><p>Your existing draft is preserved. <ApiAwareLink href="/settings">Check the generation connection</ApiAwareLink> or use the detailed editor.</p></div> : null}
    {spec ? <section className={styles.review} aria-label="Review proposed test">
      <div className={styles.reviewHeading}><div><p className="eyebrow">Proposed · not saved</p><h3 ref={questions.length ? undefined : resultRef} tabIndex={-1}>{spec.title}</h3></div>
        <span>{spec.scenarios.length} {spec.scenarios.length === 1 ? 'case' : 'cases'}</span></div>
      {outdated ? <p role="status" className={styles.warning}>Your request changed. Update the draft before approving it.</p> : null}
      {previous ? <details className={styles.changes}><summary>Review changes · {changes.length}</summary>
        {changes.length ? <ul>{changes.map((change, index) => <li key={index}>{change}</li>)}</ul> : <p>No material changes.</p>}
        <button type="button" className="secondary-link" disabled={busy || saving} onClick={() => {
          setDraft(previous); setRequest(previous.request); setAnswers(previous.answers); setPrevious(null); setChanges([]); setQuestions([]);
        }}>Undo last update</button>
      </details> : null}
      {draft.result.assumptions.length ? <aside className={styles.warning}><strong>Suggested assumptions—please check</strong><ul>{draft.result.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul></aside> : null}
      <div className={styles.rules}>
        <RuleList title="Agent should" rules={spec.required_behaviors} onChange={(rules) => editSpec({ ...spec, required_behaviors: rules })} disabled={busy || saving} />
        <RuleList title="Must not" rules={spec.forbidden_behaviors} onChange={(rules) => editSpec({ ...spec, forbidden_behaviors: rules })} disabled={busy || saving} />
      </div>
      <p className={styles.boundary}><strong>Allowed assistance</strong> {spec.permissible_behavior}</p>
      {scope === 'set' ? <p className={styles.note}>Dedicated cases for {new Set(spec.scenarios.map((item) => item.behavior_id)).size} of {spec.required_behaviors.length + spec.forbidden_behaviors.length} rules. Other rules remain policy context, not dedicated test coverage.</p> : null}
      <div className={styles.cases}>{spec.scenarios.map((item, index) => <article className={styles.case} key={item.id}>
        <header><strong>{item.title}</strong><span>{item.variant}</span></header>
        {scope === 'set' ? <p><b>Focused check</b>{[...spec.required_behaviors, ...spec.forbidden_behaviors].find((rule) => rule.id === item.behavior_id)?.label || 'Not specified'}</p> : null}
        <p><b>Caller does</b>{item.steps?.join('\n')}</p><p><b>Expected outcome</b>{item.expected_outcome}</p>
        <details><summary>Edit this case</summary>
          <label>Caller instructions<textarea rows={3} disabled={busy || saving} value={item.steps?.join('\n') || ''} onChange={(event) => editSpec({ ...spec, scenarios: spec.scenarios.map((entry, candidate) => candidate === index ? { ...entry, steps: event.target.value.split('\n') } : entry) })} /></label>
          <label>Expected outcome<textarea rows={2} disabled={busy || saving} value={item.expected_outcome || ''} onChange={(event) => editSpec({ ...spec, scenarios: spec.scenarios.map((entry, candidate) => candidate === index ? { ...entry, expected_outcome: event.target.value } : entry) })} /></label>
        </details>
      </article>)}</div>
      <div className={styles.evidence}><strong>What we’ll need to verify</strong>
        <span>{spec.evidence_requirements?.map((item) => item.replaceAll('_', ' ')).join(' · ') || 'Not specified'}</span>
        <small>Missing tool/state evidence leaves an operational outcome unverified. A spoken claim is not a receipt.</small></div>
      <div className={styles.chips} aria-label="Refine draft">{[
        ['Add interruptions', 'Also test a caller interrupting and correcting the request.'],
        ['Make it harder', 'Make the caller pressure more challenging without changing the policy.'],
      ].map(([label, instruction]) => <button key={label} type="button" disabled={busy || saving}
        onClick={() => setRequest((current) => `${current}\n${instruction}`)}>{label}</button>)}</div>
      <details><summary>Edit title, task and boundary</summary><div className={styles.details}>
        <label>Title<input disabled={busy || saving} value={spec.title} onChange={(event) => editSpec({ ...spec, title: event.target.value })} /></label>
        <label>Task<textarea disabled={busy || saving} value={spec.objective} onChange={(event) => editSpec({ ...spec, objective: event.target.value })} /></label>
        <label>Allowed assistance<textarea disabled={busy || saving} value={spec.permissible_behavior || ''} onChange={(event) => editSpec({ ...spec, permissible_behavior: event.target.value })} /></label>
      </div></details>
      <footer className={styles.footer}><p>Approval saves this reviewed {scope === 'case' ? 'case' : 'version and publishes its cases'}. It does not start a call.</p>
        <button type="button" className="primary-link" disabled={busy || saving || outdated || Boolean(questions.length) || !validDraft}
          onClick={() => void approve()}>{saving ? 'Saving reviewed test…' : scope === 'case' ? 'Approve and create case' : 'Approve test set and choose agent'}</button>
      </footer>
      <small className={styles.note}>Drafted with {draft.result.model}. Suggestions are not verified policy. No model calls happen while you type.</small>
    </section> : null}
  </section>;
}

function RuleList({ title, rules, onChange, disabled }: { title: string; rules: EditableAssertSpec['required_behaviors']; onChange: (rules: EditableAssertSpec['required_behaviors']) => void; disabled: boolean }) {
  return <section aria-label={title}><h4>{title}</h4>{rules.length ? <ul>{rules.map((rule, index) => <li key={rule.id}>
    <strong>{rule.label}</strong>{rule.description && rule.description !== rule.label ? <p>{rule.description}</p> : null}
    <details><summary>Edit rule</summary><label>Rule<input disabled={disabled} value={rule.label} onChange={(event) => onChange(rules.map((entry, candidate) => candidate === index ? { ...entry, label: event.target.value, description: event.target.value, source_quote: '' } : entry))} /></label></details>
  </li>)}</ul> : <p>None proposed.</p>}</section>;
}
