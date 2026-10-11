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
