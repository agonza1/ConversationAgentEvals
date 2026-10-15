import { expect, test } from '@playwright/test';

function fixture() {
  return {
    execution_run_id: 'exec-steven-reporting', mode: 'pipecat_webrtc', status: 'needs_review',
    user_id: 'demo-user', agent_id: 'steven', agent_name: 'Steven', tester_id: 'pipecat_tester',
    executor_id: 'waylo_livekit', suite_id: 'user-scenarios', scenario_ids: ['information-only'],
    conversations: [{
      conversation_id: 'steven-reporting-1', execution_run_id: 'exec-steven-reporting',
      scenario_id: 'information-only', scenario_title: 'Information only', suite_id: 'user-scenarios',
      mode: 'pipecat_webrtc', status: 'needs_review', verdict: 'needs_review', score: null,
      turns: [{ turn_index: 1, speaker: 'Caller', text: 'Tell me the warranty.' },
        { turn_index: 2, speaker: 'Agent', text: 'It has a one-year warranty.' }],
      action_trace: [], final_state: {},
      audio_session: { transport: 'waylo_livekit' },
      latency_marks: [1910, 3098].map((latency_ms, index) => ({
        event_id: `caller-${index + 1}/observer-latency`, turn_id: `caller-${index + 1}`,
        kind: 'cae_livekit_queue_end_to_received_speech', latency_ms,
        clock_origin: 'cae_monotonic_since_transport_creation',
      })),
      metrics_summary: { verdict: 'needs_review', score: null, turn_count: 2,
        interruption_count: 0, call_resolution_success: 0,
        latency: { count: 2, avg_ms: 2504, p90_ms: 3098, outlier_count: 0 } },
      evidence_diagnostics: {
        interruption: { status: 'observed', reported_true: 0, reported_false: 3,
          controlled_attempts: 0, handling_status: 'not_tested' },
        response_latency: { worker_observations: [1652, 2964].map((value_ms, index) => ({
          event_id: `worker-${index}`, turn_index: index + 1, value_ms, clock_origin: 'waylo_worker_utc',
        })) },
        speech_boundary: { status: 'needs_review', comparisons: [{
          reference_text: 'Tell me the warranty.', target_asr_final: 'Tell me warranty.',
          alignment: 'ordered_one_to_one_provisional', scoreable: false,
        }] },
      },
      evidence_coverage: {
        conversation_experience: { status: 'partial', evaluation_status: 'needs_review',
          observed: ['audio', 'observer_response_latency', 'native_interruption_flags'], missing: ['transport_quality', 'verified_interruption_handling'] },
        speech_boundary: { status: 'partial', evaluation_status: 'needs_review',
          observed: ['tester_reference', 'target_asr_final_provisional_alignment'], missing: ['peer_delivery_ack'] },
        agent_execution: { status: 'partial', evaluation_status: 'needs_review',
          observed: ['tool_arguments', 'tool_results', 'turn_associations'], missing: ['policy_decisions'] },
        business_outcome: { status: 'partial', evaluation_status: 'needs_review',
          observed: ['request_list_final_readback'], missing: ['full_backend_state'] },
      },
    }],
  };
}

