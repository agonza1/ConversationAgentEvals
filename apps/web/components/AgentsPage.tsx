'use client';

import { FormEvent, useEffect, useId, useRef, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import {
  AccConnectionStatus,
  AgentRecord,
  agentTryItOutHref,
  createAgent,
  deleteAgent,
  isBuiltInAgent,
  listAgents,
  testAccConnection,
  updateAgent,
} from '@/lib/execution';

type FormTarget = AgentRecord['target'];

type AgentFormState = {
  name: string;
  channel: AgentRecord['channel'];
  target: FormTarget;
  environment: NonNullable<AgentRecord['environment']>;
  endpointUrl: string;
  authType: 'none' | 'bearer_secret' | 'api_key_secret';
  secretRef: string;
  apiKeyHeader: string;
  responsePath: string;
  timeoutMs: number;
  sipUri: string;
  phoneNumber: string;
  accBaseUrl: string;
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
  sipUri: '',
  phoneNumber: '',
  accBaseUrl: 'http://127.0.0.1:8026',
  description: '',
};

const TARGET_OPTIONS: Record<
  AgentRecord['channel'],
  Array<{
    value: FormTarget;
    label: string;
    group: 'Live connections' | 'Built-in samples';
    comingSoon?: boolean;
  }>
> = {
  text: [
    { value: 'http_endpoint', label: 'HTTP JSON chat endpoint', group: 'Live connections' },
    { value: 'openai_codex', label: 'Connected OpenAI prompt agent', group: 'Live connections' },
    { value: 'mock_agent', label: 'Built-in sample text agent', group: 'Built-in samples' },
  ],
  voice: [
    { value: 'browser_webrtc_agent', label: 'ACC browser WebRTC (coming soon)', group: 'Live connections', comingSoon: true },
    { value: 'sip_agent', label: 'ACC SIP URI (coming soon)', group: 'Live connections', comingSoon: true },
    { value: 'phone_agent', label: 'ACC phone number (coming soon)', group: 'Live connections', comingSoon: true },
    { value: 'builtin_sample_voice', label: 'Built-in sample voice agent', group: 'Built-in samples' },
  ],
};

function isComingSoonTarget(target: FormTarget) {
  return TARGET_OPTIONS.text.concat(TARGET_OPTIONS.voice).some((option) => option.value === target && option.comingSoon);
}

function channelLabel(channel: AgentRecord['channel']) {
  return channel === 'voice' ? 'Voice' : 'Text';
}

function targetLabel(target: FormTarget) {
  if (target === 'mock_agent') return 'Built-in sample text agent';
  if (target === 'openai_codex') return 'OpenAI endpoint (live)';
  if (target === 'offline_acc_fixture') return 'Saved ACC text replay';
  if (target === 'http_endpoint') return 'HTTP JSON endpoint (live)';
  if (target === 'browser_webrtc_agent') return 'ACC browser WebRTC (coming soon)';
  if (target === 'sip_agent') return 'ACC SIP URI (coming soon)';
  if (target === 'phone_agent') return 'ACC phone number (coming soon)';
  if (target === 'builtin_sample_voice') return 'Built-in sample voice agent';
  return 'Legacy saved voice replay';
}

function isBuiltInSampleTarget(target: FormTarget) {
  return target === 'mock_agent' || target === 'builtin_sample_voice';
}

function isSavedReplayTarget(target: FormTarget) {
  return target === 'offline_acc_fixture' || target === 'voice_fixture';
}

function isAccTarget(target: FormTarget) {
  return target === 'sip_agent' || target === 'phone_agent' || target === 'browser_webrtc_agent';
}

function connectionFromForm(values: AgentFormState): AgentRecord['connection'] {
  if (values.target === 'http_endpoint') {
    return {
      endpoint_url: values.endpointUrl.trim(),
      auth_type: values.authType || 'none',
      secret_ref: values.authType === 'none' ? null : values.secretRef.trim(),
      api_key_header: values.apiKeyHeader.trim() || 'x-api-key',
      response_path: values.responsePath.trim() || 'response',
      timeout_ms: values.timeoutMs,
    };
  }
  if (isAccTarget(values.target)) {
    return {
      acc_base_url: values.accBaseUrl.trim(),
      sip_uri: values.target === 'sip_agent' ? values.sipUri.trim() : null,
      phone_number: values.target === 'phone_agent' ? values.phoneNumber.trim() : null,
    };
  }
  return {};
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
    sipUri: agent.connection?.sip_uri || '',
    phoneNumber: agent.connection?.phone_number || '',
    accBaseUrl: agent.connection?.acc_base_url || 'http://127.0.0.1:8026',
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
    ...(agent.connection?.sip_uri ? [{ label: 'Destination', value: agent.connection.sip_uri }] : []),
    ...(agent.connection?.phone_number ? [{ label: 'Destination', value: agent.connection.phone_number }] : []),
    ...(agent.connection?.acc_base_url ? [{ label: 'ACC', value: agent.connection.acc_base_url }] : []),
    {
      label: 'Evidence',
      value: isBuiltInSampleTarget(agent.target)
        ? 'Generated during the run'
        : isSavedReplayTarget(agent.target)
          ? 'Saved replay'
          : agent.target === 'http_endpoint'
            ? 'Black-box response'
            : 'Provider response',
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
  const [sipUri, setSipUri] = useState(initial.sipUri);
  const [phoneNumber, setPhoneNumber] = useState(initial.phoneNumber);
  const [accBaseUrl, setAccBaseUrl] = useState(initial.accBaseUrl);
  const [accStatus, setAccStatus] = useState<AccConnectionStatus | null>(null);
  const [testingAcc, setTestingAcc] = useState(false);
  const [description, setDescription] = useState(initial.description);
  const comingSoon = isComingSoonTarget(target);

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
    setSipUri(initial.sipUri);
    setPhoneNumber(initial.phoneNumber);
    setAccBaseUrl(initial.accBaseUrl);
    setAccStatus(null);
    setDescription(initial.description);
  }, [initial]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (comingSoon) return;
    await onSubmit({
      name,
      channel,
      target,
      environment,
      endpointUrl,
      authType,
      secretRef,
      apiKeyHeader,
      responsePath,
      timeoutMs,
      sipUri,
      phoneNumber,
      accBaseUrl,
      description,
    });
  }

  function onChannelChange(nextChannel: AgentRecord['channel']) {
    setChannel(nextChannel);
    if (!TARGET_OPTIONS[nextChannel].some((option) => option.value === target)) {
      setTarget(TARGET_OPTIONS[nextChannel][0].value);
    }
  }

  async function onTestAcc() {
    setTestingAcc(true);
    try {
      const result = await testAccConnection(accBaseUrl);
      setAccStatus(result);
    } catch (error) {
      setAccStatus({
        connected: false,
        status: 'error',
        label: 'ACC connection failed',
        message: error instanceof Error ? error.message : 'Could not test ACC connection',
      });
    } finally {
      setTestingAcc(false);
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
              onChange={(event) => setTarget(event.target.value as FormTarget)}
            >
              {(['Live connections', 'Built-in samples'] as const).map((group) => {
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
          {isBuiltInSampleTarget(target) ? (
            <div className="agents-form-notice" role="note">
              <strong>Built-in sample</strong>
              <span>Uses predictable responses generated during this run. It does not contact a deployed agent.</span>
            </div>
          ) : isSavedReplayTarget(target) ? (
            <div className="agents-form-notice" role="note">
              <strong>Saved evidence</strong>
              <span>Saved conversation replay belongs in Eval evidence and cannot be created as a new target.</span>
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
                    <option value="bearer_secret">Configured bearer credential</option>
                    <option value="api_key_secret">Configured API key</option>
                  </select>
                </label>
                {authType !== 'none' ? (
                  <label>
                    <span>Credential ID</span>
                    <input required aria-label="Credential ID" value={secretRef} onChange={(event) => setSecretRef(event.target.value)} placeholder="support-staging" pattern="[a-z][a-z0-9-]{0,63}" />
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
              <small>Ask an administrator for a configured credential ID. Environment-variable names and raw credentials are never accepted or stored here.</small>
            </fieldset>
          ) : null}
          {isAccTarget(target) ? (
            <fieldset className="agents-connection-fields">
              <legend>Agentic Contact Center connection</legend>
              <p>
                ACC owns live browser, SIP, phone/PSTN, FreeSWITCH, Verto, and production media. CAE only tests readiness here; its execution adapter is not implemented yet.
              </p>
              <label>
                <span>ACC base URL</span>
                <input
                  required
                  type="url"
                  aria-label="ACC base URL"
                  value={accBaseUrl}
                  onChange={(event) => { setAccBaseUrl(event.target.value); setAccStatus(null); }}
                  placeholder="http://127.0.0.1:8026"
                />
              </label>
              {target === 'sip_agent' ? (
                <label>
                  <span>SIP URI</span>
                  <input
                    required
                    aria-label="SIP URI"
                    value={sipUri}
                    onChange={(event) => setSipUri(event.target.value)}
                    placeholder="sip:agent@example.com"
                  />
                </label>
              ) : null}
              {target === 'phone_agent' ? (
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
              <div className="agents-connection-actions">
                <button type="button" className="secondary-link" disabled={testingAcc || !accBaseUrl.trim()} onClick={() => void onTestAcc()}>
                  {testingAcc ? 'Testing…' : 'Test ACC readiness'}
                </button>
                {accStatus ? (
                  <span role="status" className={accStatus.connected ? 'is-ready' : 'is-blocked'}>
                    {accStatus.label}: {accStatus.message}
                  </span>
                ) : null}
              </div>
              <small>
                A successful readiness check does not enable Create target until CAE can actually execute and capture evidence through this adapter. See{' '}
                <a href="https://github.com/agonza1/agentic-contact-center" target="_blank" rel="noreferrer">
                  Agentic Contact Center
                </a>
              </small>
            </fieldset>
          ) : null}
          {comingSoon ? (
            <div className="agents-form-notice" role="note">
              <strong>CAE ↔ ACC live adapter coming soon</strong>
              <span>
                Readiness and executability are separate. Create stays disabled until ConversationAgentEvals can launch the ACC session and capture evidence end to end.
              </span>
            </div>
          ) : null}
          {target === 'builtin_sample_voice' ? (
            <div className="agents-form-notice" role="note">
              <strong>Built-in sample voice call</strong>
              <span>
                Runs through the CAE local audio loop. It is not a browser, SIP, or phone call, and saved replay is not used as the target.
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
            <button type="submit" className="primary-link" disabled={saving || comingSoon} title={comingSoon ? 'CAE ↔ ACC live adapter coming soon' : undefined}>
              {saving ? 'Saving…' : comingSoon ? 'Coming soon' : submitLabel}
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
    if (isComingSoonTarget(values.target)) {
      setError('That ACC executor is not implemented in CAE yet, so this target cannot be created.');
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await createAgent({
        name: values.name.trim(),
        channel: values.channel,
        target: values.target,
        environment: isBuiltInSampleTarget(values.target) || isSavedReplayTarget(values.target) ? 'local' : values.environment,
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
    if (isComingSoonTarget(values.target)) {
      setError('That ACC executor is not implemented in CAE yet, so this target cannot be created.');
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await updateAgent(agentId, {
        name: values.name.trim(),
        channel: values.channel,
        target: values.target,
        environment: isBuiltInSampleTarget(values.target) || isSavedReplayTarget(values.target) ? 'local' : values.environment,
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
            Add a text or voice target — use a built-in sample or connect your own endpoint.
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
