'use client';

import { FormEvent, useEffect, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import {
  AgentRecord,
  createAgent,
  deleteAgent,
  listAgents,
  updateAgent,
} from '@/lib/execution';

export function AgentsPage() {
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [channel, setChannel] = useState<AgentRecord['channel']>('text');
  const [target, setTarget] = useState<AgentRecord['target']>('mock_agent');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  async function reload() {
    const next = await listAgents();
    setAgents(next);
  }

  useEffect(() => {
    let active = true;
    listAgents()
      .then((next) => {
        if (active) setAgents(next);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : 'Could not load agents');
      });
    return () => {
      active = false;
    };
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await createAgent({
        name: name.trim(),
        channel,
        target,
        description: description.trim() || undefined,
      });
      setName('');
      setDescription('');
      setMessage('Agent created.');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create agent');
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="page-shell compact-shell">
      <SiteNav current="agents" />
      <section className="minimal-hero" aria-labelledby="agents-title">
        <p className="eyebrow">Agent registry</p>
        <h1 id="agents-title">Agents</h1>
        <p>Define text and voice targets used when launching evaluation runs.</p>
      </section>

      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {message ? <p className="scenarios-muted">{message}</p> : null}

      <div className="scenarios-layout">
        <aside className="card scenarios-sidebar">
          <div className="scenarios-sidebar-header">
            <div>
              <p className="eyebrow">Library</p>
              <h2>Registered agents</h2>
            </div>
          </div>
          <ul className="scenarios-list">
            {agents.map((agent) => (
              <li key={agent.id}>
                <div className="agent-list-card">
                  <strong>{agent.name}</strong>
                  <span>{agent.channel} · {agent.target}</span>
                  <div className="scenarios-actions">
                    <button
                      type="button"
                      className="secondary-link"
                      onClick={() => {
                        const nextName = window.prompt('Rename agent', agent.name);
                        if (!nextName?.trim()) return;
                        void updateAgent(agent.id, { name: nextName.trim() }).then(reload).catch((err) => setError(String(err)));
                      }}
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      className="secondary-link"
                      onClick={() => void deleteAgent(agent.id).then(reload).catch((err) => setError(String(err)))}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </aside>

        <form className="card scenarios-create-form" onSubmit={(event) => void onCreate(event)}>
          <div>
            <p className="eyebrow">Create</p>
            <h2>New agent</h2>
          </div>
          <label>
            <span>Name</span>
            <input required value={name} onChange={(event) => setName(event.target.value)} placeholder="Support bot v2" />
          </label>
          <label>
            <span>Channel</span>
            <select aria-label="Agent channel" value={channel} onChange={(event) => setChannel(event.target.value as AgentRecord['channel'])}>
              <option value="text">text</option>
              <option value="voice">voice</option>
            </select>
          </label>
          <label>
            <span>Target</span>
            <select aria-label="Agent target" value={target} onChange={(event) => setTarget(event.target.value as AgentRecord['target'])}>
              <option value="mock_agent">mock_agent</option>
              <option value="offline_acc_fixture">offline_acc_fixture</option>
              <option value="voice_fixture">voice_fixture</option>
            </select>
          </label>
          <label>
            <span>Description</span>
            <textarea rows={4} value={description} onChange={(event) => setDescription(event.target.value)} />
          </label>
          <button type="submit" className="primary-link" disabled={saving}>
            {saving ? 'Creating…' : 'Create agent'}
          </button>
        </form>
      </div>
    </main>
  );
}
