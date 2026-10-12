const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

// Execute the actual production helper, extracting its AST rather than a copy.
const filename = path.join(__dirname, '../components/BenchmarkRunner.tsx');
const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declaration = source.statements.find((item) => ts.isFunctionDeclaration(item) && item.name?.text === 'voiceCallStatus');
assert.ok(declaration, 'voiceCallStatus must remain an independently testable helper');
const compiled = ts.transpileModule(`${declaration.getText(source)}\nmodule.exports = voiceCallStatus;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;
const context = { module: { exports: {} } };
vm.runInNewContext(compiled, context);
const voiceCallStatus = context.module.exports;

const queueDeclaration = source.statements.find((item) => ts.isFunctionDeclaration(item) && item.name?.text === 'isQueuedVoiceTargetId');
assert.ok(queueDeclaration, 'queued voice classification must remain independently testable');
const queueCompiled = ts.transpileModule(`${queueDeclaration.getText(source)}\nmodule.exports = isQueuedVoiceTargetId;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;
const queueContext = { module: { exports: {} } };
vm.runInNewContext(queueCompiled, queueContext);
const isQueuedVoiceTargetId = queueContext.module.exports;

for (const target of ['waylo', 'builtin_sample_voice', 'pipecat_public_demo', 'signalwire_holy_guacamole']) {
  test(`${target} uses queued voice launch guards, serial calls and queue limits`, () => {
    assert.equal(isQueuedVoiceTargetId(target), true);
  });
}
for (const target of [undefined, null, 'openai_codex', 'voice_fixture', 'browser_webrtc_agent', 'sip_agent', 'phone_agent']) {
  test(`${target} does not inherit queued voice behavior`, () => {
    assert.equal(isQueuedVoiceTargetId(target), false);
  });
}

let launchDeclaration;
function findLaunchDeclaration(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === 'onLaunchExecution') launchDeclaration = node;
  ts.forEachChild(node, findLaunchDeclaration);
}
findLaunchDeclaration(source);
const activeDeclaration = source.statements.find((item) => ts.isFunctionDeclaration(item) && item.name?.text === 'isActiveExecutionStatus');
assert.ok(launchDeclaration && activeDeclaration, 'the actual launch guard must remain testable');
const launchCompiled = ts.transpileModule(`${activeDeclaration.getText(source)}\n${launchDeclaration.getText(source)}\nmodule.exports = onLaunchExecution;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;

for (const status of ['queued', 'running']) {
  test(`Waylo launch handler refuses a second ${status} call independently of the disabled button`, async () => {
    const messages = [];
    let posts = 0;
    const launchContext = {
      module: { exports: {} }, selectedSuite: { id: 'fixture-suite' },
      ensureDemoIdentity: () => ({ userId: 'fixture-user', projectId: 'fixture-project' }),
      matchingProductProjects: [], productProjectId: null,
      selectedScoreAgent: { target: 'waylo' }, needsWayloConnection: false,
      requestedSuiteScope: false, voiceQueueLimitError: null, temporaryWayloTarget: false,
      queuedVoiceTarget: isQueuedVoiceTargetId('waylo'), executionRun: { status },
      setExecutionMessage: (message) => messages.push(message),
      createExecutionRun: () => { posts += 1; throw new Error('A duplicate call must not be posted.'); },
    };
    vm.runInNewContext(launchCompiled, launchContext);
    assert.equal(await launchContext.module.exports(), null);
    assert.equal(posts, 0);
    assert.deepEqual(messages, ['Wait for this voice run to finish before starting another from this page.']);
  });
}

for (const transport of ['pipecat_daily_webrtc', 'signalwire_webrtc']) {
  for (const status of ['completed', 'failed', 'needs_review']) {
    test(`${transport}: completed media remains completed with ${status} evaluation status`, () => {
      assert.equal(voiceCallStatus({ status, audio_session: { transport, closed: true, proof: true } }), 'completed');
    });
  }
}
for (const status of ['completed', 'failed', 'needs_review']) {
  for (const audio_session of [undefined, {}, { closed: true }, { proof: true }, { closed: false, proof: true }, { closed: true, proof: false }, { closed: 'true', proof: 'true' }]) {
    test(`${status}: incomplete or absent media proof ${JSON.stringify(audio_session)} does not establish completion`, () => {
      assert.equal(voiceCallStatus({ status, audio_session }), 'completion unverified');
    });
  }
}
for (const status of ['queued', 'running']) {
  test(`active execution remains ${status} despite partial media fields`, () => {
    assert.equal(voiceCallStatus({ status, audio_session: { closed: true, proof: true } }), status);
  });
}
for (const tester_status of ['failed', 'needs_review']) {
  test(`tester ${tester_status} takes precedence over closed media`, () => {
    assert.equal(voiceCallStatus({ status: 'completed', audio_session: { tester_status, closed: true, proof: true } }), 'incomplete');
  });
}
test('an execution error takes precedence over media proof', () => {
  assert.equal(voiceCallStatus({ status: 'completed', error: 'ASR timeout', audio_session: { closed: true, proof: true } }), 'incomplete');
});
test('a completed local tester does not inherit a policy failure', () => {
  assert.equal(voiceCallStatus({ status: 'failed', audio_session: { tester_status: 'completed' } }), 'completed');
});
