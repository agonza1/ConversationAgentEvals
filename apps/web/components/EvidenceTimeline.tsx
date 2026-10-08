'use client';

type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue => value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};

type EvidenceRow = { id: string; moment: string; kind: string; status: string; detail: unknown; time: unknown; dialog: number | null };
const usableTime = (time: unknown): time is string => typeof time === 'string' && Number.isFinite(Date.parse(time));

function EvidenceTable({ rows }: { rows: EvidenceRow[] }) {
  return <div style={{ overflowX: 'auto' }}>
    <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
      <thead><tr><th>Moment</th><th>Evidence</th><th>Status</th><th>Details</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id}>
        <td style={{ padding: 8 }}>{row.moment}{usableTime(row.time) ? <small style={{ display: 'block' }}>{row.time}</small> : null}</td>
        <td style={{ padding: 8 }}>{row.kind}</td><td style={{ padding: 8 }}>{row.status}</td>
        <td style={{ padding: 8 }}>{typeof row.detail === 'string' ? row.detail : <details><summary>View evidence</summary><pre style={{ maxWidth: 560, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(row.detail, null, 2)}</pre></details>}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

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
  // dialog is a zero-based vCon index; turn_index is one-based. Do not
  // substitute an action sequence number for a conversation reference.
  const linkedDialog = (event: RecordValue): number | null => {
    const index = event.dialog !== undefined ? event.dialog : typeof event.turn_index === 'number' ? event.turn_index - 1 : null;
    return typeof index === 'number' && Number.isInteger(index) && index >= 0 && index < dialogs.length && dialogs[index].type === 'text' ? index : null;
  };
  const actionEvent = (event: RecordValue) => String(event.event_type).startsWith('action.') || event.type === 'agent_action' || event.type === 'policy_violation';
  const moment = (event: RecordValue) => {
    const index = linkedDialog(event);
    return index !== null ? `Turn ${index + 1}` : usableTime(event.timestamp ?? event.started_at) ? 'Observation' : 'Timing unknown';
  };
  const parties = Array.isArray(vcon.parties) ? vcon.parties.map(record) : [];
  const conversation: EvidenceRow[] = dialogs.flatMap((d, i) => d.type !== 'text' ? [] : [{
    id: `dialog-${i}`, moment: `Turn ${i + 1}`, kind: Array.isArray(d.parties) ? d.parties.map((p) => typeof p === 'number' ? String(parties[p]?.name ?? 'Speaker') : 'Speaker').join(', ') || 'Conversation' : 'Conversation',
    status: 'text', detail: d.body, time: d.start, dialog: i,
  }]);
  const events: EvidenceRow[] = [
    ...tools.map((e, i) => ({ id: `tool-${i}`, moment: moment(e), kind: `${actionEvent(e) ? 'Action' : 'Tool call'} · ${String(e.name)}`, status: String(e.status), detail: e, time: e.timestamp ?? e.started_at, dialog: linkedDialog(e) })),
    ...voice.map((e, i) => ({ id: `voice-${i}`, moment: moment(e), kind: String(e.event_type), status: 'observed', detail: e, time: e.timestamp, dialog: linkedDialog(e) })),
    ...states.map((s, i) => ({ id: `state-${i}`, moment: `${String(s.phase)} state`, kind: 'Business state', status: String(s.source), detail: s.state, time: s.observed_at, dialog: linkedDialog(s) })),
  ];
  const all = [...conversation, ...events];
  const timed = all.length > 0 && all.every((row) => usableTime(row.time));
  const byDialog = new Map<number, EvidenceRow[]>();
  for (const event of events) {
    if (event.dialog === null) continue;
    const group = byDialog.get(event.dialog) ?? [];
    group.push(event);
    byDialog.set(event.dialog, group);
  }
  // References establish association, not an invented wall-clock time.
  const rows = timed ? all.sort((a, b) => Date.parse(String(a.time)) - Date.parse(String(b.time)))
    : conversation.flatMap((row) => [row, ...(byDialog.get(row.dialog!) ?? [])]);
  const unlinked = timed ? [] : events.filter((event) => event.dialog === null);
  const actionCount = tools.filter(actionEvent).length;
  const level = states.length ? 'State observed' : actionCount ? 'Actions observed' : tools.length ? 'Tools observed' : 'Transcript only';
  return (
    <section className="card" aria-label="Conversation and action evidence">
      <h3>Conversation and action evidence</h3>
      <p>{body.synthetic ? 'Synthetic sample · ' : ''}{level} · {actionCount} actions · {tools.length - actionCount} tool events · {states.length} state snapshots · {voice.length} voice events</p>
      <p style={{ color: 'var(--muted)' }}>Evidence is reported by its source. Tool success and confirmed business state are separate observations. {timed ? 'Sorted by observation time.' : 'Actions linked to a turn appear beneath it; exact timing is not inferred.'}</p>
      {rows.length > 0 ? <EvidenceTable rows={rows} /> : null}
      {unlinked.length > 0 ? <section aria-label="Evidence with unknown timing">
        <h4>Unlinked evidence · timing unknown</h4>
        <p style={{ color: 'var(--muted)' }}>These observations have no usable turn link. Their placement here does not mean they happened after the conversation.</p>
        <EvidenceTable rows={unlinked} />
      </section> : null}
    </section>
  );
}
