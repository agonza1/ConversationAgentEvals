'use client';

import { useEffect, useRef, useState } from 'react';
import { getApiBase } from '@/lib/execution';
import { apiErrorMessage } from '@/lib/apiError';

export interface OpenAIProviderStatus {
  id?: string;
  provider?: string;
  status: string;
  email?: string | null;
  account_id?: string | null;
  plan_type?: string | null;
  message?: string | null;
  last_error?: string | null;
  execution_provider?: string | null;
  execution_default_model?: string | null;
}

async function request<T>(path: string, method = 'GET'): Promise<T> {
  const response = await fetch(`${getApiBase()}/api/product/providers/openai/${path}`, {
    method, cache: 'no-store',
    ...(method === 'POST' ? { headers: { 'Content-Type': 'application/json' }, body: '{}' } : {}),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(apiErrorMessage(text, response.status));
  return JSON.parse(text) as T;
}

/** One connection lifecycle for Settings and the inline agent-run prompt. */
export function useOpenAIConnection(enabled = true) {
  const [provider, setProvider] = useState<OpenAIProviderStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [failed, setFailed] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    const lifecycle = generation;
    const attempt = ++generation.current;
    request<OpenAIProviderStatus>('status').then((status) => {
      if (attempt === generation.current) setProvider(status);
    }).catch(() => {
      if (attempt === generation.current) {
        setFailed(true);
        setMessage('Connection status unavailable. You can retry connecting.');
      }
    });
    return () => { lifecycle.current++; };
  }, [enabled]);

  async function connect() {
    const attempt = ++generation.current;
    setConnecting(true);
    setMessage(null);
    setFailed(false);
    try {
      const started = await request<{ authorize_url: string }>('oauth/start', 'POST');
      if (attempt !== generation.current) return;
      window.open(started.authorize_url, '_blank', 'noopener,noreferrer');
      setMessage('Complete OpenAI login in the opened tab. This page will refresh when connected.');
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline && attempt === generation.current) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        if (attempt !== generation.current) return;
        const status = await request<OpenAIProviderStatus>('status');
        if (attempt !== generation.current) return;
        setProvider(status);
        if (status.status === 'connected') {
          setMessage(`Connected as ${status.email || status.account_id || 'OpenAI account'}.`);
          return;
        }
      }
      if (attempt === generation.current) {
        setFailed(true);
        setMessage('Login timed out. Connect again to retry.');
      }
    } catch (error) {
      if (attempt === generation.current) {
        setFailed(true);
        setMessage(error instanceof Error ? error.message : 'Could not connect OpenAI.');
      }
    } finally {
      if (attempt === generation.current) setConnecting(false);
    }
  }

  async function disconnect() {
    const attempt = ++generation.current;
    setConnecting(false);
    setDisconnecting(true);
    setMessage(null);
    setFailed(false);
    try {
      const status = await request<OpenAIProviderStatus>('disconnect', 'POST');
      if (attempt !== generation.current) return;
      setProvider(status);
      setMessage('OpenAI disconnected.');
    } catch (error) {
      if (attempt === generation.current) {
        setFailed(true);
        setMessage(error instanceof Error ? error.message : 'Could not disconnect OpenAI.');
      }
    } finally {
      if (attempt === generation.current) setDisconnecting(false);
    }
  }

  return { provider, message, connecting, disconnecting, failed, connect, disconnect };
}
