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
const timeline = (dialog, tool_events, state_snapshots = [], voice_events = []) => renderToStaticMarkup(React.createElement(EvidenceTimeline, {
  vcon: { dialog, parties: [{ name: 'Caller' }], attachments: [attachment('', {
    body: { schema: 'cae-execution-evidence-v1', tool_events, state_snapshots, voice_events },
  })] },
}));
const text = (body, extra = {}) => ({ type: 'text', body, parties: [0], ...extra });

test('explicit dialog zero and turn links interleave actions without inventing timestamps', () => {
  const html = timeline([text('FIRST TURN'), text('SECOND TURN')], [
    { name: 'FIRST ACTION', event_type: 'action.completed', status: 'success', dialog: 0 },
    { name: 'SECOND ACTION', type: 'agent_action', status: 'success', turn_index: 2 },
  ]);
  assert.ok(html.indexOf('FIRST TURN') < html.indexOf('FIRST ACTION'));
  assert.ok(html.indexOf('FIRST ACTION') < html.indexOf('SECOND TURN'));
  assert.ok(html.indexOf('SECOND TURN') < html.indexOf('SECOND ACTION'));
  assert.match(html, /2 actions · 0 tool events/);
  assert.match(html, /Caller/);
  assert.doesNotMatch(html, /Unlinked evidence|Sorted by observation time/);
});

test('unlinked actions and final state are explicitly separated from conversation order', () => {
  const html = timeline([text('CALL TURN')], [{ name: 'Unlinked action', status: 'success', sequence: 1 }], [
    { phase: 'final', state: { complete: true }, source: 'reported' },
  ]);
  assert.ok(html.indexOf('Unlinked evidence') < html.indexOf('Unlinked action'));
  assert.match(html, /does not mean they happened after the conversation/);
  assert.match(html, /final state/);
});

test('recording dialogs do not renumber text references', () => {
  const html = timeline([{ type: 'recording' }, text('LINKED TURN'), text('NEXT TURN')], [
    { name: 'LINKED TOOL', dialog: 1, status: 'success' },
  ]);
  assert.ok(html.indexOf('LINKED TURN') < html.indexOf('LINKED TOOL'));
  assert.ok(html.indexOf('LINKED TOOL') < html.indexOf('NEXT TURN'));
  assert.match(html, /Turn 2/);
});

test('invalid references are never replaced by sequence-based guesses', () => {
  for (const reference of [{ dialog: -1 }, { dialog: 9, turn_index: 1 }, { dialog: '0' }, { turn_index: 0 }]) {
    const html = timeline([text('TURN')], [{ name: 'UNLINKED', sequence: 1, ...reference }]);
    assert.ok(html.indexOf('Unlinked evidence') < html.indexOf('UNLINKED'));
  }
});

test('complete observation timestamps establish chronological order', () => {
  const html = timeline([text('LATER TURN', { start: '2026-10-05T12:00:01Z' })], [
    { name: 'EARLIER TOOL', timestamp: '2026-10-05T12:00:00Z' },
  ]);
  assert.ok(html.indexOf('EARLIER TOOL') < html.indexOf('LATER TURN'));
  assert.match(html, /Sorted by observation time/);
  assert.doesNotMatch(html, /Unlinked evidence/);
});

test('mixed timestamps do not imply chronology, and linked voice events are retained', () => {
  const html = timeline([text('TURN')], [{ name: 'TOOL', timestamp: '2026-10-05T12:00:00Z' }], [], [
    { event_type: 'asr.receipt', dialog: 0 },
  ]);
  assert.ok(html.indexOf('TURN') < html.indexOf('asr.receipt'));
  assert.ok(html.indexOf('asr.receipt') < html.indexOf('Unlinked evidence'));
  assert.ok(html.indexOf('Unlinked evidence') < html.indexOf('TOOL'));
  assert.doesNotMatch(html, /Sorted by observation time/);
});

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
