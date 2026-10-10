'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  connectWaylo, disconnectWaylo, getWayloConnection, isWayloLocalControlPage,
  refreshWayloConnection, subscribeWayloConnection, loadWayloConnectionConfig, WAYLO_MIKE_AGENT_ID,
  type WayloConnectionInfo, type WayloTargetConnection,
} from '@/lib/wayloConnection';

export function useWayloConnection(connection?: WayloTargetConnection) {
  const [, update] = useState(0);
  useEffect(() => subscribeWayloConnection(() => update((value) => value + 1)), []);
  return getWayloConnection(connection);
}

/** Shows only the tab-bound grant, never infers connectivity from saved target IDs. */
export function WayloConnectionStatus({ connection }: { connection: WayloTargetConnection }) {
  const active = useWayloConnection(connection);
  return <div className={`waylo-card-status ${active ? 'is-connected' : 'is-disconnected'}`} role="status" aria-label="Waylo connection status">
    <p><span aria-hidden="true">● </span><strong>{active ? 'Connected in this tab' : 'Not connected'}</strong></p>
    <p>{active
      ? `Temporary access · expires ${new Date(active.expires_at).toLocaleTimeString()}.`
      : 'Show details to connect. Reloading this page requires sign-in again.'}</p>
  </div>;
}

