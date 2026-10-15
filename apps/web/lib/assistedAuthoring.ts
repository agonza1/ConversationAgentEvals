import type { EditableAssertSpec } from './types';
import { getApiBase } from './api';

export type AuthoringScope = 'case' | 'set';
export interface AuthoringContext { label: string; text: string }
export const AUTHORING_CONTEXT_LIMIT = 16000;

export function prepareAuthoringContext(source?: AuthoringContext): { context?: AuthoringContext; omitted: number } {
  if (!source) return { omitted: 0 };
  const characters = Array.from(source.text);
  const omitted = Math.max(0, characters.length - AUTHORING_CONTEXT_LIMIT);
  const label = Array.from(source.label).slice(0, omitted ? 170 : 200).join('');
  return { context: { label: omitted ? `${label} (source excerpt)` : label,
    text: characters.slice(0, AUTHORING_CONTEXT_LIMIT).join('') }, omitted };
}
export interface AuthoringDraft {
  spec: EditableAssertSpec | null;
  questions: { id: string; question: string; options: string[] }[];
  assumptions: string[];
  provider: string;
  model: string;
}

export async function requestAuthoringDraft(payload: {
  request: string; scope: AuthoringScope; context?: AuthoringContext;
  answers: Record<string, string>; question_context?: Record<string, string>; current_spec?: EditableAssertSpec;
}, signal: AbortSignal): Promise<AuthoringDraft> {
  const response = await fetch(`${getApiBase()}/api/specs/assist`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not draft this test. Your work has not changed.');
  if (!data || !Array.isArray(data.questions) || !Array.isArray(data.assumptions) || (!data.spec && !data.questions.length)) {
    throw new Error('The generator returned an incomplete draft. Try again or use the detailed editor.');
  }
  return data;
}

export function approvedAuthoringSpec(spec: EditableAssertSpec): EditableAssertSpec {
  return { ...spec, generated_content_status: 'approved',
    required_behaviors: spec.required_behaviors.map((item) => ({ ...item, draft: false })),
    forbidden_behaviors: spec.forbidden_behaviors.map((item) => ({ ...item, draft: false })),
    scenarios: spec.scenarios.map((item) => ({ ...item, draft: false })),
    deterministic_checks: (spec.deterministic_checks || []).map((item) => ({ ...item, draft: false })),
  };
}

export function quickCaseFromDraft(spec: EditableAssertSpec) {
  if (spec.scenarios.length !== 1 || !spec.required_behaviors.length) throw new Error('A quick case needs exactly one caller case and at least one check.');
  const item = spec.scenarios[0];
  if (!item.steps?.[0]?.trim() || !item.expected_outcome?.trim()) throw new Error('Review the caller opening and expected outcome before creating the case.');
  return {
    title: item.title,
    simulated_user_prompt: [item.persona, ...(item.steps || [])].filter(Boolean).join('\n'),
    expected_output: item.expected_outcome,
    description: spec.objective,
    required_actions: spec.required_behaviors.map((rule) => rule.description || rule.label),
    forbidden_actions: spec.forbidden_behaviors.map((rule) => rule.description || rule.label),
    evidence_requirements: spec.evidence_requirements || ['transcript'],
    requirements: spec.requirements || '',
    permissible_behavior: spec.permissible_behavior || '',
    caller_steps: [...item.steps],
  };
}

export function authoringChanges(before: EditableAssertSpec, after: EditableAssertSpec): string[] {
  const changes: string[] = [];
  for (const key of ['title', 'role', 'objective', 'permissible_behavior'] as const) {
    if (before[key] !== after[key]) changes.push(`Changed ${key.replaceAll('_', ' ')}: ${after[key] || 'removed'}`);
  }
  for (const [key, name] of [['required_behaviors', 'required rule'], ['forbidden_behaviors', 'forbidden rule'], ['scenarios', 'case']] as const) {
    const oldItems = before[key];
    const newItems = after[key];
    const title = (item: typeof oldItems[number]) => 'label' in item ? item.label : item.title;
    for (const item of newItems) {
      const previous = oldItems.find((old) => old.id === item.id);
      if (!previous) changes.push(`Added ${name}: ${title(item)}`);
      else if (JSON.stringify(previous) !== JSON.stringify(item)) changes.push(`Updated ${name}: ${title(item)}`);
    }
    for (const item of oldItems) if (!newItems.some((next) => next.id === item.id)) changes.push(`Removed ${name}: ${title(item)}`);
  }
  if (JSON.stringify(before.evidence_requirements) !== JSON.stringify(after.evidence_requirements)) changes.push('Updated evidence requirements');
  return changes;
}
