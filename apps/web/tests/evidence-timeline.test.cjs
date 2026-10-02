const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const source = fs.readFileSync(path.join(__dirname, '../components/EvidenceTimeline.tsx'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
});
const componentModule = { exports: {} };
new Function('require', 'module', 'exports', outputText)(require, componentModule, componentModule.exports);
const { EvidenceTimeline } = componentModule.exports;

const attachment = (name, overrides = {}) => ({
  purpose: 'CAE execution evidence', mediatype: 'application/json', encoding: 'json',
  body: { schema: 'cae-execution-evidence-v1', tool_events: [{ event_id: name, name, status: 'success' }] },
  ...overrides,
});
const render = (attachments) => renderToStaticMarkup(React.createElement(EvidenceTimeline, { vcon: { attachments } }));

test('timeline ignores lookalike attachments before the scored evidence profile', () => {
  const html = render([
    attachment('Unrelated tool', { purpose: 'Other evidence' }),
    attachment('Wrong media tool', { mediatype: 'text/plain' }),
    attachment('Wrong encoding tool', { encoding: 'base64url' }),
    attachment('Wrong schema tool', { body: { schema: 'other-profile' } }),
    attachment('Scored tool'),
  ]);
  assert.match(html, /Scored tool/);
  assert.match(html, /1 tool events/);
  assert.doesNotMatch(html, /Unrelated tool|Wrong media tool|Wrong encoding tool|Wrong schema tool/);
});

test('timeline does not treat unrelated lookalike evidence as tool observations', () => {
  const html = render([attachment('Unrelated tool', { purpose: 'Other evidence' })]);
  assert.match(html, /Transcript only/);
  assert.match(html, /0 tool events/);
  assert.doesNotMatch(html, /Unrelated tool/);
});
