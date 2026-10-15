const { test } = require('node:test');
const assert = require('node:assert/strict');
const { wayloDevOrigins } = require('./waylo-dev-origins');
test('selected fallback port receives exact loopback origins', () => {
  assert.equal(wayloDevOrigins(3013), 'http://127.0.0.1:3013,http://localhost:3013');
});
test('explicit origin policy is never broadened, even if empty', () => {
  assert.equal(wayloDevOrigins(3013, 'http://localhost:4444'), 'http://localhost:4444');
  assert.equal(wayloDevOrigins(3013, ''), '');
});
