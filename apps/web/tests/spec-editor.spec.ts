import { expect, test } from '@playwright/test';

test('spec editor generates draft checks, requires approval, previews YAML, and saves a version', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'workspace-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'workspace-project');
  });
  await page.route('**/api/specs/templates', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        templates: [
          {
            id: 'generic-conversation-agent',
            label: 'Generic conversation agent',
            description: 'Standalone CAE template',
            spec: {
              title: 'Generic support agent',
              role: 'customer support agent',
              objective: 'Resolve the user request while following policy.',
              generated_content_status: 'none',
              required_behaviors: [],
              forbidden_behaviors: [],
              scenario_seeds: [],
              scenarios: [],
              deterministic_checks: [],
              evidence_requirements: ['conversation transcript'],
              judges: [{ id: 'semantic-policy-judge', name: 'Semantic policy judge', kind: 'semantic', rubric: 'Score success and forbidden checks.', weight: 1, provider: 'configured-default' }],
              extensions: {},
            },
          },
        ],
      }),
    });
  });

  await page.route('**/api/specs/generate', async (route) => {
    expect(JSON.parse(route.request().postData() || '{}')).toMatchObject({
      title: 'Cancellation rescue agent',
      role: 'insurance retention voice agent',
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        provider: 'openai_codex',
        model: 'gpt-5.4',
        status: 'draft',
        requires_user_approval: true,
        required_behaviors: [{ id: 'complete-task', label: 'Completes the stated task', description: 'Draft', severity: 'error', draft: true }],
        forbidden_behaviors: [{ id: 'no-unsupported-claims', label: 'No unsupported claims', description: 'Draft', severity: 'error', draft: true }],
        scenario_seeds: ['Policy trap: user asks for an unsupported promise.'],
        scenarios: [{ id: 'policy-trap', title: 'Pressure for unsupported promise', persona: 'frustrated caller', description: 'Caller pressures the agent.', steps: [], expected_outcome: 'Agent refuses unsupported promise.', draft: true }],
        deterministic_checks: [{ id: 'final-state-present', label: 'Final state evidence present', description: 'Draft', severity: 'warning', draft: true }],
        judges: [{ id: 'semantic-policy-judge', name: 'Semantic policy judge', kind: 'semantic', rubric: 'Score policy-safe rescue.', weight: 1, provider: 'configured-default' }],
        note: 'Draft suggestions only.',
      }),
    });
  });

  await page.route('**/api/specs/preview', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    const approved = body.spec.generated_content_status === 'approved';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        valid: approved,
        errors: approved ? [] : [{ field: 'generated_content_status', message: 'Generated suggestions must be approved or edited before saving.', severity: 'error' }],
        warnings: [],
        normalized: body.spec,
        yaml: `suite: cancellation-rescue-agent\nbehavior:\n  name: cancellation-rescue-agent\n  description: ${body.spec.objective}\npipeline:\n  systematize: {}\n`,
        json_preview: { suite: 'cancellation-rescue-agent', pipeline: { systematize: {} } },
        export_filename: 'cancellation-rescue-agent.eval_config.yaml',
        assert_validator: 'assert-ai',
        assert_validated: true,
      }),
    });
  });

  await page.route('**/api/specs', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    expect(body.spec.generated_content_status).toBe('approved');
    expect(body.user_id).toBe('workspace-user');
    expect(body.project_id).toBe('workspace-project');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'cancellation-rescue-agent',
        version: 1,
        user_id: body.user_id,
        project_id: body.project_id,
        created_at: '2026-07-17T20:00:00Z',
        updated_at: '2026-07-17T20:00:00Z',
        spec: { ...body.spec, id: 'cancellation-rescue-agent', version: 1 },
        yaml: 'suite: cancellation-rescue-agent\npipeline:\n  systematize: {}\n',
      }),
    });
  });

  await page.goto('/specs/new?api_base=http%3A%2F%2Fapi.example.test');
  await expect(page.getByRole('heading', { name: 'Friendly editable ASSERT YAML' })).toBeVisible();
  await expect(page.getByText('CAE-owned')).toBeVisible();
  await page.getByRole('button', { name: 'Generate draft checks/scenarios' }).click();
  await expect(page.getByText('Generated suggestions are draft content')).toBeVisible();
  await expect(page.getByLabel('Success checks')).toHaveValue(/Completes the stated task/);
  await expect(page.getByText('Generated suggestions must be approved')).toBeVisible();
  await page.getByRole('button', { name: 'Approve generated draft' }).click();
  await expect(page.getByText('Valid preview')).toBeVisible();
  await expect(page.getByText('suite: cancellation-rescue-agent')).toBeVisible();
  await expect(page.getByText('Workspace: workspace-project')).toBeVisible();
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `cancellation-rescue-agent` version 1/)).toBeVisible();
});

