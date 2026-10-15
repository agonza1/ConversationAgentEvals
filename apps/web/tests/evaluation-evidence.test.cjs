const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const Module = require('node:module');
const path = require('node:path');
const filename = path.resolve('apps/web/lib/evaluationEvidence.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const helperName = path.resolve('apps/web/lib/operationalVerification.ts');
const helper = new Module(helperName, module);
helper._compile(ts.transpileModule(fs.readFileSync(helperName, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, helperName);
const loaded = new Module(filename, module);
const originalRequire = loaded.require.bind(loaded);
loaded.require = name => name === './operationalVerification' ? helper.exports : originalRequire(name);
loaded._compile(compiled, filename);
const { evaluationEvidence, reviewEvidenceStatus } = loaded.exports;
const transcriptReport = () => ({ verdict: 'needs_review', overall_score: 100, semantic_review_required: true,
  scoring_mode: 'transcript', action_trace: '', final_state: {}, transcript: 'Agent: Your address was updated.',
  evidence_audit_summary: { transcript_present: true, action_trace_present: false, final_state_present: false } });

const operational = () => ({ schema: 'cae-billing-simulation-v1', context: 'simulation', status: 'verified',
  checks: { identity_verified: true, address_committed: true, stored_address_matches_request: true,
    update_read_back: true, invoice_effect_explained: true }, tool_calls: 4, receipt_id: 's:billing-update:1' });

test('verified is the result and simulation is context, while policy review stays separate', () => {
  const view = evaluationEvidence({ verdict: 'needs_review', semantic_review_required: true,
    evaluation_scope: { synthetic: true }, operational_verification: operational() });
  assert.equal(view.headline, 'Verified'); assert.match(view.sourceLabel, /Simulation/);
  assert.equal(view.reviewRequired, true); assert.match(view.explanation, /separate/);
});
test('simulation flags, failed checks or missing receipts cannot promote an outcome', () => {
  const missing = operational(); missing.receipt_id = '';
  assert.equal(helper.exports.operationalVerification(missing).verified, false);
  for (const key of Object.keys(operational().checks)) {
    const value = operational(); value.checks[key] = false;
    assert.equal(helper.exports.operationalVerification(value).verified, false);
  }
  assert.equal(helper.exports.operationalVerification({ status: 'verified', simulation: true }).verified, false);
  const view = evaluationEvidence({ verdict: 'fail', operational_verification: operational() });
  assert.equal(view.headline, 'Evaluation reported a failure');
});

test('a perfect transcript score is not verified execution and exports do not imply imported evidence', () => {
  const report = transcriptReport(); report.ietf_vcon_export = { attachments: [{ body: { tool_events: ['invented'] } }] };
  const view = evaluationEvidence(report);
  assert.equal(view.headline, 'Outcome unverified'); assert.equal(view.score, 100);
  assert.equal(view.imported, false); assert.equal(view.trace, 'missing'); assert.equal(view.state, 'missing');
  assert.match(view.explanation, /Spoken confirmations do not prove/);
});
test('historical analysis and high legacy pass scores cannot invent tool/state presence', () => {
  const report = transcriptReport(); report.verdict = 'pass'; report.semantic_review_required = false;
  report.vcon_export = { source_format: 'vcon', analysis: [{ score: 100, completed_actions: ['update address'] }] };
  const view = evaluationEvidence(report);
  assert.equal(view.imported, true); assert.equal(view.headline, 'Outcome unverified');
});
test('unknown legacy coverage stays unknown rather than absent, zero or success', () => {
  const view = evaluationEvidence({ overall_score: 100, verdict: 'pass' });
  assert.equal(view.trace, 'unknown'); assert.equal(view.state, 'unknown'); assert.equal(view.transcript, 'unknown');
  assert.equal(view.headline, 'Verification not established');
});
test('a request-only trace is supplied evidence, not a successful result or authenticated receipt', () => {
  const view = evaluationEvidence({ verdict: 'needs_review', action_trace: [{ tool: 'update_address', status: 'requested' }], final_state: {}, transcript: 'Agent: Done.' });
  assert.equal(view.trace, 'present'); assert.equal(view.state, 'missing'); assert.equal(view.headline, 'Review required');
  assert.match(view.explanation, /request is not a successful result/); assert.match(view.explanation, /not independently authenticated/);
});
test('explicit audit absence outranks contradictory legacy raw values; source scope outranks audit', () => {
  const report = transcriptReport(); report.action_trace = [{ status: 'success' }];
  assert.equal(evaluationEvidence(report).trace, 'missing');
  report.evaluation_scope = { input_source: 'vcon', artifact_presence: { action_trace: true } };
  assert.equal(evaluationEvidence(report).trace, 'present'); assert.equal(evaluationEvidence(report).imported, true);
});
test('false completion state is present, not treated as empty or proof of success', () => {
  const view = evaluationEvidence({ final_state: { complete: false }, action_trace: '[]', verdict: 'fail', score: 0 });
  assert.equal(view.state, 'present'); assert.equal(view.trace, 'missing'); assert.equal(view.score, 0);
  assert.equal(view.headline, 'Evaluation reported a failure');
});
test('synthetic structured samples are never presented as real-world verification', () => {
  const view = evaluationEvidence({ verdict: 'pass', score: 100, vcon_intake_summary: { synthetic: true }, final_state: { complete: true } });
  assert.match(view.headline, /Synthetic evidence/); assert.equal(view.synthetic, true);
  const failure = evaluationEvidence({ verdict: 'fail', vcon_intake_summary: { synthetic: true } });
  assert.equal(failure.headline, 'Evaluation reported a failure'); assert.match(failure.sourceLabel, /Synthetic/);
});
test('judge status distinguishes pending, failed, absent and separate completion, never agrees=proof', () => {
  assert.equal(reviewEvidenceStatus(null), 'No completed review recorded');
  assert.equal(reviewEvidenceStatus({}, true), 'Review in progress');
  assert.equal(reviewEvidenceStatus({ status: 'blocked' }), 'Review unavailable — retry when ready');
  assert.equal(reviewEvidenceStatus({ status: 'ready' }), 'No completed review recorded');
  assert.equal(reviewEvidenceStatus({ status: 'ready', judge_result: { agrees: true } }), 'Review recorded separately');
});
test('non-finite scores never appear as meaningful diagnostic values', () => {
  for (const score of [NaN, Infinity, '100', null]) assert.equal(evaluationEvidence({ score }).score, null);
});
test('a plain transcript is text even when its content happens to be valid JSON', () => {
  for (const transcript of ['100', 'false', '[]']) {
    const view = evaluationEvidence({ transcript, action_trace: [], final_state: {} });
    assert.equal(view.transcript, 'present'); assert.equal(view.headline, 'Outcome unverified');
  }
});
test('legacy overall verdict aliases preserve explicit failures', () => {
  const view = evaluationEvidence({ overall: 'FAIL', transcript: 'Agent: Forbidden behavior.', action_trace: [], final_state: {} });
  assert.equal(view.failed, true); assert.equal(view.headline, 'Evaluation reported a failure');
});

test('structured call and conversation source labels do not collapse to transcript', () => {
  for (const [source, label] of Object.entries({ call: 'Structured call input', group_call: 'Group call input', groupCall: 'Group call input', conversation: 'Structured conversation input' })) {
    assert.equal(evaluationEvidence({ evaluation_scope: { input_source: source }, scoring_mode: 'transcript' }).sourceLabel, label);
    assert.equal(evaluationEvidence({ vcon_export: { source_format: source } }).sourceLabel, label);
  }
});
