const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const source = fs.readFileSync(path.join(__dirname, '../components/LiveRunFeedback.tsx'), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
});
const componentModule = { exports: {} };
new Function('require', 'module', 'exports', outputText)(require, componentModule, componentModule.exports);
const { sessionEvents, LiveRunFeedback } = componentModule.exports;
const conversations = ['case-one', 'case-two'].map((id) => ({
  conversation_id: id, scenario_title: id,
  live_events: [1, 2].map((sequence) => ({
    sequence, kind: 'audio', speaker: 'Caller', text: id, media_url: `/${id}/${sequence}.wav`,
  })),
}));

test('a selected session never queues audio from the next case', () => {
  assert.deepEqual(sessionEvents(conversations, 'case-one').map((event) => event.media_url),
    ['/case-one/1.wav', '/case-one/2.wav']);
  assert.deepEqual(sessionEvents(conversations, 'case-two').map((event) => event.media_url),
    ['/case-two/1.wav', '/case-two/2.wav']);
  assert.deepEqual(sessionEvents(conversations, 'missing'), []);
});

test('suite live monitor retains explicit session identities', () => {
  assert.deepEqual(sessionEvents(conversations).map((event) => event.conversationId),
    ['case-one', 'case-one', 'case-two', 'case-two']);
});

test('completed suite offers one recorded session and discloses omitted waits', () => {
  const html = renderToStaticMarkup(React.createElement(LiveRunFeedback, {
    conversations, apiBase: '', voice: true, runStatus: 'completed',
  }));
  assert.match(html, /Recorded session/);
  assert.match(html, /value="case-one" selected/);
  assert.match(html, /only the selected session/);
  assert.match(html, /without the original response waits/);
});
