/** Local Waylo control: the browser proof exists only in this module's memory.
 * Never export it as component state, put it in a target payload, or persist it.
 */
export const WAYLO_SESSION_ENDPOINT = 'https://api.poc.app.waylovoice.ai';
export const WAYLO_MIKE_AGENT_ID = 'b722ea62-524a-4664-8702-f38f2bed5cbe';

export type WayloTargetConnection = {
  endpoint_url?: string | null;
  workspace_id?: string | null;
  waylo_agent_id?: string | null;
  auth_type?: string;
};

export type WayloConnectionInfo = {
  connected: boolean;
  expires_at: string;
  endpoint_url: string;
  workspace_id: string;
  waylo_agent_id: string;
  agent_name: string;
};

type MemoryConnection = { proof: string; info: WayloConnectionInfo; timer: ReturnType<typeof setTimeout> };
const connections = new Map<string, MemoryConnection>();
const listeners = new Set<() => void>();
let currentFingerprint: string | null = null;

function normalizedProviderEndpoint(value?: string | null): string | null {
  try {
    const endpoint = new URL(value || '');
    if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) return null;
    return `${endpoint.origin}${endpoint.pathname.replace(/\/$/, '')}`;
  } catch { return null; }
}

function fingerprint(connection: WayloTargetConnection): string | null {
  const endpoint = normalizedProviderEndpoint(connection.endpoint_url?.trim());
  if (!endpoint || !connection.workspace_id || !connection.waylo_agent_id) return null;
  return JSON.stringify([endpoint, connection.workspace_id.trim().toLowerCase(), connection.waylo_agent_id.trim().toLowerCase()]);
}

