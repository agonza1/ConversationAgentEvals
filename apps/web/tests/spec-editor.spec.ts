import { expect, test } from '@playwright/test';

test('spec editor generates draft checks, requires approval, previews YAML, and saves a version', async ({ page }) => {
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
        provider: 'local_draft_generator',
        model: 'not-configured',
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
        yaml: `assert_version: v2\nmetadata:\n  title: ${body.spec.title}\nobjective: ${body.spec.objective}\n`,
        json_preview: { metadata: { title: body.spec.title } },
        export_filename: 'cancellation-rescue-agent.assert.yml',
      }),
    });
  });

  await page.route('**/api/specs', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    expect(body.spec.generated_content_status).toBe('approved');
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
        yaml: 'assert_version: v2\n',
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
  await expect(page.getByText('assert_version: v2')).toBeVisible();
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `cancellation-rescue-agent` version 1/)).toBeVisible();
});
