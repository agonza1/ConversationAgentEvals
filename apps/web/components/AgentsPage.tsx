'use client';

import { FormEvent, useEffect, useId, useRef, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import {
  AgentRecord,
  agentTryItOutHref,
  createAgent,
  deleteAgent,
  isBuiltInAgent,
  listAgents,
  updateAgent,
} from '@/lib/execution';

type AgentFormState = {
  name: string;
  channel: AgentRecord['channel'];
  target: AgentRecord['target'];
  environment: NonNullable<AgentRecord['environment']>;
  endpointUrl: string;
  authType: 'none' | 'bearer_secret' | 'api_key_secret';
  secretRef: string;
  apiKeyHeader: string;
  responsePath: string;
  timeoutMs: number;
  description: string;
};

const EMPTY_FORM: AgentFormState = {
  name: '',
  channel: 'text',
  target: 'http_endpoint',
  environment: 'staging',
  endpointUrl: '',
  authType: 'none',
  secretRef: '',
  apiKeyHeader: 'x-api-key',
  responsePath: 'response',
  timeoutMs: 15000,
  description: '',
};

const TARGET_OPTIONS: Record<AgentRecord['channel'], Array<{ value: AgentRecord['target']; label: string; group: 'Live connections' | 'Testing fixtures' }>> = {
  text: [
    { value: 'http_endpoint', label: 'HTTP JSON chat endpoint', group: 'Live connections' },
    { value: 'openai_codex', label: 'Connected OpenAI prompt agent', group: 'Live connections' },
    { value: 'mock_agent', label: 'Deterministic text mock', group: 'Testing fixtures' },
    { value: 'offline_acc_fixture', label: 'Offline ACC evidence replay', group: 'Testing fixtures' },
  ],
  voice: [{ value: 'voice_fixture', label: 'Offline ACC voice evidence replay', group: 'Testing fixtures' }],
};

function channelLabel(channel: AgentRecord['channel']) {
  return channel === 'voice' ? 'Voice' : 'Text';
}

function targetLabel(target: AgentRecord['target']) {
  if (target === 'mock_agent') return 'Built-in text mock';
  if (target === 'openai_codex') return 'OpenAI endpoint (live)';
  if (target === 'offline_acc_fixture') return 'Built-in text ACC fixture';
  if (target === 'http_endpoint') return 'HTTP JSON endpoint (live)';
  return 'Built-in voice fixture';
}

function isFixtureTarget(target: AgentRecord['target']) {
  return target === 'mock_agent' || target === 'offline_acc_fixture' || target === 'voice_fixture';
}

function connectionFromForm(values: AgentFormState): AgentRecord['connection'] {
  if (values.target !== 'http_endpoint') return {};
  return {
    endpoint_url: values.endpointUrl.trim(),
    auth_type: values.authType || 'none',
    secret_ref: values.authType === 'none' ? null : values.secretRef.trim(),
    api_key_header: values.apiKeyHeader.trim() || 'x-api-key',
    response_path: values.responsePath.trim() || 'response',
    timeout_ms: values.timeoutMs,
  };
}

function formFromAgent(agent: AgentRecord): AgentFormState {
  return {
    name: agent.name,
    channel: agent.channel,
    target: agent.target,
    environment: agent.environment || 'local',
    endpointUrl: agent.connection?.endpoint_url || '',
    authType: agent.connection?.auth_type || 'none',
    secretRef: agent.connection?.secret_ref || '',
    apiKeyHeader: agent.connection?.api_key_header || 'x-api-key',
    responsePath: agent.connection?.response_path || 'response',
    timeoutMs: agent.connection?.timeout_ms || 15000,
    description: agent.description ?? '',
  };
}

function agentInitials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return 'AG';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] ?? ''}${parts[1][0] ?? ''}`.toUpperCase();
}

function AgentAvatar({ agent }: { agent: AgentRecord }) {
  const isVoice = agent.channel === 'voice';
  return (
    <div className={`agents-avatar ${isVoice ? 'is-voice' : 'is-text'}`} aria-hidden="true">
      <span>{agentInitials(agent.name)}</span>
    </div>
  );
}

function AgentConfigRows({ agent }: { agent: AgentRecord }) {
  const endpoint = agent.connection?.endpoint_url
    ? (() => {
        try {
          return new URL(agent.connection?.endpoint_url || '').origin;
        } catch {
          return agent.connection?.endpoint_url || '—';
        }
      })()
    : null;
  const rows = [
    { label: 'Channel', value: channelLabel(agent.channel) },
    { label: 'Connection', value: targetLabel(agent.target) },
    { label: 'Environment', value: agent.environment || 'local' },
    ...(endpoint ? [{ label: 'Endpoint', value: endpoint, detail: `Reply path ${agent.connection?.response_path || 'response'}` }] : []),
    {
      label: 'Evidence',
      value: isFixtureTarget(agent.target) ? 'Fixture-backed' : agent.target === 'http_endpoint' ? 'Black-box response' : 'Provider response',
    },
    {
      label: 'Target ID',
      value: agent.id,
      detail: agent.metadata?.model_name ? `Model ${agent.metadata.model_name}` : undefined,
    },
    {
      label: 'Prompt version',
      value: agent.metadata?.prompt_version || '—',
    },
  ];

  return (
    <dl className="agents-config-list">
      {rows.map((row) => (
        <div key={row.label} className="agents-config-row">
          <dt>{row.label}</dt>
          <dd>
            <span>{row.value}</span>
            {row.detail ? <small>{row.detail}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function AgentCardMenu({
  agent,
  onEdit,
  onDelete,
}: {
  agent: AgentRecord;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="agents-card-menu" ref={menuRef}>
      <button
        type="button"
        className="agents-card-menu-trigger"
        aria-label={`Actions for ${agent.name}`}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        ⋯
      </button>
      {open ? (
        <div className="agents-card-menu-panel" role="menu">
          <button type="button" role="menuitem" onClick={() => { setOpen(false); onEdit(); }}>
            Edit
          </button>
          <button
            type="button"
            role="menuitem"
            className="is-danger"
            onClick={() => { setOpen(false); onDelete(); }}
          >
            Delete
          </button>
        </div>
      ) : null}
    </div>
  );
}

function AgentFormModal({
  title,
  submitLabel,
  initial,
  saving,
  onClose,
  onSubmit,
}: {
  title: string;
  submitLabel: string;
  initial: AgentFormState;
  saving: boolean;
  onClose: () => void;
  onSubmit: (values: AgentFormState) => Promise<void>;
}) {
  const titleId = useId();
  const [name, setName] = useState(initial.name);
  const [channel, setChannel] = useState(initial.channel);
  const [target, setTarget] = useState(initial.target);
  const [environment, setEnvironment] = useState(initial.environment);
  const [endpointUrl, setEndpointUrl] = useState(initial.endpointUrl);
  const [authType, setAuthType] = useState(initial.authType);
  const [secretRef, setSecretRef] = useState(initial.secretRef);
  const [apiKeyHeader, setApiKeyHeader] = useState(initial.apiKeyHeader);
  const [responsePath, setResponsePath] = useState(initial.responsePath);
  const [timeoutMs, setTimeoutMs] = useState(initial.timeoutMs);
  const [description, setDescription] = useState(initial.description);

  useEffect(() => {
    setName(initial.name);
    setChannel(initial.channel);
    setTarget(initial.target);
    setEnvironment(initial.environment);
    setEndpointUrl(initial.endpointUrl);
    setAuthType(initial.authType);
    setSecretRef(initial.secretRef);
    setApiKeyHeader(initial.apiKeyHeader);
    setResponsePath(initial.responsePath);
    setTimeoutMs(initial.timeoutMs);
    setDescription(initial.description);
  }, [initial]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await onSubmit({ name, channel, target, environment, endpointUrl, authType, secretRef, apiKeyHeader, responsePath, timeoutMs, description });
  }

  function onChannelChange(nextChannel: AgentRecord['channel']) {
    setChannel(nextChannel);
    if (!TARGET_OPTIONS[nextChannel].some((option) => option.value === target)) {
      setTarget(TARGET_OPTIONS[nextChannel][0].value);
    }
  }

  return (
    <div className="agents-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="agents-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="agents-modal-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="agents-modal-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <form className="agents-modal-form" onSubmit={(event) => void handleSubmit(event)}>
          <label>
            <span>Name</span>
            <input required value={name} onChange={(event) => setName(event.target.value)} placeholder="Billing support — staging" />
          </label>
          <label>
            <span>Channel</span>
            <select
              aria-label="Target channel"
              value={channel}
              onChange={(event) => onChannelChange(event.target.value as AgentRecord['channel'])}
            >
              <option value="text">Text</option>
              <option value="voice">Voice</option>
            </select>
          </label>
          <label>
            <span>Connection adapter</span>
            <select
              aria-label="Target connection"
              value={target}
              onChange={(event) => setTarget(event.target.value as AgentRecord['target'])}
            >
              {(['Live connections', 'Testing fixtures'] as const).map((group) => {
                const options = TARGET_OPTIONS[channel].filter((option) => option.group === group);
                return options.length ? (
                  <optgroup key={group} label={group}>
                    {options.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </optgroup>
                ) : null;
              })}
            </select>
          </label>
          {isFixtureTarget(target) ? (
            <div className="agents-form-notice" role="note">
              <strong>Testing fixture</strong>
              <span>This replays generated or saved evidence. It does not contact a deployed agent.</span>
            </div>
          ) : (
            <label>
              <span>Environment</span>
              <select aria-label="Target environment" value={environment} onChange={(event) => setEnvironment(event.target.value as AgentFormState['environment'])}>
                <option value="local">Local</option>
                <option value="staging">Staging</option>
                <option value="production">Production</option>
              </select>
            </label>
          )}
          {target === 'http_endpoint' ? (
            <fieldset className="agents-connection-fields">
              <legend>HTTP JSON contract</legend>
              <p>
                We POST <code>{'{"message":"…","history":[…],"scenario":{…}}'}</code> and read reply text from the response path.
              </p>
              <label>
                <span>Endpoint URL</span>
                <input required type="url" aria-label="Endpoint URL" value={endpointUrl} onChange={(event) => setEndpointUrl(event.target.value)} placeholder="https://staging.example.com/chat" />
              </label>
              <div className="agents-form-grid">
                <label>
                  <span>Authentication</span>
                  <select aria-label="Target authentication" value={authType} onChange={(event) => setAuthType(event.target.value as AgentFormState['authType'])}>
                    <option value="none">None</option>
                    <option value="bearer_secret">Bearer token from environment</option>
                    <option value="api_key_secret">API key from environment</option>
                  </select>
                </label>
                {authType !== 'none' ? (
                  <label>
                    <span>Secret environment variable</span>
                    <input required aria-label="Secret environment variable" value={secretRef} onChange={(event) => setSecretRef(event.target.value)} placeholder="SUPPORT_AGENT_TOKEN" pattern="[A-Za-z_][A-Za-z0-9_]*" />
                  </label>
                ) : null}
                {authType === 'api_key_secret' ? (
                  <label>
                    <span>API key header</span>
                    <input required aria-label="API key header" value={apiKeyHeader} onChange={(event) => setApiKeyHeader(event.target.value)} />
                  </label>
                ) : null}
                <label>
                  <span>Response text path</span>
                  <input required aria-label="Response text path" value={responsePath} onChange={(event) => setResponsePath(event.target.value)} placeholder="response" />
                </label>
                <label>
                  <span>Timeout (ms)</span>
                  <input required aria-label="Target timeout" type="number" min={500} max={120000} value={timeoutMs} onChange={(event) => setTimeoutMs(Number(event.target.value) || 15000)} />
                </label>
              </div>
              <small>Only the environment-variable name is saved. Raw credentials are never stored in the target registry.</small>
            </fieldset>
          ) : null}
          {channel === 'voice' ? (
            <div className="agents-form-notice" role="note">
              <strong>Live voice adapters are not enabled yet</strong>
              <span>Browser WebRTC/Pipecat and SIP/phone connections remain planned until end-to-end media proof is available.</span>
            </div>
          ) : null}
          <label>
            <span>Description</span>
            <textarea
              rows={4}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Optional notes — e.g. staging endpoint for the support bot under test."
            />
          </label>
          <div className="agents-modal-actions">
            <button type="button" className="secondary-link" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="primary-link" disabled={saving}>
              {saving ? 'Saving…' : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function AgentsPage() {
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [editingAgent, setEditingAgent] = useState<AgentRecord | null>(null);
  const [apiBaseOverride, setApiBaseOverride] = useState<string | null>(null);

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
        if (active) setError(err instanceof Error ? err.message : 'Could not load agent targets');
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setApiBaseOverride(new URLSearchParams(window.location.search).get('api_base'));
  }, []);

  async function onCreate(values: AgentFormState) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await createAgent({
        name: values.name.trim(),
        channel: values.channel,
        target: values.target,
        environment: isFixtureTarget(values.target) ? 'local' : values.environment,
        connection: connectionFromForm(values),
        description: values.description.trim() || null,
      });
      setShowCreate(false);
      setMessage('Agent target created.');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create agent target');
    } finally {
      setSaving(false);
    }
  }

  async function onEdit(agentId: string, values: AgentFormState) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await updateAgent(agentId, {
        name: values.name.trim(),
        channel: values.channel,
        target: values.target,
        environment: isFixtureTarget(values.target) ? 'local' : values.environment,
        connection: connectionFromForm(values),
        description: values.description.trim() || null,
      });
      setEditingAgent(null);
      setMessage('Agent target updated.');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update agent target');
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(agent: AgentRecord) {
    if (!window.confirm(`Delete target “${agent.name}”?`)) return;
    setError(null);
    setMessage(null);
    try {
      await deleteAgent(agent.id);
      setMessage('Agent target deleted.');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete agent target');
    }
  }

  return (
    <main className="page-shell agents-shell">
      <SiteNav current="agents" />

      <header className="agents-page-header">
        <div>
          <p className="eyebrow">Agent targets</p>
          <h1 id="agents-title">Targets</h1>
          <p className="agents-page-lede">
            Connections to systems under test. Built-in entries are testing targets for text and voice;
            add your own to point personas and runs at an existing endpoint.
          </p>
        </div>
        <button type="button" className="agents-add-button" onClick={() => setShowCreate(true)}>
          <span aria-hidden="true">+</span>
          Add agent target
        </button>
      </header>

      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {message ? <p className="scenarios-muted">{message}</p> : null}

      <section className="agents-grid" aria-label="Agent targets">
        {agents.map((agent) => (
          <article key={agent.id} className="agents-card">
            <div className="agents-card-top">
              <div className="agents-card-identity">
                <AgentAvatar agent={agent} />
                <div className="agents-card-title-block">
                  <h2>{agent.name}</h2>
                  <div className="agents-badges">
                    <span className="agents-badge agents-badge-channel">{channelLabel(agent.channel)}</span>
                    {isBuiltInAgent(agent) ? (
                      <span className="agents-badge agents-badge-builtin">Built-in testing target</span>
                    ) : (
                      <span className="agents-badge agents-badge-channel">Custom target</span>
                    )}
                  </div>
                </div>
              </div>
              <AgentCardMenu
                agent={agent}
                onEdit={() => setEditingAgent(agent)}
                onDelete={() => void onDelete(agent)}
              />
            </div>

            <a className="agents-try-button" href={agentTryItOutHref(agent.id, apiBaseOverride)}>
              <span className="agents-try-icon" aria-hidden="true">
                {agent.channel === 'voice' ? '☎' : '✎'}
              </span>
              Try it Out
            </a>

            <section className="agents-card-section">
              <h3>Description</h3>
              <p>{agent.description?.trim() || 'No description provided.'}</p>
            </section>

            <hr className="agents-card-divider" />

            <section className="agents-card-section">
              <h3>Configuration</h3>
              <AgentConfigRows agent={agent} />
            </section>
          </article>
        ))}
      </section>

      {!agents.length && !error ? (
        <section className="agents-empty card">
          <h2>No agent targets yet</h2>
          <p className="scenarios-muted">
            Add a text or voice testing target — built-in mocks/fixtures or a connection to your own endpoint.
          </p>
          <button type="button" className="primary-link" onClick={() => setShowCreate(true)}>
            Add agent target
          </button>
        </section>
      ) : null}

      {showCreate ? (
        <AgentFormModal
          title="Add agent target"
          submitLabel="Create target"
          initial={EMPTY_FORM}
          saving={saving}
          onClose={() => setShowCreate(false)}
          onSubmit={onCreate}
        />
      ) : null}

      {editingAgent ? (
        <AgentFormModal
          title={`Edit ${editingAgent.name}`}
          submitLabel="Save changes"
          initial={formFromAgent(editingAgent)}
          saving={saving}
          onClose={() => setEditingAgent(null)}
          onSubmit={(values) => onEdit(editingAgent.id, values)}
        />
      ) : null}
    </main>
  );
}
