"""One-shot PR 164 verification helper; excluded from the candidate tree."""
from pathlib import Path
import sys
import textwrap


def replace(path, old, new):
    file = Path(path)
    source = file.read_text(encoding='utf-8')
    if source.count(old) != 1:
        raise RuntimeError(f'Expected exactly one patch anchor in {path}: {old[:100]!r}')
    file.write_text(source.replace(old, new), encoding='utf-8')


def append(path, content):
    file = Path(path)
    file.write_text(file.read_text(encoding='utf-8') + '\n' + textwrap.dedent(content), encoding='utf-8')


def tests():
    append('apps/api/tests/test_upstream_assert_generation.py', r'''

@pytest.mark.parametrize('failure', [
    'artifact_directory', 'request_write', 'worker_start', 'timeout',
    'worker_exit', 'malformed_output', 'partial_output',
])
def test_failed_generation_refunds_real_shared_credits_and_releases_slot(native_generation, monkeypatch, failure):
    from app.services import judge_budget
    root, _, _, child = native_generation
    ledger = root / 'credits.json'
    monkeypatch.setattr(judge_budget, '_judge_spend_path', lambda: ledger)
    monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', '60')
    monkeypatch.setenv('LLM_JUDGE_RESERVED_DAILY_CREDITS', '0')
    monkeypatch.setattr(generation, '_reserve_judge_credits', judge_budget._reserve_judge_credits)
    assert judge_budget._reserve_judge_credits(judge_budget._judge_spend_control(), credits=10)[0]
    artifact_root = root / 'generation'
    calls = []

    def worker(command, **kwargs):
        calls.append(command)
        if failure == 'worker_start':
            raise OSError('private worker startup detail')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('worker', 300)
        if failure == 'worker_exit':
            return SimpleNamespace(returncode=1)
        response = child(command, **kwargs)
        if failure == 'malformed_output':
            (artifact_root / 'test_set.jsonl').write_text('{invalid', encoding='utf-8')
        elif failure == 'partial_output':
            (artifact_root / 'summary.json').write_text(json.dumps({'errored_count': 1, 'saved_count': 3}), encoding='utf-8')
        return response

    with monkeypatch.context() as failing:
        failing.setattr(generation.subprocess, 'run', worker)
        if failure == 'artifact_directory':
            artifact_root.write_text('Not a directory', encoding='utf-8')
        elif failure == 'request_write':
            original_write = Path.write_text
            def fail_request(path, *args, **kwargs):
                if path.name == 'request.json':
                    raise OSError('private artifact path detail')
                return original_write(path, *args, **kwargs)
            failing.setattr(Path, 'write_text', fail_request)
        with pytest.raises(SpecGenerationFailed) as exc:
            generation.generate_assert_case_drafts(design(), selected=['payment!'],
                samples_per_behavior=3, artifact_root=artifact_root)
        assert 'private' not in str(exc.value)
    assert generation._ACTIVE == 0
    assert judge_budget._load_judge_spend()['spent'] == 10
    if failure in {'artifact_directory', 'request_write'}:
        assert calls == []
    # A failure must not lock out either a later generation or a judge request.
    result = generation.generate_assert_case_drafts(design(), selected=['payment!'],
        samples_per_behavior=3, artifact_root=root / 'retry')
    assert len(result['scenarios']) == 3
    assert judge_budget._load_judge_spend()['spent'] == 40
    assert judge_budget._reserve_judge_credits(judge_budget._judge_spend_control(), credits=10)[0]
    assert judge_budget._load_judge_spend()['spent'] == 50


def test_rejected_generation_does_not_refund_other_work(native_generation, monkeypatch):
    from app.services import judge_budget
    root, _, calls, _ = native_generation
    monkeypatch.setattr(judge_budget, '_judge_spend_path', lambda: root / 'credits.json')
    monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', '30')
    monkeypatch.setenv('LLM_JUDGE_RESERVED_DAILY_CREDITS', '0')
    monkeypatch.setattr(generation, '_reserve_judge_credits', judge_budget._reserve_judge_credits)
    assert judge_budget._reserve_judge_credits(judge_budget._judge_spend_control(), credits=10)[0]
    with pytest.raises(SpecGenerationUnavailable, match='budget is exhausted'):
        generation.generate_assert_case_drafts(design(), selected=['payment!'],
            samples_per_behavior=3, artifact_root=root / 'rejected')
    assert judge_budget._load_judge_spend()['spent'] == 10
    assert generation._ACTIVE == 0
    assert calls == []
    assert not (root / 'rejected').exists()
''')
    replace('apps/web/tests/execution-launch.spec.ts',
        'resultCases?: boolean; active?: boolean',
        "resultCases?: boolean; active?: boolean; publicMedia?: 'pipecat_daily_webrtc' | 'signalwire_webrtc'")
    replace('apps/web/tests/execution-launch.spec.ts',
        "audio_session: { tester_status: index === 1 ? 'failed' : 'completed' },",
        "audio_session: options.publicMedia ? { transport: options.publicMedia, closed: true, proof: true }\n      : { tester_status: index === 1 ? 'failed' : 'completed' },")
    append('apps/web/tests/execution-launch.spec.ts', r'''

for (const [agentId, publicMedia] of [
  ['public-pipecat', 'pipecat_daily_webrtc'],
  ['public-signalwire', 'signalwire_webrtc'],
] as const) {
  test(`${agentId} displays media completion independently of policy findings`, async ({ page }) => {
    await mockReviewedVoiceSuite(page, { resultCases: true, publicMedia });
    await page.goto(`/runs?suite_id=reviewed-assert-suite&run_scope=suite&agent_id=${agentId}`);
    const launch = page.getByRole('region', { name: 'Launch agent run' });
    await launch.getByRole('button', { name: 'Run 3 voice tests' }).click();
    const rows = launch.getByLabel('Execution conversations');
    for (const index of [1, 3]) {
      const row = rows.locator('article').filter({ hasText: `Address correction ${index}` });
      await expect(row).toContainText('Call: completed');
      await expect(row.getByLabel('Voice test result')).toContainText(index === 3 ? 'fail' : 'needs review');
    }
    await expect(rows.locator('article').filter({ hasText: 'Address correction 2' })).toContainText('Call: incomplete');
    await expect(launch).not.toContainText('All tests passed');
  });
}
''')
    Path('apps/web/tests/voice-call-status.test.cjs').write_text(textwrap.dedent(r'''
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
''').lstrip(), encoding='utf-8')


