import { expect, test } from '@playwright/test';

test('product eval API journey works end to end', async ({ request, baseURL }) => {
  expect(baseURL).toBeTruthy();
  const userId = `playwright-user-${Date.now()}`;

  const pageResponse = await request.get('/benchmarks');
  expect(pageResponse.ok()).toBeTruthy();
  await expect(pageResponse.text()).resolves.toContain('Benchmark history and reports.');

  const configResponse = await request.get('/api/product/config');
  expect(configResponse.ok()).toBeTruthy();
  const config = await configResponse.json();
  expect(config.pricing).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'starter', price_label: '$19/month', seats: 'Unlimited seats' }),
    expect.objectContaining({ id: 'team', price_label: '$99/month', seats: 'Unlimited seats' }),
    expect.objectContaining({ id: 'business', price_label: 'Contact Us' }),
  ]));

  const suitesResponse = await request.get('/api/benchmarks/suites');
  expect(suitesResponse.ok()).toBeTruthy();
  const suites = await suitesResponse.json();
  expect(suites[0].id).toBe('call-center-voice-ai');
  expect(suites[0].scenarios.length).toBeGreaterThan(0);

  const suiteId = suites[0].id;
  const scenarioId = suites[0].scenarios[0].id;
  const simulationResponse = await request.post('/api/benchmarks/simulate', {
    data: {
      suite_id: suiteId,
      scenario_id: scenarioId,
      agent_profile: 'playwright request runner',
    },
  });
  expect(simulationResponse.ok()).toBeTruthy();
  const simulation = await simulationResponse.json();
  expect(simulation.transcript).toContain('playwright request runner');
  expect(simulation.benchmark_report.verdict).toBe('pass');

  const freeJudgeResponse = await request.post('/api/product/judge', {
    data: {
      plan: 'free',
      report: simulation.benchmark_report,
      transcript: simulation.transcript,
    },
  });
  expect(freeJudgeResponse.ok()).toBeTruthy();
  await expect(freeJudgeResponse.json()).resolves.toEqual(expect.objectContaining({
    status: 'blocked',
    required_plan: 'starter',
  }));

  const paidJudgeResponse = await request.post('/api/product/judge', {
    data: {
      plan: 'starter',
      report: simulation.benchmark_report,
      transcript: simulation.transcript,
    },
  });
  expect(paidJudgeResponse.ok()).toBeTruthy();
  await expect(paidJudgeResponse.json()).resolves.toEqual(expect.objectContaining({
    status: 'ready',
    credits: 10,
  }));

  const saveResponse = await request.post('/api/product/runs', {
    data: {
      user_id: userId,
      project_id: 'call-center-demo',
      plan: 'starter',
      report: simulation.benchmark_report,
      transcript: simulation.transcript,
    },
  });
  expect(saveResponse.ok()).toBeTruthy();
  const saved = await saveResponse.json();
  expect(saved.id).toBeTruthy();
  expect(saved.artifacts.vcon_export).toEqual(expect.objectContaining({
    available: true,
    source_format: 'transcript',
    appended_analysis_type: 'agentic_benchmark_eval',
  }));
  expect(saved.artifacts.contract_artifacts).toEqual(expect.objectContaining({
    available: true,
    suite_contract_manifest_sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    scenario_contract_sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  }));

  const listResponse = await request.get(`/api/product/runs?user_id=${userId}&project_id=call-center-demo`);
  expect(listResponse.ok()).toBeTruthy();
  const savedRuns = await listResponse.json();
  expect(savedRuns).toEqual(expect.arrayContaining([expect.objectContaining({ id: saved.id })]));

  const exportResponse = await request.get(`/api/product/runs/${saved.id}/export?user_id=${userId}`);
  expect(exportResponse.ok()).toBeTruthy();
  await expect(exportResponse.json()).resolves.toEqual(expect.objectContaining({
    id: saved.id,
    filename: `agentbench-call-center-demo-${saved.id}.json`,
    report: expect.objectContaining({ verdict: 'pass' }),
    artifacts: expect.objectContaining({
      vcon_export: expect.objectContaining({ available: true }),
      contract_artifacts: expect.objectContaining({ available: true }),
    }),
  }));

  const projectExportResponse = await request.get(`/api/product/projects/call-center-demo/export?user_id=${userId}`);
  expect(projectExportResponse.ok()).toBeTruthy();
  await expect(projectExportResponse.json()).resolves.toEqual(expect.objectContaining({
    filename: 'agentbench-call-center-demo-project-export.json',
    project_id: 'call-center-demo',
    run_count: 1,
    summary: expect.objectContaining({ latest_status: 'baseline' }),
    vcon_export_summary: expect.objectContaining({
      available_records: 1,
      total_runs: 1,
    }),
    contract_artifact_summary: expect.objectContaining({
      available_records: 1,
      total_runs: 1,
      suite_contract_manifest_sha256s: expect.arrayContaining([saved.artifacts.contract_artifacts.suite_contract_manifest_sha256]),
      scenario_contract_sha256s: expect.arrayContaining([saved.artifacts.contract_artifacts.scenario_contract_sha256]),
    }),
    runs: expect.arrayContaining([expect.objectContaining({ id: saved.id })]),
  }));

  const filteredProjectExportResponse = await request.get(
    `/api/product/projects/call-center-demo/export?user_id=${userId}&suite_id=${suiteId}&scenario_id=${scenarioId}`,
  );
  expect(filteredProjectExportResponse.ok()).toBeTruthy();
  await expect(filteredProjectExportResponse.json()).resolves.toEqual(expect.objectContaining({
    filename: `agentbench-call-center-demo-${suiteId}-${scenarioId}-project-export.json`,
    suite_id: suiteId,
    scenario_id: scenarioId,
    run_count: 1,
    summary: expect.objectContaining({ run_count: 1, latest_status: 'baseline' }),
    contract_artifact_summary: expect.objectContaining({
      available_records: 1,
      total_runs: 1,
      suite_contract_manifest_sha256s: expect.arrayContaining([saved.artifacts.contract_artifacts.suite_contract_manifest_sha256]),
      scenario_contract_sha256s: expect.arrayContaining([saved.artifacts.contract_artifacts.scenario_contract_sha256]),
    }),
    runs: expect.arrayContaining([expect.objectContaining({ id: saved.id })]),
  }));
});