test('Waylo reporting separates observer/worker latency, provisional WER, native flags and four Cs', async ({ page }) => {
  const run = fixture();
  await page.addInitScript(() => localStorage.setItem('conversation-evals-demo-user', 'demo-user'));
  await page.route('**/api/**', (route) => route.fulfill({ json: route.request().url().includes('/runs/exec-steven-reporting') ? run : {} }));
  await page.goto('/runs/exec-steven-reporting');
  const observerTile = page.getByRole('button', { name: /Observed target response latency 2504ms/ });
  await expect(observerTile).toContainText('2 responses across the suite');
  await expect(page.getByRole('button', { name: /Interruption Detection Not tested/ })).toContainText('No deliberate barge-in');
  const coverage = page.getByLabel('Waylo evidence coverage');
  for (const name of ['Conversation · experience', 'Comprehension · speech boundary', 'Conduct · execution', 'Completion · business outcome']) {
    await expect(coverage).toContainText(name);
  }
  await expect(coverage).toContainText('Captured: tool arguments, tool results, turn associations');
  await expect(coverage).toContainText('Missing or unverified: policy decisions');
  await observerTile.click();
  await expect(page.getByLabel('Observer latency observations')).toContainText('caller-1: 1910ms');
  await expect(page.getByLabel('Worker latency observations')).toContainText('1652ms');
  await expect(page.getByLabel('Metric detail')).toContainText('Do not add or average');
  await page.getByRole('button', { name: /Caller ASR comparison 1 \/ 4/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('not an accepted grade');
  await expect(page.getByLabel('Per-turn word error rates')).toContainText('Waylo final ASR');
  await page.getByRole('button', { name: /Interruption Detection Not tested/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('that is not a barge-in test');
});

test('Waylo missing flags and unaligned speech stay unknown rather than zero', async ({ page }) => {
  const run = fixture();
  const row = run.conversations[0];
  row.latency_marks = [];
  row.evidence_diagnostics.interruption = { status: 'unknown', reported_true: 0, reported_false: 0,
    controlled_attempts: 0, handling_status: 'not_tested' };
  row.evidence_diagnostics.speech_boundary.comparisons = [];
  await page.addInitScript(() => localStorage.setItem('conversation-evals-demo-user', 'demo-user'));
  await page.route('**/api/**', (route) => route.fulfill({ json: route.request().url().includes('/runs/exec-steven-reporting') ? run : {} }));
  await page.goto('/runs/exec-steven-reporting');
  await expect(page.getByRole('button', { name: /Interruption Detection Not measured/ })).toContainText('telemetry was not captured');
  await page.getByRole('button', { name: /Caller ASR comparison Not measured/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('Provisional WER is unknown');
  await page.getByRole('button', { name: /Observed target response latency n\/a/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('Observer response timing is unknown');
  await expect(page.getByLabel('Worker latency observations')).toContainText('2964ms');
});

test('Waylo turn sequence does not invent a clock or shift caller latency onto greetings', async ({ page }) => {
  const run = fixture();
  const turns = [
    { turn_index: 1, speaker: 'Agent', text: 'Welcome to the store.', frame_metadata: { greeting: true } },
    { turn_index: 2, speaker: 'Caller', text: 'Tell me the warranty.', frame_metadata: { duration_ms: 1800 } },
    { turn_index: 3, speaker: 'Agent', text: 'It has a one-year warranty.',
      frame_metadata: { response_to_turn_ids: ['caller-1'] } },
    { turn_index: 4, speaker: 'Caller', text: 'What is the return policy?', frame_metadata: { duration_ms: 500 } },
    { turn_index: 5, speaker: 'Agent', text: 'Returns are accepted for thirty days.',
      frame_metadata: { response_to_turn_ids: ['caller-2'] } },
    { turn_index: 6, speaker: 'Agent', text: 'I heard both corrections.',
      frame_metadata: { response_to_turn_ids: ['caller-1', 'caller-2'], overlapping_response_window: true } },
    { turn_index: 7, speaker: 'Agent', text: 'An unassociated response.',
      frame_metadata: { transcript_source: 'waylo_final_transcript' } },
  ];
  run.conversations[0].turns = turns;
  // Mark order differs from conversation order; correlation must use IDs.
  run.conversations[0].latency_marks.reverse();
  await page.addInitScript(() => localStorage.setItem('conversation-evals-demo-user', 'demo-user'));
  await page.route('**/api/**', (route) => route.fulfill({ json: route.request().url().includes('/runs/exec-steven-reporting') ? run : {} }));
  await page.goto('/runs/exec-steven-reporting');
  const sequence = page.getByLabel('Waylo conversation turn sequence');
  await expect(sequence).toContainText('Ordered evidence, not an elapsed-time timeline');
  await expect(sequence.locator('.conversation-flow-chart')).toHaveCount(0);
  const rows = sequence.getByRole('listitem');
  await expect(rows.nth(0)).toContainText('Speech duration unknown · Greeting; no caller response latency');
  await expect(rows.nth(0)).not.toContainText('1910ms');
  await expect(rows.nth(1)).toContainText('Observed speech duration: 1.8s');
  await expect(rows.nth(2)).toContainText('Speech duration unknown · Observed response to caller-1: 1910ms');
  await expect(rows.nth(2)).not.toContainText('3098ms');
  await expect(rows.nth(4)).toContainText('Speech duration unknown · Observed response to caller-2: 3098ms');
  await expect(rows.nth(5)).toContainText('Individual response timing unknown');
  await expect(rows.nth(6)).toContainText('Individual response timing unknown');
});

test('zero caller edits are scoped to available references; missing ASR is not zero', async ({ page }) => {
  const run = fixture();
  const pair = run.conversations[0].evidence_diagnostics.speech_boundary.comparisons[0];
  pair.target_asr_final = pair.reference_text;
  await page.route('**/api/**', route => route.fulfill({ json: route.request().url().includes('/runs/exec-steven-reporting') ? run : {} }));
  await page.goto('/runs/exec-steven-reporting');
  await page.getByRole('button', { name: /Caller ASR comparison 0 \/ 4/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('Agent speech accuracy is not measured');
  pair.target_asr_final = '';
  await page.reload();
  await expect(page.getByRole('button', { name: /Caller ASR comparison Not measured/ })).toBeVisible();
});

test('legacy Waylo review requires explicit current-rule selection, not a silent backfill or model call', async ({ page }) => {
  const run = fixture();
  const row = run.conversations[0];
  Object.assign(row, { scenario_id: 'mike-lookup-failure', evaluation_findings: {},
    final_state: { request_list: [] }, action_trace: [{ name: 'search_documents', status: 'success' }] });
  const snapshot = { sha256: 'a'.repeat(64), contract: { title: 'Lookup failure', goal: 'No invented product after tool error',
    required_actions: ['search_documents'], forbidden_actions: ['create_order'], waylo_test: { setup: 'Inject API_UNAVAILABLE' } } };
  let saves = 0; let judges = 0;
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url()); const method = route.request().method();
    if (url.pathname.endsWith('/readiness')) return route.fulfill({ json: { ready: true, message: 'ASSERT ready' } });
    if (url.pathname.endsWith('/review-contract')) {
      if (method === 'POST') {
        saves++;
        expect(route.request().postDataJSON().contract_sha256).toBe(snapshot.sha256);
        Object.assign(row, { evaluation_findings: { scoring_mode: 'capture_only', evaluation_contract_snapshot: snapshot } });
        return route.fulfill({ json: run });
      }
      return route.fulfill({ json: { snapshot, source: 'current_scenario_not_historical' } });
    }
    if (url.pathname.endsWith('/judge')) {
      judges++;
      return route.fulfill({ json: { status: 'ready', judge_result: { agrees: true }, message: 'Fixture review completed', evidence_citations: [] } });
    }
    return route.fulfill({ json: url.pathname.includes('/runs/exec-steven-reporting') ? run : {} });
  });
  await page.goto('/runs/exec-steven-reporting');
  await page.getByRole('button', { name: /Verified Resolution Rate n\/a/ }).click();
  await expect(page.getByLabel('Resolution explanation')).toContainText('no scenario evaluation has run');
  await expect(page.getByLabel('Resolution explanation')).toContainText('requires a lookup error, but none was recorded');
  await expect(page.getByRole('button', { name: 'Save review rules first' })).toBeDisabled();
  await expect(page.getByLabel('Select review rules')).toContainText('not a recovered historical contract');
  expect(saves).toBe(0); expect(judges).toBe(0);
  await page.getByRole('button', { name: 'Use these rules for review' }).click();
  await expect(page.getByRole('button', { name: 'Review with LLM judge' })).toBeEnabled();
  expect(saves).toBe(1); expect(judges).toBe(0);
  await page.getByRole('button', { name: 'Review with LLM judge' }).click();
  await expect.poll(() => judges).toBe(1);
});
