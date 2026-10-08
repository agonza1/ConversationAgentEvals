import type { JudgeProposedEvaluation } from './execution';

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : {};
const text = (value: unknown): string | null => typeof value === 'string' && value.trim() ? value : null;
const scalar = (value: unknown): string | number | boolean | null => typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value)) ? value : null;
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const identifier = (value: unknown): string | null => typeof value === 'string' && /^[a-zA-Z0-9_.:/-]{1,160}$/.test(value) && !value.includes('://') && !value.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(value) && !/(^|\/)\.{1,2}(\/|$)/.test(value) && !/(^|\/)(artifacts|storage|apps|node_modules|\.codex)(\/|$)/i.test(value) && !/(client[_-]?secret|secret[_-]?access[_-]?key|secret|password|token|private[_-]?key|api[_-]?key)[:=]/i.test(value) && !/\/(Users|home|workspace|private|tmp|opt|app|root|var|mnt|Volumes|etc|srv)\//.test(value) ? value : null;
const hash = (value: unknown, length: number): string | null => typeof value === 'string' && new RegExp(`^[a-f0-9]{${length}}$`).test(value) ? value : null;

export interface DimensionView {
  name: string; value: string | number | boolean | null; outcome: string;
  justification: string | null; ordinalLabel: string | null; hasScale: boolean;
  rubric: Array<{ value: string | number; label: string }>;
}
export interface BehaviorView {
  name: string; outcome: string; relevant: boolean | null; violated: boolean | null;
  confidence: string | number | boolean | null; reasoning: string | null; references: string[];
}
export interface AssertReviewView {
  id: string; status: string; statusLabel: string; dateLabel: string; timestamp: number | null;
  model: string | null; version: string | null; evidenceLabel: string; rationale: string | null;
  dimensions: DimensionView[]; behaviors: BehaviorView[]; citations: string[];
  technical: Array<{ label: string; value: string | null }>;
  proposal: JudgeProposedEvaluation | null;
}

export function normalizeAssertReview(value: unknown): AssertReviewView | null {
  const review = object(value); const result = object(review.judge_result); const provenance = object(result.provenance);
  if ((provenance.engine !== 'assert' && review.provider !== 'assert-ai') || typeof review.review_id !== 'string' || !review.review_id) return null;
  const dimensions = object(provenance.dimensions), applicability = object(provenance.dimension_applicability);
  const justification = object(provenance.dimension_justifications), scales = object(provenance.dimension_scales);
  const names = [...new Set([...Object.keys(dimensions), ...Object.keys(applicability), ...Object.keys(justification), ...Object.keys(scales)])];
  const dimensionViews = names.map((name): DimensionView => {
    const value = scalar(dimensions[name]); const scale = object(scales[name]);
    const rubric = scale.type === 'ordinal' && Array.isArray(scale.values) ? scale.values.flatMap((entry) => {
      const item = object(entry);
      return (typeof item.value === 'string' || (typeof item.value === 'number' && Number.isFinite(item.value))) && typeof item.label === 'string'
        ? [{ value: item.value, label: item.label }] : [];
    }) : [];
    const match = rubric.filter((entry) => typeof entry.value === typeof value && entry.value === value);
    return { name, value, outcome: applicability[name] === false ? 'Not applicable'
      : value === true ? 'Flagged' : value === false ? 'Clear' : value === null ? 'Unavailable' : 'Recorded value',
      justification: text(justification[name]), ordinalLabel: match.length === 1 ? match[0].label : null,
      hasScale: scale.type === 'ordinal', rubric };
  });
  const nodes = Array.isArray(provenance.node_judgments) ? provenance.node_judgments : [];
  const behaviors = nodes.map((raw): BehaviorView => {
    const node = object(raw); const relevant = typeof node.relevant === 'boolean' ? node.relevant : null;
    const violated = typeof node.violated === 'boolean' ? node.violated : null;
    return { name: text(node.node_name) || 'Behavior name unavailable', relevant, violated,
      outcome: relevant === false ? 'Not relevant' : relevant === true && violated === true ? 'Flagged'
        : relevant === true && violated === false ? 'Clear' : 'Unavailable',
      confidence: scalar(node.confidence), reasoning: text(node.reasoning), references: [
        ...(Array.isArray(node.evidence_turns) ? node.evidence_turns.map((turn) =>
          typeof turn === 'number' && Number.isInteger(turn) && turn >= 0 ? `Judge turn ${turn}`
            : `Unsupported judge-turn reference: ${scalar(turn) === null ? 'Unavailable' : String(scalar(turn))}`)
          : node.evidence_turns === undefined ? [] : ['Judge-turn references unavailable (malformed field)']),
        ...strings(node.evidence_references),
      ] };
  }).map((node, index) => ({ node, index, rank: node.outcome === 'Flagged' ? 0 : node.outcome === 'Clear' ? 1 : 2 }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index).map((entry) => entry.node);
  const parsedDate = typeof review.created_at === 'string' && review.created_at.trim() ? Date.parse(review.created_at) : NaN;
  const timestamp = Number.isFinite(parsedDate) ? parsedDate : null;
  const proposal = object(result.proposed_evaluation);
  const validProposal: JudgeProposedEvaluation | null = (proposal.verdict === 'pass' || proposal.verdict === 'fail' || proposal.verdict === 'needs_review') && typeof proposal.summary === 'string'
    ? { verdict: proposal.verdict, summary: proposal.summary, corrected_findings: strings(proposal.corrected_findings), remaining_gaps: strings(proposal.remaining_gaps) } : null;
  const version = typeof provenance.assert_version === 'string' && /^\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?$/.test(provenance.assert_version) ? provenance.assert_version : null;
  const model = identifier(review.model);
  const status = typeof review.status === 'string' ? review.status : 'unknown';
  return { id: review.review_id, status,
    statusLabel: status === 'pending_confirmation' ? 'Pending confirmation' : status === 'applied' ? 'Applied' : status === 'superseded' ? 'Superseded' : 'Status unknown',
    timestamp, dateLabel: timestamp === null ? 'Recorded time unavailable' : new Date(timestamp).toLocaleString(),
    model, version, evidenceLabel: provenance.evidence_level === 'gray_box' ? 'Trace-backed evidence' : provenance.evidence_level === 'partial_structured' ? 'Partially structured evidence' : provenance.evidence_level === 'black_box' ? 'Transcript-only evidence' : 'Evidence level unavailable',
    rationale: text(result.rationale), proposal: validProposal, dimensions: dimensionViews, behaviors, citations: strings(review.evidence_citations),
    technical: [{ label: 'Input fingerprint', value: hash(provenance.input_fingerprint, 16) },
      { label: 'Score SHA-256', value: hash(provenance.score_sha256, 64) },
      { label: 'Output SHA-256', value: hash(review.output_sha256, 64) },
      { label: 'ASSERT version', value: version }, { label: 'Judge model', value: model }],
  };
}

export function sortedAssertReviews(values: unknown): AssertReviewView[] {
  if (!Array.isArray(values)) return [];
  return values.map(normalizeAssertReview).filter((value): value is AssertReviewView => value !== null)
    .map((review, index) => ({ review, index })).sort((a, b) =>
      (b.review.timestamp ?? -Infinity) - (a.review.timestamp ?? -Infinity) || b.index - a.index)
    .map((entry) => entry.review);
}
