const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');
const filename = path.resolve('apps/web/lib/assistedAuthoring.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const loaded = new Module(filename, module);
loaded.require = (name) => name === './api' ? { getApiBase: () => 'http://synthetic.invalid' } : require(name);
loaded._compile(compiled, filename);
const { quickCaseFromDraft, approvedAuthoringSpec, authoringChanges, requestAuthoringDraft, prepareAuthoringContext, AUTHORING_CONTEXT_LIMIT } = loaded.exports;
function draft() { return { title: 'Corrections', objective: 'Follow the latest caller request.', permissible_behavior: 'Collect requests only.',
  generated_content_status: 'draft', required_behaviors: [{ id: 'latest', label: 'Use latest', description: 'Use the corrected time.', draft: true }],
  forbidden_behaviors: [{ id: 'claim', label: 'No claim', description: 'Do not invent completion.', draft: true }],
  scenarios: [{ id: 'normal', title: 'Correction', steps: ['Use 3 PM.', 'Confirm afternoon.'], expected_outcome: 'Repeat 3 PM.', behavior_id: 'latest', draft: true }],
  evidence_requirements: ['transcript', 'final_state_or_action_trace'], requirements: 'Source policy', deterministic_checks: [] }; }

test('quick-case conversion retains approved caller steps, rule descriptions and evidence, not a rewritten scenario', () => {
  assert.deepEqual(quickCaseFromDraft(draft()), { title: 'Correction', simulated_user_prompt: 'Use 3 PM.\nConfirm afternoon.', spoken_opener: 'Use 3 PM.',
    expected_output: 'Repeat 3 PM.', description: 'Follow the latest caller request.', required_actions: ['Use the corrected time.'],
    forbidden_actions: ['Do not invent completion.'], evidence_requirements: ['transcript', 'final_state_or_action_trace'], requirements: 'Source policy', permissible_behavior: 'Collect requests only.', caller_steps: ['Use 3 PM.', 'Confirm afternoon.'] });
});
test('approval creates a new snapshot without mutating the draft or weakening case/rule identities', () => {
  const original = draft(); const approved = approvedAuthoringSpec(original);
  assert.equal(approved.generated_content_status, 'approved');
  assert.equal(approved.scenarios[0].draft, false); assert.equal(approved.required_behaviors[0].draft, false);
  assert.equal(original.generated_content_status, 'draft'); assert.equal(original.scenarios[0].draft, true);
  assert.equal(approved.scenarios[0].behavior_id, 'latest');
});
test('quick cases reject missing caller steps or more than one case', () => {
  const value = draft(); value.scenarios[0].steps = [];
  assert.throws(() => quickCaseFromDraft(value), /caller opening/);
  value.scenarios.push(value.scenarios[0]); assert.throws(() => quickCaseFromDraft(value), /exactly one/);
});
test('change review identifies added, changed and removed content without mutating either snapshot', () => {
  const before = draft(); const after = structuredClone(before);
  after.title = 'Revised'; after.role = 'Corrected support agent'; after.scenarios[0].expected_outcome = 'Revised outcome.'; after.forbidden_behaviors = [];
  const changes = authoringChanges(before, after);
  assert.ok(changes.some((line) => line.startsWith('Changed title:')));
  assert.ok(changes.some((line) => line.startsWith('Changed role:')));
  assert.ok(changes.some((line) => line.startsWith('Updated case:')));
  assert.ok(changes.some((line) => line.startsWith('Removed forbidden rule:')));
  assert.equal(before.title, 'Corrections');
});
test('explicit draft request uses the selected API and abort signal, never saves or launches a run', async () => {
  const old = global.fetch; const calls = [];
  global.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ spec: draft(), questions: [], assumptions: [] }) }; };
  try {
    const controller = new AbortController();
    await requestAuthoringDraft({ request: 'Test corrected times.', scope: 'case', answers: {} }, controller.signal);
    assert.equal(calls.length, 1); assert.equal(calls[0].url, 'http://synthetic.invalid/api/specs/assist');
    assert.equal(calls[0].options.signal, controller.signal);
  } finally { global.fetch = old; }
});

test('source excerpts are bounded by Unicode characters and explicitly labelled, without mutating the source', () => {
  const source = { label: 'A very long policy title '.repeat(20), text: '😀'.repeat(AUTHORING_CONTEXT_LIMIT + 12) };
  const prepared = prepareAuthoringContext(source);
  assert.equal(Array.from(prepared.context.text).length, AUTHORING_CONTEXT_LIMIT);
  assert.equal(prepared.omitted, 12); assert.ok(prepared.context.label.includes('source excerpt'));
  assert.ok(prepared.context.label.length <= 200);
  assert.equal(Array.from(source.text).length, AUTHORING_CONTEXT_LIMIT + 12);
  assert.deepEqual(prepareAuthoringContext(), { omitted: 0 });
});
