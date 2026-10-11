'use client';

import { useOpenAIConnection } from './useOpenAIConnection';

export function OpenAIConnectionSettings() {
  return <OpenAIConnectionPanel connection={useOpenAIConnection()} />;
}

export function OpenAIConnectionPanel({ connection }: { connection: ReturnType<typeof useOpenAIConnection> }) {
  const { provider, message, connecting, connect, disconnect } = connection;
  return <section className="card openai-provider-panel" aria-label="OpenAI agent provider">
    <p className="eyebrow">Agent execution provider</p>
    <h2>Connect OpenAI for agent execution</h2>
    <p>{provider?.status === 'connected'
      ? `Connected${provider.email ? ` as ${provider.email}` : ''}${provider.plan_type ? ` (${provider.plan_type})` : ''}.`
      : provider?.message || 'Connect your local account for agent execution. This is separate from the evaluation judge connection.'}</p>
    {provider?.status === 'connected'
      ? <button type="button" className="secondary-link" onClick={() => void disconnect()}>Disconnect OpenAI</button>
      : <button type="button" className="primary-link" disabled={connecting} onClick={() => void connect()}>{connecting ? 'Waiting for OpenAI…' : 'Connect OpenAI'}</button>}
    {message ? <p role="status">{message}</p> : null}
  </section>;
}
