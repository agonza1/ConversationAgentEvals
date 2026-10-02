'use client';

type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue => value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};

export function EvidenceTimeline({ vcon }: { vcon?: RecordValue | null }) {
  if (!vcon) return null;
  const attachments = Array.isArray(vcon.attachments) ? vcon.attachments : [];
  const body = record(attachments.map(record).find((a) =>
    a.purpose === 'CAE execution evidence'
    && a.mediatype === 'application/json'
    && a.encoding === 'json'
    && record(a.body).schema === 'cae-execution-evidence-v1'
  )?.body);
  const tools = Array.isArray(body.tool_events) ? body.tool_events.map(record) : [];
  const voice = Array.isArray(body.voice_events) ? body.voice_events.map(record) : [];
  const states = Array.isArray(body.state_snapshots) ? body.state_snapshots.map(record) : [];
  const dialogs = Array.isArray(vcon.dialog) ? vcon.dialog.map(record) : [];
  const rows = [
    ...dialogs.filter((d) => d.type === 'text').map((d, i) => ({ id: `dialog-${i}`, moment: `Turn ${i + 1}`, kind: 'Conversation', status: 'text', detail: d.body, time: d.start })),
    ...tools.map((e, i) => ({ id: String(e.event_id), moment: e.turn_index ? `Turn ${e.turn_index}` : `Action ${e.sequence ?? i + 1}`, kind: String(e.name), status: String(e.status), detail: e, time: e.timestamp ?? e.started_at })),
    ...voice.map((e, i) => ({ id: String(e.event_id), moment: e.turn_index ? `Turn ${e.turn_index}` : `Voice ${i + 1}`, kind: String(e.event_type), status: 'observed', detail: e, time: e.timestamp })),
    ...states.map((s, i) => ({ id: `state-${i}`, moment: String(s.phase), kind: 'Business state', status: String(s.source), detail: s.state, time: s.observed_at })),
  ];
  // Order by wall-clock only when every row has a usable observation time.
  const timed = rows.every((row) => typeof row.time === 'string' && Number.isFinite(Date.parse(row.time)));
  if (timed) rows.sort((a, b) => Date.parse(String(a.time)) - Date.parse(String(b.time)));
  const level = states.length ? 'State observed' : tools.length ? 'Tools observed' : 'Transcript only';
  return (
    <section className="card" aria-label="Conversation and action evidence">
      <h3>Conversation and action evidence</h3>
      <p>{body.synthetic ? 'Synthetic sample · ' : ''}{level} · {tools.length} tool events · {states.length} state snapshots · {voice.length} voice events</p>
      <p style={{ color: 'var(--muted)' }}>Evidence is reported by its source. Tool success and confirmed business state are separate observations. {timed ? 'Sorted by observation time.' : 'Grouped by evidence type; missing timing is not inferred.'}</p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
          <thead><tr><th>Moment</th><th>Evidence</th><th>Status</th><th>Details</th></tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.id}>
              <td style={{ padding: 8 }}>{row.moment}{row.time ? <small style={{ display: 'block' }}>{String(row.time)}</small> : null}</td>
              <td style={{ padding: 8 }}>{row.kind}</td><td style={{ padding: 8 }}>{row.status}</td>
              <td style={{ padding: 8 }}>{typeof row.detail === 'string' ? row.detail : <details><summary>View evidence</summary><pre style={{ maxWidth: 560, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(row.detail, null, 2)}</pre></details>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}