test('loading and previewing a rich template preserves structured fields', async ({ page }) => {
  const richSpec = {
    title: 'Rich support agent',
    role: 'customer support agent',
    objective: 'Resolve a rich structured request without losing evaluation metadata.',
    generated_content_status: 'none',
    required_behaviors: [{ id: 'success-rich', label: 'Resolve request', description: 'Keep this detailed description.', severity: 'info', draft: false }],
    forbidden_behaviors: [{ id: 'failure-rich', label: 'No invention', description: 'Keep this forbidden description.', severity: 'error', draft: false }],
    scenario_seeds: ['Rich seed'],
    scenarios: [{ id: 'rich-scenario', title: 'Rich scenario', persona: 'careful operator', description: 'Structured scenario.', steps: ['First step', 'Second step'], expected_outcome: 'Detailed terminal state.', draft: false }],
    deterministic_checks: [{ id: 'det-rich', label: 'Artifact exists', description: 'Keep artifact detail.', severity: 'warning', draft: false }],
    evidence_requirements: ['transcript'],
    judges: [{ id: 'judge-rich', name: 'Rich judge', kind: 'semantic', rubric: 'Keep the rich rubric.', weight: 2, provider: 'configured-default', model: 'gpt-5.4' }],
    runtime_overrides: { target: { endpoint: 'http://example.test/agent' } },
    extensions: { integration: { nested: true } },
  };
  let previewed: Record<string, unknown> | null = null;
  await page.route('**/api/specs/templates', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ templates: [{ id: 'rich', label: 'Rich template', description: 'Rich', spec: richSpec }] }),
  }));
  await page.route('**/api/specs/preview', async (route) => {
    previewed = JSON.parse(route.request().postData() || '{}').spec;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, errors: [], warnings: [], normalized: previewed, yaml: 'suite: rich-support-agent\npipeline:\n  systematize: {}\n', json_preview: {}, export_filename: 'rich.eval_config.yaml', assert_validator: 'assert-ai', assert_validated: true }),
    });
  });

  await page.goto('/specs/new?api_base=http%3A%2F%2Fapi.example.test');
  await page.getByLabel('Template').selectOption('rich');
  await expect.poll(() => previewed).not.toBeNull();
  await expect.poll(() => JSON.stringify(previewed)).toBe(JSON.stringify(richSpec));
});

test('deleting a check does not transfer removed metadata to the surviving check', async ({ page }) => {
  const richSpec = {
    title: 'Metadata-safe support agent',
    role: 'customer support agent',
    objective: 'Keep evaluation metadata attached to the correct requirement after edits.',
    generated_content_status: 'none',
    required_behaviors: [
      { id: 'removed-check', label: 'Remove me', description: 'Metadata that must not survive.', severity: 'error', draft: false },
      { id: 'kept-check', label: 'Keep me', description: 'Metadata that belongs to the survivor.', severity: 'info', draft: false },
    ],
    forbidden_behaviors: [{ id: 'forbidden', label: 'No invention', description: 'Do not invent.', severity: 'error', draft: false }],
    scenario_seeds: ['Metadata edit'],
    scenarios: [],
    deterministic_checks: [],
    evidence_requirements: ['transcript'],
    judges: [{ id: 'judge', name: 'Judge', kind: 'semantic', rubric: 'Evaluate metadata safety.', weight: 1, provider: 'configured-default' }],
    runtime_overrides: {},
    extensions: {},
  };
  let previewed: typeof richSpec | null = null;
  await page.route('**/api/specs/templates', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ templates: [{ id: 'metadata', label: 'Metadata template', description: 'Metadata', spec: richSpec }] }),
  }));
  await page.route('**/api/specs/preview', async (route) => {
    previewed = JSON.parse(route.request().postData() || '{}').spec;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, errors: [], warnings: [], normalized: previewed, yaml: 'suite: metadata-safe-support-agent\npipeline:\n  systematize: {}\n', json_preview: {}, export_filename: 'metadata.eval_config.yaml', assert_validator: 'assert-ai', assert_validated: true }),
    });
  });

  await page.goto('/specs/new?api_base=http%3A%2F%2Fapi.example.test');
  await page.getByLabel('Template').selectOption('metadata');
  await page.getByLabel('Success checks').fill('Keep me');
  await expect.poll(() => previewed?.required_behaviors).toEqual([
    { id: 'kept-check', label: 'Keep me', description: 'Metadata that belongs to the survivor.', severity: 'info', draft: false },
  ]);
});

test('save completion does not overwrite edits made while the request is in flight', async ({ page }) => {
  let releaseSave: (() => void) | null = null;
  let markSaveStarted: (() => void) | null = null;
  const saveStarted = new Promise<void>((resolve) => { markSaveStarted = resolve; });
  await page.route('**/api/specs/templates', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ templates: [] }) }));
  await page.route('**/api/specs/preview', async (route) => {
    const spec = JSON.parse(route.request().postData() || '{}').spec;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ valid: true, errors: [], warnings: [], normalized: spec, yaml: 'suite: stale-save\npipeline:\n  systematize: {}\n', json_preview: {}, export_filename: 'stale-save.eval_config.yaml', assert_validator: 'assert-ai', assert_validated: true }) });
  });
  await page.route('**/api/specs', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    markSaveStarted?.();
    await new Promise<void>((resolve) => { releaseSave = resolve; });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'stale-save', version: 1, user_id: body.user_id, project_id: body.project_id, created_at: '2026-07-18T20:00:00Z', updated_at: '2026-07-18T20:00:00Z', spec: { ...body.spec, id: 'stale-save', version: 1 }, yaml: 'suite: stale-save\n' }) });
  });

  await page.goto('/specs/new?api_base=http%3A%2F%2Fapi.example.test');
  await page.getByRole('button', { name: 'Save version' }).click();
  await saveStarted;
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill('This newer objective must survive the older save response.');
  releaseSave?.();

  await expect(page.getByText(/Saved `stale-save` version 1/)).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue('This newer objective must survive the older save response.');
});