export function isWayloLocalControlPage(): boolean {
  if (typeof window === 'undefined' || !['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) return false;
  const override = new URLSearchParams(window.location.search).get('api_base');
  try { return !override || new URL(override, window.location.origin).origin === window.location.origin; }
  catch { return false; }
}

function sameOriginRequest(requestUrl?: string): boolean {
  if (!isWayloLocalControlPage()) return false;
  try {
    return new URL(requestUrl || '/api/waylo/connection/status', window.location.origin).origin === window.location.origin;
  } catch { return false; }
}

function announce() { listeners.forEach((listener) => listener()); }

function forget(key: string, notify = true) {
  const entry = connections.get(key);
  if (!entry) return;
  clearTimeout(entry.timer);
  connections.delete(key);
  if (currentFingerprint === key) currentFingerprint = null;
  if (notify) announce();
}

function memoryConnection(connection?: WayloTargetConnection) {
  const key = connection ? fingerprint(connection) : currentFingerprint;
  if (!key) return null;
  const entry = connections.get(key);
  if (!entry) return null;
  if (Date.parse(entry.info.expires_at) <= Date.now()) {
    forget(key, false);
    queueMicrotask(announce);
    return null;
  }
  return { key, entry };
}

/** Public status deliberately excludes the private control proof. */
export function getWayloConnection(connection?: WayloTargetConnection): WayloConnectionInfo | null {
  const found = memoryConnection(connection);
  return found ? { ...found.entry.info } : null;
}

export function subscribeWayloConnection(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Proof headers are restricted to the same-origin local CAE API, never Waylo. */
export function wayloRequestHeaders(connection?: WayloTargetConnection, requestUrl?: string): Record<string, string> {
  if (!sameOriginRequest(requestUrl)) return {};
  if (connection && connection.auth_type !== 'waylo_browser_session') return {};
  const found = memoryConnection(connection);
  return found ? { 'X-CAE-Waylo-Control': '1', 'X-CAE-Waylo-Session': found.entry.proof } : {};
}

export async function connectWaylo(payload: {
  email: string; password: string; waylo_agent_id: string; tenant_id?: string; confirm: true; expected_endpoint_url: string;
}): Promise<WayloConnectionInfo> {
  if (!isWayloLocalControlPage()) throw new Error('Temporary Waylo connections are available only on local CAE.');
  const response = await fetch('/api/waylo/connection/connect', {
    method: 'POST', cache: 'no-store', credentials: 'omit',
    headers: { ...wayloRequestHeaders(undefined, '/api/waylo/connection/connect'), 'Content-Type': 'application/json', 'X-CAE-Waylo-Control': '1' },
    body: JSON.stringify(payload),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Waylo connection failed.');
  const expiry = Math.min(Date.parse(value.expires_at), Date.now() + 15 * 60_000);
  const expectedBase = payload.expected_endpoint_url.replace(/\/$/, '');
  const expectedEndpoint = payload.tenant_id
    ? `${expectedBase}/admin/tenants/${payload.tenant_id.trim().toLowerCase()}` : expectedBase;
  if (value.connected !== true || typeof value.session_proof !== 'string' || !value.session_proof
    || typeof value.endpoint_url !== 'string' || !normalizedProviderEndpoint(expectedEndpoint)
    || normalizedProviderEndpoint(value.endpoint_url) !== normalizedProviderEndpoint(expectedEndpoint)
    || !Number.isFinite(expiry) || expiry <= Date.now()
    || typeof value.workspace_id !== 'string' || typeof value.waylo_agent_id !== 'string'
    || value.waylo_agent_id.toLowerCase() !== payload.waylo_agent_id.trim().toLowerCase()) {
    throw new Error('Waylo did not return a valid temporary connection.');
  }
  const info: WayloConnectionInfo = {
    connected: true, expires_at: new Date(expiry).toISOString(), endpoint_url: value.endpoint_url,
    workspace_id: value.workspace_id, waylo_agent_id: value.waylo_agent_id,
    agent_name: typeof value.agent_name === 'string' ? value.agent_name : 'Waylo agent',
  };
  const key = fingerprint(info)!;
  // A successful account switch replaces this tab's one active grant. A failed
  // login leaves the previous grant usable so the operator can retry/disconnect.
  for (const priorKey of connections.keys()) forget(priorKey);
  const timer = setTimeout(() => forget(key), expiry - Date.now());
  connections.set(key, { proof: value.session_proof, info, timer });
  currentFingerprint = key;
  announce();
  return { ...info };
}

export async function loadWayloConnectionConfig(): Promise<string> {
  if (!isWayloLocalControlPage()) throw new Error('Temporary Waylo connections require same-origin local CAE.');
  const response = await fetch('/api/waylo/connection/config', {
    cache: 'no-store', credentials: 'omit', headers: { 'X-CAE-Waylo-Control': '1' },
  });
  if (!response.ok) throw new Error('Could not load the configured Waylo sign-in destination. No credentials were sent.');
  const value = await response.json();
  try {
    const endpoint = new URL(value.endpoint_url);
    if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new Error();
    // Preserve the validated configured spelling: the server checks exact
    // consent-recipient equality (including an operator's explicit HTTPS port).
    return value.endpoint_url;
  } catch { throw new Error('The configured Waylo sign-in destination is not safe. No credentials were sent.'); }
}

export async function refreshWayloConnection(connection?: WayloTargetConnection): Promise<WayloConnectionInfo | null> {
  const found = memoryConnection(connection);
  if (!found) return null;
  const response = await fetch('/api/waylo/connection/status', {
    cache: 'no-store', credentials: 'omit', headers: wayloRequestHeaders(connection),
  });
  if (response.status === 401 || response.status === 403 || response.status === 410) {
    forget(found.key); return null;
  }
  const value = await response.json();
  if (!response.ok) throw new Error('Could not check the temporary Waylo connection.');
  if (value.connected !== true) { forget(found.key); return null; }
  return getWayloConnection(connection);
}

export async function disconnectWaylo(connection?: WayloTargetConnection): Promise<void> {
  const found = memoryConnection(connection);
  if (!found) return;
  const response = await fetch('/api/waylo/connection/disconnect', {
    method: 'POST', cache: 'no-store', credentials: 'omit',
    headers: { 'Content-Type': 'application/json', ...wayloRequestHeaders(connection) }, body: '{}',
  });
  if (!response.ok && ![401, 403, 410].includes(response.status)) {
    throw new Error('Server disconnect failed. The temporary connection remains available to retry; an active call may continue until its timeout.');
  }
  forget(found.key);
}