export function WayloConnection({ connection, agentId, onConnected, onEndpoint, compact = false }: {
  connection?: WayloTargetConnection;
  agentId?: string;
  onConnected?: (info: WayloConnectionInfo) => void;
  onEndpoint?: (endpoint: string) => void;
  compact?: boolean;
}) {
  const active = useWayloConnection(connection);
  const [local, setLocal] = useState(false);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [requestedAgent, setRequestedAgent] = useState(agentId || connection?.waylo_agent_id || WAYLO_MIKE_AGENT_ID);
  const [tenant, setTenant] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [hasPassword, setHasPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const passwordInput = useRef<HTMLInputElement | null>(null);
  const passwordRef = useCallback((input: HTMLInputElement | null) => {
    if (!input && passwordInput.current) passwordInput.current.value = '';
    passwordInput.current = input;
  }, []);
  const seenConnection = useRef(false);
  const endpointCallback = useRef(onEndpoint);
  endpointCallback.current = onEndpoint;

  useEffect(() => {
    let mounted = true;
    const enabled = isWayloLocalControlPage();
    setLocal(enabled);
    if (enabled) void loadWayloConnectionConfig().then((value) => {
      if (mounted) { setEndpoint(value); endpointCallback.current?.(value); }
    }).catch((error) => { if (mounted) setMessage(error instanceof Error ? error.message : 'Could not load Waylo configuration.'); });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    setRequestedAgent(agentId || connection?.waylo_agent_id || WAYLO_MIKE_AGENT_ID);
  }, [agentId, connection?.waylo_agent_id]);
  useEffect(() => {
    if (active) seenConnection.current = true;
    else if (seenConnection.current) setMessage('Temporary connection ended or expired. Reconnect to start another call.');
  }, [active?.connected, active?.expires_at]);
  useEffect(() => () => { if (passwordInput.current) passwordInput.current.value = ''; }, []);
  useEffect(() => {
    const prefix = endpoint ? `${endpoint}/admin/tenants/` : null;
    if (prefix && connection?.endpoint_url?.startsWith(prefix)) setTenant(connection.endpoint_url.slice(prefix.length).replace(/\/$/, ''));
  }, [connection?.endpoint_url, endpoint]);
  useEffect(() => {
    if (!active) return;
    void refreshWayloConnection(connection).catch(() => setMessage('Could not confirm the server connection. Refresh before starting a call.'));
    // The public status subscription handles expiry; no background provider traffic.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection?.endpoint_url, connection?.workspace_id, connection?.waylo_agent_id]);

  async function connect() {
    if (!confirmed || !endpoint || !email.trim() || !passwordInput.current?.value || !requestedAgent.trim()) return;
    setBusy(true); setMessage('');
    try {
      const pending = connectWaylo({ email: email.trim(), password: passwordInput.current.value,
        waylo_agent_id: requestedAgent.trim().toLowerCase(), ...(tenant.trim() ? { tenant_id: tenant.trim().toLowerCase() } : {}),
        confirm: true, expected_endpoint_url: endpoint });
      passwordInput.current.value = '';
      setHasPassword(false);
      const info = await pending;
      onConnected?.(info);
      setConfirmed(false);
      setMessage('Connected for this browser tab only. Starting a call still requires your explicit launch.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Waylo connection failed.');
    } finally {
      if (passwordInput.current) passwordInput.current.value = '';
      setHasPassword(false);
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true); setMessage('');
    try { await disconnectWaylo(connection); seenConnection.current = false; setMessage('Disconnected. CAE is stopping any active call; remote processing may take a moment to end.'); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Disconnect failed.'); }
    finally { setBusy(false); }
  }

  const content = <>
    <p>Sign-in sends your credentials once to <span>{endpoint || 'the configured Waylo API (loading)'}</span> through local CAE. The temporary connection lasts at most 15 minutes. Reload requires reconnecting; sign-in never starts a call.</p>
    <details className="waylo-privacy-details">
      <summary>Credential handling details</summary>
      <p>Your password is immediately cleared from the form. CAE keeps the provider token only in server memory until expiry. The private control proof stays in this tab’s JavaScript memory. Neither is saved with the target or in browser storage; no refresh cookie is retained.</p>
    </details>
    {!local ? <p>Open local CAE on localhost or 127.0.0.1 to connect.</p> : active ? <>
      <dl className="waylo-bound-target">
        <div><dt>Workspace</dt><dd>{active.workspace_id}</dd></div>
        <div><dt>Waylo agent</dt><dd>{active.waylo_agent_id}</dd></div>
      </dl>
      <div className="waylo-action-row">
        <button type="button" className="secondary-link" disabled={busy} onClick={() => void disconnect()}>Disconnect Waylo</button>
        <button type="button" className="secondary-link" disabled={busy} onClick={() => void refreshWayloConnection(connection).catch(() => setMessage('Could not check the connection.'))}>Refresh Waylo connection</button>
      </div>
    </> : <div className="waylo-connection-fields">
      <label><span>Waylo email</span><input type="email" aria-label="Waylo email" autoComplete="off" value={email} onChange={(event) => setEmail(event.target.value)} disabled={busy} /></label>
      <label><span>Waylo password</span><input type="password" aria-label="Waylo password" autoComplete="off" ref={passwordRef} onChange={(event) => setHasPassword(Boolean(event.target.value))} disabled={busy || !endpoint} /></label>
      <label><span>Agent UUID to connect</span><input aria-label="Waylo sign-in agent UUID" value={requestedAgent} disabled={busy || Boolean(connection?.workspace_id)} onChange={(event) => setRequestedAgent(event.target.value)} /></label>
      <label><span>Owning tenant UUID (platform administrators only, optional)</span><input aria-label="Waylo sign-in tenant UUID" value={tenant} onChange={(event) => setTenant(event.target.value)} disabled={busy} /></label>
      <label className="waylo-consent"><input type="checkbox" aria-label="Confirm temporary Waylo sign-in" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={busy} /><span>I agree to send my sign-in credentials once to Waylo through local CAE and retain a temporary connection in memory.</span></label>
      <button type="button" className="primary-link" disabled={busy || !endpoint || !confirmed || !hasPassword || !email.trim() || !requestedAgent.trim()} onClick={() => void connect()}>{busy ? 'Connecting…' : 'Connect Waylo'}</button>
    </div>}
  </>;

  return <section className="agents-card-section waylo-connection" aria-label="Temporary Waylo connection">
    <h3>{compact ? 'Waylo connection' : 'Connect Waylo'}</h3>
    {active ? <p className="waylo-connection-status" role="status">Connected to {active.agent_name} · expires {new Date(active.expires_at).toLocaleTimeString()}.</p>
      : compact ? <p className="waylo-connection-status">Not connected in this tab. Connect before starting a call.</p> : null}
    {compact ? <details className="waylo-control-details" onToggle={(event) => {
      if (!event.currentTarget.open) {
        if (passwordInput.current) passwordInput.current.value = '';
        setHasPassword(false); setConfirmed(false);
      }
    }}>
      <summary>{active ? 'Manage connection' : 'Connect Waylo'}</summary>
      <div className="waylo-control-content">{content}</div>
    </details> : <div className="waylo-control-content">{content}</div>}
    {message ? <p role="status">{message}</p> : null}
  </section>;
}
