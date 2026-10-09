const assert = require('node:assert/strict');
const http = require('node:http');
const { parse } = require('node:url');
const { test } = require('node:test');
const { proxyRequest } = require('next/dist/server/lib/router-utils/proxy-request');
const config = require('../next.config.js');

const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const close = server => new Promise(resolve => server.close(resolve));

test('configured Next rewrite delivers a judge error after its former 30s timeout', { timeout: 45_000 }, async () => {
  const upstream = http.createServer((_req, res) => {
    setTimeout(() => {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ detail: 'Synthetic ASSERT failure; evidence unchanged.' }));
    }, 31_000);
  });
  const proxy = http.createServer((req, res) => {
    const url = parse(`http://127.0.0.1:${upstream.address().port}/slow-judge`);
    proxyRequest(req, res, url, undefined, undefined, config.experimental.proxyTimeout)
      .catch(() => { res.writeHead(500); res.end('Proxy reset'); });
  });
  try {
    await listen(upstream);
    await listen(proxy);
    const response = await fetch(`http://127.0.0.1:${proxy.address().port}/api/assert/synthetic/judge`);
    assert.equal(response.status, 502);
    assert.match((await response.json()).detail, /Synthetic ASSERT failure/);
  } finally {
    await Promise.all([close(proxy), close(upstream)]);
  }
});
