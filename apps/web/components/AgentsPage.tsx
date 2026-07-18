'use client';

import { FormEvent, useEffect, useId, useRef, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import {
  AccConnectionStatus,
  AgentRecord,
  AgentTarget,
  agentTryItOutHref,
  createAgent,
  deleteAgent,
  getAccConnectionStatus,
  isBuiltInAgent,
  listAgents,
  testAccConnection,
  updateAgent,
} from '@/lib/execution';

type AgentFormState = {
  name: string;
  channel: AgentRecord['channel'];
  target: AgentTarget;
  description: string;
  sip_uri: string;
  phone_number: string;
  acc_base_url: string;
};

type DestinationOption = {
  value: AgentTarget;
  label: string;
  disabled?: boolean;
  hint?: string;
};

const EMPTY_FORM: AgentFormState = {
  name: '',
  channel: 'text',
  target: 'mock_agent',
  description: '',
  sip_uri: '',
  phone_number: '',
  acc_base_url: '',
};

function textDestinations(): DestinationOption[] {
  return [
    { value: 'mock_agent', label: 'Built-in sample agent' },
    { value: 'openai_codex', label: 'OpenAI text endpoint' },
    { value: 'offline_acc_fixture', label: 'Built-in text fixture' },
  ];
}

function voiceDestinations(connection: AccConnectionStatus | null): DestinationOption[] {
  const destination = (target: AgentTarget) => connection?.destinations?.[target];
  const option = (value: AgentTarget, label: string): DestinationOption => {
    const capability = destination(value);
    const ready = connection?.connected === true && capability?.creatable === true;
    return {
      value,
      label,
      disabled: !ready,
      hint: capability?.label || (connection?.connected ? 'Not ready in this ACC runtime' : 'Connect ACC to enable'),
    };
  };
  return [
    { value: 'builtin_sample_voice', label: 'Built-in sample agent' },
    option('browser_webrtc_agent', 'Browser/WebRTC agent'),
    option('sip_agent', 'SIP agent'),
    option('phone_agent', 'Phone agent'),
  ];
}

function channelLabel(channel: AgentRecord['channel']) {
  return channel === 'voice' ? 'Voice' : 'Text';
}

function targetLabel(target: AgentRecord['target']) {
  if (target === 'mock_agent') return 'Built-in sample text agent';
  if (target === 'openai_codex') return 'OpenAI text endpoint';
  if (target === 'offline_acc_fixture') return 'Built-in text fixture';
  if (target === 'sip_agent') return 'SIP agent';
  if (target === 'phone_agent') return 'Phone agent';
  if (target === 'browser_webrtc_agent') return 'Browser/WebRTC agent';
  return 'Built-in sample voice agent';
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
  const rows = [
    { label: 'Channel', value: channelLabel(agent.channel) },
    { label: 'Destination', value: targetLabel(agent.target) },
    agent.sip_uri ? { label: 'SIP URI', value: agent.sip_uri } : null,
    agent.phone_number ? { label: 'Phone', value: agent.phone_number } : null,
    {
      label: 'Target ID',
      value: agent.id,
      detail: agent.metadata?.model_name ? `Model ${agent.metadata.model_name}` : undefined,
    },
    {
      label: 'Prompt version',
      value: agent.metadata?.prompt_version || '—',
    },
  ].filter(Boolean) as Array<{ label: string; value: string; detail?: string }>;

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
  accConnection,
  onAccConnectionChange,
  onClose,
  onSubmit,
}: {
  title: string;
  submitLabel: string;
  initial: AgentFormState;
  saving: boolean;
  accConnection: AccConnectionStatus | null;
  onAccConnectionChange: (status: AccConnectionStatus) => void;
  onClose: () => void;
  onSubmit: (values: AgentFormState) => Promise<void>;
}) {
  const titleId = useId();
  const [name, setName] = useState(initial.name);
  const [channel, setChannel] = useState(initial.channel);
  const [target, setTarget] = useState(initial.target);
  const [description, setDescription] = useState(initial.description);
  const [sipUri, setSipUri] = useState(initial.sip_uri);
  const [phoneNumber, setPhoneNumber] = useState(initial.phone_number);
  const [accBaseUrl, setAccBaseUrl] = useState(
    initial.acc_base_url || accConnection?.base_url || 'http://127.0.0.1:8026',
  );
  const [connectionStatus, setConnectionStatus] = useState(accConnection);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const destinations = channel === 'voice' ? voiceDestinations(connectionStatus) : textDestinations();
  const selectedDestination = destinations.find((item) => item.value === target);
  const destinationDisabled = Boolean(selectedDestination?.disabled);

  useEffect(() => {
    setConnectionStatus(accConnection);
    if (accConnection?.base_url) setAccBaseUrl(accConnection.base_url);
  }, [accConnection]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (destinationDisabled) return;
    await onSubmit({
      name,
      channel,
      target,
      description,
      sip_uri: sipUri,
      phone_number: phoneNumber,
      acc_base_url: accBaseUrl,
    });
  }

  function onChannelChange(nextChannel: AgentRecord['channel']) {
    setChannel(nextChannel);
    const nextOptions = nextChannel === 'voice' ? voiceDestinations(connectionStatus) : textDestinations();
    const firstCreatable = nextOptions.find((item) => !item.disabled) || nextOptions[0];
    setTarget(firstCreatable.value);
    setSipUri('');
    setPhoneNumber('');
  }

  async function testConnection() {
    setTestingConnection(true);
    setConnectionError(null);
    try {
      const status = await testAccConnection(accBaseUrl);
      setConnectionStatus(status);
      onAccConnectionChange(status);
      if (status.base_url) {
        setAccBaseUrl(status.base_url);
        window.localStorage.setItem('conversation-evals-acc-base-url', status.base_url);
      }
      if (!status.connected) setConnectionError(status.message);
    } catch (err) {
      setConnectionError(err instanceof Error ? err.message : 'Could not test ACC connection.');
    } finally {
      setTestingConnection(false);
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
            <input required value={name} onChange={(event) => setName(event.target.value)} placeholder="Support bot endpoint" />
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
          <fieldset className="agents-destination-fieldset">
            <legend>Destination</legend>
            <p className="agents-form-help">
              Destination is the agent under test. Choose the executor (local audio loop vs ACC) when launching a run.
            </p>
            {channel === 'voice' ? (
              <div className="agents-acc-connect">
                <div>
                  <strong>Agentic Contact Center</strong>
                  <span>
                    {connectionStatus?.connected
                      ? `Connected to ${connectionStatus.base_url}`
                      : 'Connect ACC to unlock its available live destinations.'}
                  </span>
                </div>
                <div className="agents-acc-connect-controls">
                  <input
                    aria-label="ACC base URL"
                    value={accBaseUrl}
                    onChange={(event) => {
                      setAccBaseUrl(event.target.value);
                      setConnectionStatus(null);
                      setConnectionError(null);
                    }}
                    placeholder="http://127.0.0.1:8026"
                  />
                  <button
                    type="button"
                    className="secondary-link"
                    disabled={testingConnection || !accBaseUrl.trim()}
                    onClick={() => void testConnection()}
                  >
                    {testingConnection ? 'Testing…' : connectionStatus?.connected ? 'Test again' : 'Test connection'}
                  </button>
                </div>
                {connectionStatus?.connected ? (
                  <small className="agents-connection-success">Connection verified. Ready destinations are enabled below.</small>
                ) : null}
                {connectionError ? <small className="agents-connection-error" role="alert">{connectionError}</small> : null}
              </div>
            ) : null}
            <div className="agents-destination-list" role="radiogroup" aria-label="Target destination">
              {destinations.map((option) => (
                <label
                  key={option.value}
                  className={`agents-destination-card${option.disabled ? ' is-disabled' : ''}${target === option.value ? ' is-selected' : ''}`}
                >
                  <input
                    type="radio"
                    name="destination"
                    value={option.value}
                    checked={target === option.value}
                    disabled={option.disabled}
                    onChange={() => setTarget(option.value)}
                  />
                  <span>
                    <strong>{option.label}</strong>
                    {option.disabled ? <em>{option.hint || 'Requires ACC connection'}</em> : null}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {target === 'sip_agent' && !destinationDisabled ? (
            <label>
              <span>SIP URI</span>
              <input
                required
                aria-label="SIP URI"
                value={sipUri}
                onChange={(event) => setSipUri(event.target.value)}
                placeholder="sip:agent@example.com:5060"
              />
            </label>
          ) : null}
          {target === 'phone_agent' && !destinationDisabled ? (
            <label>
              <span>Phone number (E.164)</span>
              <input
                required
                aria-label="Phone number"
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
                placeholder="+12125550123"
              />
            </label>
          ) : null}
          {target === 'browser_webrtc_agent' && !destinationDisabled ? (
            <p className="agents-form-help">
              This target will use the verified ACC browser WebRTC session endpoint.
            </p>
          ) : null}
          {channel === 'voice' && target === 'builtin_sample_voice' ? (
            <div className="agents-form-notice" role="note">
              <strong>Built-in sample voice call</strong>
              <span>
                Creatable without ACC. At launch, the default executor is the CAE local audio loop — no phone or SIP call.
                Saved conversation replay lives on the Eval evidence path, not as a destination here.
              </span>
            </div>
          ) : null}
          {destinationDisabled ? (
            <div className="agents-form-notice" role="note">
              <strong>{selectedDestination?.hint || 'Requires ACC connection'}</strong>
              <span>
                {connectionStatus?.connected
                  ? 'This ACC runtime responded, but this destination is not ready.'
                  : 'Enter the ACC URL above and choose Test connection.'}
              </span>
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
            <button type="submit" className="primary-link" disabled={saving || destinationDisabled}>
              {saving ? 'Saving…' : destinationDisabled ? 'Requires ACC connection' : submitLabel}
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
  const [accConnection, setAccConnection] = useState<AccConnectionStatus | null>(null);

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
    const savedAccUrl = window.localStorage.getItem('conversation-evals-acc-base-url') || undefined;
    getAccConnectionStatus(savedAccUrl)
      .then((status) => {
        if (active) setAccConnection(status);
      })
      .catch(() => {
        if (active) {
          setAccConnection({
            connected: false,
            status: 'requires_acc_connection',
            label: 'Requires ACC connection',
            message: 'Could not read ACC connection status.',
          });
        }
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
        description: values.description.trim() || null,
        sip_uri: values.sip_uri.trim() || null,
        phone_number: values.phone_number.trim() || null,
        acc_base_url: values.acc_base_url.trim() || null,
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
        description: values.description.trim() || null,
        sip_uri: values.sip_uri.trim() || null,
        phone_number: values.phone_number.trim() || null,
        acc_base_url: values.acc_base_url.trim() || null,
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
            Destinations for the agent under test. Executors (local audio loop, ACC SIP/phone/browser) are chosen
            when you launch a run — not when you create a target.
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
                      <span className="agents-badge agents-badge-builtin">Built-in sample</span>
                    ) : (
                      <span className="agents-badge agents-badge-channel">Custom destination</span>
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
            Add a text or voice destination — built-in sample agent, or an ACC-backed SIP/phone/browser agent once connected.
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
          accConnection={accConnection}
          onAccConnectionChange={setAccConnection}
          onClose={() => setShowCreate(false)}
          onSubmit={onCreate}
        />
      ) : null}

      {editingAgent ? (
        <AgentFormModal
          title={`Edit ${editingAgent.name}`}
          submitLabel="Save changes"
          initial={{
            name: editingAgent.name,
            channel: editingAgent.channel,
            target: editingAgent.target === 'voice_fixture' ? 'builtin_sample_voice' : editingAgent.target,
            description: editingAgent.description ?? '',
            sip_uri: editingAgent.sip_uri ?? '',
            phone_number: editingAgent.phone_number ?? '',
            acc_base_url: editingAgent.acc_base_url ?? '',
          }}
          saving={saving}
          accConnection={accConnection}
          onAccConnectionChange={setAccConnection}
          onClose={() => setEditingAgent(null)}
          onSubmit={(values) => onEdit(editingAgent.id, values)}
        />
      ) : null}
    </main>
  );
}
