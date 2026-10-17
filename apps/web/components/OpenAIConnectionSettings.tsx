'use client';

import { useOpenAIConnection } from './useOpenAIConnection';
import { useHostedAuth } from './AuthGate';

export function OpenAIConnectionSettings() {
  const { enabled } = useHostedAuth();
  return <OpenAIConnectionPanel connection={useOpenAIConnection(!enabled)} />;
}

export function OpenAIConnectionPanel({ connection }: { connection: ReturnType<typeof useOpenAIConnection> }) {
  const { enabled } = useHostedAuth();
  if (enabled) return <section className="card settings-card"><h2>Your OpenAI connection</h2><p>AI operations use only your API key.</p><a href="/settings#agent-execution">Manage your API key in Settings</a></section>;
  const { provider, message, connecting, disconnecting, failed, connect, disconnect } = connection;
  const connected = provider?.status === 'connected';
  const checking = !provider && !message;
  const state = connecting ? 'Connecting…' : checking ? 'Checking…' : connected ? 'Connected' : provider?.status === 'expired' ? 'Expired' : 'Not connected';
  return <section id="agent-execution" className="card settings-card" aria-label="OpenAI agent provider" aria-busy={checking || connecting || disconnecting}>
    <header className="settings-card-header">
      <div><p className="eyebrow">Agent tests</p><h2>Agent execution</h2></div>
      <span className="settings-badge" data-tone={connected ? 'success' : 'neutral'}>{state}</span>
    </header>
    <p className="settings-description">The OpenAI account used by your agent tests. This connection is separate from the evaluation judge.</p>
    {connected ? <dl className="settings-summary">
      <div><dt>Account</dt><dd>{provider.email || 'OpenAI account'}</dd></div>
      {provider.plan_type ? <div><dt>Plan</dt><dd>{provider.plan_type}</dd></div> : null}
    </dl> : provider?.message ? <p className="settings-help">{provider.message}</p> : null}
    <div className="settings-actions">
      {connected
        ? <button type="button" className="secondary-link settings-danger" disabled={disconnecting || connecting} onClick={() => void disconnect()}>{disconnecting ? 'Disconnecting…' : 'Disconnect OpenAI'}</button>
        : <button type="button" className="primary-link" disabled={connecting || checking || disconnecting} onClick={() => void connect()}>{connecting ? 'Waiting for OpenAI…' : 'Connect OpenAI'}</button>}
    </div>
    {message ? <p className="settings-feedback" role={failed ? 'alert' : 'status'}>{message}</p> : null}
  </section>;
}
