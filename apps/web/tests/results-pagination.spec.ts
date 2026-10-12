import { expect, test } from '@playwright/test';

test('results fetch bounded summaries, page forward, and reset server filters', async ({ page }) => {
  const requests: URL[] = [];
  await page.route('**/api/execution/runs?*', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const offset = Number(url.searchParams.get('offset'));
    const count = url.searchParams.has('status') ? 2 : offset === 0 ? 11 : 3;
    await route.fulfill({ json: Array.from({ length: count }, (_, index) => ({
      execution_run_id: `exec-page-${offset + index}`, agent_name: `Agent ${offset + index}`,
      status: 'completed', mode: 'text_callable', tester_id: 'scenario_simulator',
      updated_at: '2026-01-01T12:00:00Z', conversations: [{ score: 90 }],
    })) });
  });
  await page.goto('/results?api_base=http://127.0.0.1:8025');
  const pagination = page.getByRole('navigation', { name: 'Agent tests pagination' });
  await expect(page.locator('.runs-list-item')).toHaveCount(10);
  expect(requests[0].searchParams.get('limit')).toBe('11');
  expect(requests[0].searchParams.get('summary')).toBe('true');
  await expect(pagination.getByRole('button', { name: 'Previous' })).toBeDisabled();
  await pagination.getByRole('button', { name: 'Next' }).click();
  await expect(page.locator('.runs-list-item')).toHaveCount(3);
  await expect(pagination).toContainText('Page 2');
  expect(requests.at(-1)?.searchParams.get('offset')).toBe('10');
  await expect(pagination.getByRole('button', { name: 'Next' })).toBeDisabled();
  await page.getByLabel('Filter runs by status').selectOption('completed');
  await expect(page.locator('.runs-list-item')).toHaveCount(2);
  expect(requests.at(-1)?.searchParams.get('status')).toBe('completed');
  expect(requests.at(-1)?.searchParams.get('offset')).toBe('0');
  await expect(page.locator('.runs-list-item').first()).toHaveAttribute('href', /api_base=http%3A%2F%2F127.0.0.1%3A8025/);
});

test('failed result loads offer retry without showing an empty-state claim', async ({ page }) => {
  let fail = true;
  await page.route('**/api/execution/runs?*', async (route) => {
    await route.fulfill(fail ? { status: 503, json: { detail: 'Results unavailable' } } : { json: [] });
  });
  await page.goto('/results');
  await expect(page.getByRole('alert').filter({ hasText: 'Results unavailable' })).toBeVisible();
  await expect(page.getByText('No execution runs yet')).toHaveCount(0);
  fail = false;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByText('No execution runs yet')).toBeVisible();
});

test('saved history pages are bounded and evidence stays collapsed', async ({ page }) => {
  const requests: URL[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    if (url.pathname === '/api/product/runs') {
      const offset = Number(url.searchParams.get('offset'));
      await route.fulfill({ json: Array.from({ length: offset ? 2 : 11 }, (_, index) => ({
        id: `saved-${offset + index}`, report: { run_id: `report-${offset + index}`, suite_id: 'paging-suite',
          scenario_id: offset ? 'second-case' : 'first-case', scenario_title: `Evaluation ${offset + index}`, overall_score: 80 },
      })) });
    } else if (url.pathname === '/api/benchmarks/suites') {
      await route.fulfill({ json: [{ id: 'paging-suite', title: 'Paging suite', scenarios: [
        { id: 'first-case', title: 'First case' }, { id: 'second-case', title: 'Second case' },
      ] }] });
    } else {
      await route.fulfill({ json: [] });
    }
  });
  await page.goto('/results');
  await page.getByRole('button', { name: 'Saved evaluations', exact: true }).click();
  const pagination = page.getByRole('navigation', { name: 'Saved evaluations pagination' });
  await expect(page.getByText('10 evaluations on this page')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Load run', exact: true })).toHaveCount(10);
  await expect(page.getByRole('button', { name: 'Export JSON', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Load run', exact: true }).first().click();
  await expect(page.getByLabel('Report brief')).toContainText('Suite coverage: Not available');
  await expect(page.getByLabel('Report brief')).toContainText('Regression: Not compared');
  await expect(page.getByLabel('Report brief')).not.toContainText('Next uncovered');
  await pagination.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('2 evaluations on this page')).toBeVisible();
  await expect(pagination).toContainText('Page 2');
  const savedRequests = requests.filter((url) => url.pathname === '/api/product/runs');
  expect(savedRequests.every((url) => url.searchParams.get('limit') === '11')).toBe(true);
  expect(savedRequests.at(-1)?.searchParams.get('offset')).toBe('10');
  const suiteRequests = requests.filter((url) => url.pathname === '/api/benchmarks/suite-runs');
  expect(suiteRequests.every((url) => url.searchParams.get('limit') === '11')).toBe(true);
});

test('each history panel stays usable when the other endpoint fails', async ({ page }) => {
  let failure = 'suite';
  const historyRequests: string[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/product/runs') {
      historyRequests.push('saved');
      await route.fulfill(failure === 'saved' ? { status: 503, json: { detail: 'Saved history unavailable' } } : {
        json: [{ id: 'retained-result', report: { scenario_title: 'Retained evaluation', overall_score: 80 } }],
      });
    } else if (url.pathname === '/api/benchmarks/suite-runs') {
      historyRequests.push('suite');
      await route.fulfill(failure === 'suite' ? { status: 503, json: { detail: 'Suite history unavailable' } } : {
        json: [{ suite_run_id: 'retained-suite', suite_id: 'suite-a', status: 'completed', scenario_count: 1, pass_count: 1, average_score: 80 }],
      });
    } else {
      await route.fulfill({ json: [] });
    }
  });
  await page.goto('/results');
  await page.getByRole('button', { name: 'Saved evaluations', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Suite history unavailable' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Load run', exact: true })).toHaveCount(1);
  const savedRequestCount = historyRequests.filter((kind) => kind === 'saved').length;
  failure = '';
  await page.getByRole('button', { name: 'Retry suite runs', exact: true }).click();
  await expect(page.getByText('1 suite runs on this page')).toBeVisible();
  expect(historyRequests.filter((kind) => kind === 'saved').length).toBe(savedRequestCount);
  failure = 'saved';
  await page.reload();
  await page.getByRole('button', { name: 'Saved evaluations', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Saved history unavailable' })).toBeVisible();
  await expect(page.getByText('1 suite runs on this page')).toBeVisible();
  await expect(page.getByLabel('Suite run history', { exact: true }).getByRole('article').getByText('completed', { exact: true })).toBeVisible();
});
