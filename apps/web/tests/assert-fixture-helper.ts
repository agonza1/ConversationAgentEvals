import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

/** Offline fixture preparation only; never calls a provider or an HTTP judge. */
export function fixturePython(code: string, input?: string): string {
  const container = process.env.CAE_TEST_PYTHON_CONTAINER;
  const windowsPython = path.resolve('apps/api/.venv/Scripts/python.exe');
  const python = process.env.CAE_TEST_PYTHON || (existsSync(windowsPython)
    ? windowsPython : path.resolve('apps/api/.venv/bin/python'));
  return execFileSync(container ? 'docker' : python,
    container ? ['exec', '-i', '-w', '/workspace', '-e', 'PYTHONPATH=/workspace/apps/api:/workspace/apps/api/tests',
      container, 'python', '-c', code] : ['-c', code],
    { input, encoding: 'utf8', env: { ...process.env,
      PYTHONPATH: [path.resolve('apps/api'), path.resolve('apps/api/tests')].join(path.delimiter) } });
}

export function refreshAssertFixture<T>(run: T, preserveUnverifiable = false): T {
  return JSON.parse(fixturePython(`
import sys,json
from assert_test_helpers import refresh_review
run=json.load(sys.stdin)
for conv in run['conversations']:
    for review in conv.get('judge_reviews',[]):
        if ${preserveUnverifiable ? 'True' : 'False'} and not review.get('judge_result',{}).get('provenance',{}).get('input_fingerprint'):
            # Deliberately model a historical review without input provenance.
            review.pop('deterministic_snapshot',None)
            continue
        refresh_review(run,conv,review)
print(json.dumps(run))
`, JSON.stringify(run))) as T;
}
