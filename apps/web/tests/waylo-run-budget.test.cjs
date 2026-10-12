const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const moduleUnderTest = { exports: {} };
new Function('module', 'exports', ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/wayloRunBudget.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText)(moduleUnderTest, moduleUnderTest.exports);
const { wayloRunBudgetError } = moduleUnderTest.exports;

test('ten-case temporary suites never fit even at the minimum timeout', () => {
  assert.match(wayloRunBudgetError(30, 10), /15-minute/);
  assert.match(wayloRunBudgetError(120, 10), /single scenario/);
  assert.match(wayloRunBudgetError(120, 4), /fewer iterations/);
});
test('small scopes remain available when they fit the actual remaining grant', () => {
  const now = Date.parse('2026-10-11T12:00:00Z');
  const expiry = new Date(now + 900000).toISOString();
  assert.equal(wayloRunBudgetError(120, 3, expiry, now), null);
  assert.match(wayloRunBudgetError(120, 3, expiry, now + 200000), /expires before/);
  assert.match(wayloRunBudgetError(120, 1, 'invalid', now), /expires before/);
  assert.match(wayloRunBudgetError(120, 1, expiry, now + 900000), /expires before/);
});