def code():
    file = 'apps/api/app/services/upstream_assert_generation.py'
    replace(file,
        'from app.services.judge_budget import _judge_spend_control, _reserve_judge_credits',
        'from app.services.judge_budget import _judge_spend_control, _reserve_judge_credits, _refund_judge_credits')
    source = Path(file).read_text(encoding='utf-8')
    begin = source.index('        root = Path(artifact_root or REPO_ROOT')
    end = source.index('\n\n\ndef _artifact_path', begin)
    body = source[begin:end]
    body = body.replace("        return {'scenarios':", "        result = {'scenarios':", 1)
    body += '\n        returned_drafts = True\n        return result\n'
    replacement = '        returned_drafts = False\n        try:\n'
    replacement += textwrap.indent(body, '    ')
    replacement += (
        '        except OSError as exc:\n'
        "            raise SpecGenerationFailed('ASSERT generation could not prepare artifacts or start its worker; no drafts were imported.') from exc\n"
        '        finally:\n'
        '            # Admission credits represent usable work, not an exact provider bill.\n'
        '            # Release the reservation once on every failure, including setup errors.\n'
        '            if not returned_drafts:\n'
        '                _refund_judge_credits(spend, credits=credits)\n'
    )
    Path(file).write_text(source[:begin] + replacement + source[end:], encoding='utf-8')
    replace('apps/web/components/BenchmarkRunner.tsx',
        "  if (conversation.status === 'failed') return 'incomplete';\n  if (conversation.status === 'completed') return 'completed';\n  return 'completion unverified';",
        "  // Public executors have media proof but no local tester_status. Their\n  // conversation status reflects policy evaluation, not call completion.\n  if (conversation.audio_session?.closed === true && conversation.audio_session?.proof === true) return 'completed';\n  return 'completion unverified';")
    replace('.github/workflows/ci.yml',
        'apps/web/tests/next-proxy-timeout.test.cjs',
        'apps/web/tests/next-proxy-timeout.test.cjs apps/web/tests/voice-call-status.test.cjs')
    append('docs/assert-test-generation.md', '''

### Failed-request admission credits

A generation reservation is retained only when a complete, validated set of drafts is returned. Local artifact errors, worker startup failures, timeouts, unsuccessful worker exits, and rejected native artifacts release the request's shared generation/judge credit reservation. A request rejected before reservation does not refund other work. These are estimated admission credits: a refund does not reverse an external provider charge for model calls already attempted. The existing single-process limitation still applies.

### Call completion versus evaluation

The voice results view derives call completion from local tester completion or closed, proven public media capture, never from the policy verdict. A completed call can fail automatic checks or require semantic review. Missing media proof remains completion-unverified, and capture errors remain incomplete. Saved ASSERT proposals are still separate from confirmation and application.
''')


if __name__ == '__main__':
    {'tests': tests, 'code': code}[sys.argv[1]]()
