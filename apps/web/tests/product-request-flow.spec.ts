import { expect, test } from '@playwright/test';

test('product eval API journey works end to end', async ({ request, baseURL }) => {
  expect(baseURL).toBeTruthy();

  const pageResponse = await request.get('/benchmarks');
  expect(pageResponse.ok()).toBeTruthy();
  await expect(pageResponse.text()).resolves.toContain('Run an agentic scenario test.');

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
      user_id: 'playwright-user',
      project_id: 'call-center-demo',
      plan: 'starter',
      report: simulation.benchmark_report,
      transcript: simulation.transcript,
    },
  });
  expect(saveResponse.ok()).toBeTruthy();
  const saved = await saveResponse.json();
  expect(saved.id).toBeTruthy();

  const listResponse = await request.get('/api/product/runs?user_id=playwright-user&project_id=call-center-demo');
  expect(listResponse.ok()).toBeTruthy();
  const savedRuns = await listResponse.json();
  expect(savedRuns).toEqual(expect.arrayContaining([expect.objectContaining({ id: saved.id })]));

  const exportResponse = await request.get(`/api/product/runs/${saved.id}/export?user_id=playwright-user`);
  expect(exportResponse.ok()).toBeTruthy();
  await expect(exportResponse.json()).resolves.toEqual(expect.objectContaining({
    id: saved.id,
    filename: `agentbench-call-center-demo-${saved.id}.json`,
    report: expect.objectContaining({ verdict: 'pass' }),
  }));

  const projectExportResponse = await request.get('/api/product/projects/call-center-demo/export?user_id=playwright-user');
  expect(projectExportResponse.ok()).toBeTruthy();
  await expect(projectExportResponse.json()).resolves.toEqual(expect.objectContaining({
    filename: 'agentbench-call-center-demo-project-export.json',
    project_id: 'call-center-demo',
    run_count: 1,
    summary: expect.objectContaining({ latest_status: 'baseline' }),
    runs: expect.arrayContaining([expect.objectContaining({ id: saved.id })]),
  }));
});
