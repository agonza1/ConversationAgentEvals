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
  await expect(page.getByRole('button', { name: /Interruption Detection 0 true \/ 3 false/ })).toContainText('handling not tested');
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
  await page.getByRole('button', { name: /Provisional caller WER 25%/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('not an accepted grade');
  await expect(page.getByLabel('Per-turn word error rates')).toContainText('Waylo final ASR');
  await page.getByRole('button', { name: /Interruption Detection 0 true \/ 3 false/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('Handling: not tested');
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
  await expect(page.getByRole('button', { name: /Interruption Detection unknown/ })).toContainText('No native interruption flags');
  await page.getByRole('button', { name: /Provisional caller WER n\/a/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('Provisional WER is unknown');
  await page.getByRole('button', { name: /Observed target response latency n\/a/ }).click();
  await expect(page.getByLabel('Metric detail')).toContainText('Observer response timing is unknown');
  await expect(page.getByLabel('Worker latency observations')).toContainText('2964ms');
});
