'use client';

import { useEffect, useMemo, useState } from 'react';
import { createDefaultDeck, createSession, getDeckSlides, getDefaultDeckMeta, getRealtimeContract, uploadDeck } from '@/lib/api';
import { DeckSlidesResponse, DeckSummary, DefaultDeckMeta, SessionCreateResponse } from '@/lib/types';

function normalizeApiBase(value: string) {
  return value.replace(/\/$/, '').replace(/\/api$/, '');
}

export function DeckLaunchpad() {
  const [file, setFile] = useState<File | null>(null);
  const [deck, setDeck] = useState<DeckSummary | null>(null);
  const [slides, setSlides] = useState<DeckSlidesResponse['slides']>([]);
  const [session, setSession] = useState<SessionCreateResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>('');
  const [status, setStatus] = useState<string>('');
  const [defaultDeck, setDefaultDeck] = useState<DefaultDeckMeta | null>(null);
  const [copied, setCopied] = useState(false);

  const ready = useMemo(() => deck?.status === 'ready', [deck]);
  const presentationUrl = useMemo(() => {
    if (!session?.public_url) return null;
    if (typeof window === 'undefined') return session.public_url;
    const candidate = new URL(session.public_url, window.location.origin);
    if (session.api_base_url) {
      const apiBase = new URL(session.api_base_url, window.location.origin);
      candidate.searchParams.set('api_base', normalizeApiBase(apiBase.toString()));
    }
    return candidate.toString();
  }, [session?.api_base_url, session?.public_url]);
  const manifestSlides = useMemo(() => {
    const manifest = deck?.manifest_json;
    if (!manifest || typeof manifest === 'string' || !('slides' in manifest) || !Array.isArray(manifest.slides)) {
      return [];
    }
    return manifest.slides;
  }, [deck]);

  useEffect(() => {
    let active = true;
    void getDefaultDeckMeta()
      .then((meta) => {
        if (!active) return;
        setDefaultDeck(meta);
      })
      .catch(() => {
        if (!active) return;
        setDefaultDeck({ available: false, name: 'Default demo deck' });
      });

    return () => {
      active = false;
    };
  }, []);

  async function handleUpload() {
    if (!file) {
      setError('Choose a PDF deck before uploading.');
      return;
    }

    setBusy(true);
    setError('');
    setStatus('Uploading and preprocessing your PDF…');
    setSession(null);
    try {
      const uploadedDeck = await uploadDeck(file);
      setDeck(uploadedDeck);
      const slideData = await getDeckSlides(uploadedDeck.id);
      setSlides(slideData.slides);
      setStatus(`Deck ready: ${uploadedDeck.slide_count} slides processed. Review the preview, then create the live session.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      setStatus('');
    } finally {
      setBusy(false);
    }
  }

  async function handleUseDefaultDeck() {
    setBusy(true);
    setError('');
    setStatus('Loading the built-in demo deck…');
    setSession(null);
    try {
      const loadedDeck = await createDefaultDeck();
      setDeck(loadedDeck);
      const slideData = await getDeckSlides(loadedDeck.id);
      setSlides(slideData.slides);
      setStatus(`Default demo deck ready: ${loadedDeck.slide_count} slides processed. Review the preview, then create the live session.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load default deck');
      setStatus('');
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateSession() {
    if (!deck) {
      setError('Upload a deck first.');
      return;
    }

    setBusy(true);
    setError('');
    setCopied(false);
    setStatus('Creating the public demo session…');
    try {
      const created = await createSession(deck.id);
      await getRealtimeContract(created.session_id);
      setSession(created);
      setStatus('Session ready. Open the presentation link in a new tab and click Start presentation.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create session');
      setStatus('');
    } finally {
      setBusy(false);
    }
  }

  async function handleCopyLink() {
    if (!presentationUrl) return;
    try {
      await navigator.clipboard.writeText(presentationUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Could not copy the presentation link automatically.');
    }
  }

  return (
    <div className="card" style={{ padding: 24, display: 'grid', gap: 18 }}>
      <div>
        <p style={{ color: 'var(--accent)', marginTop: 0, marginBottom: 8 }}>Operator flow</p>
        <h2 style={{ margin: 0, fontSize: 30 }}>Upload a deck and mint a live demo link.</h2>
        <p style={{ color: 'var(--muted)', lineHeight: 1.6, marginBottom: 0 }}>
          1) Upload a PDF. 2) Wait for slide preprocessing. 3) Create the session. 4) Open the presentation link and run the demo from there.
        </p>
      </div>

      <div className="card" style={{ padding: 16, background: 'var(--panel-alt)' }}>
        <p style={{ margin: '0 0 8px', color: 'var(--muted)', fontSize: 13 }}>2-minute operator checklist</p>
        <ol style={{ margin: 0, paddingLeft: 18, color: 'var(--text)', lineHeight: 1.7 }}>
          <li>Use a small PDF deck so preprocessing finishes quickly.</li>
          <li>Upload it here and confirm the slide preview looks sane.</li>
          <li>Create the public session and open the generated link.</li>
          <li>In the presentation tab, click <strong>Start</strong>, drive slides, and use the Q&amp;A box for objections.</li>
        </ol>
      </div>

      <div style={{ display: 'grid', gap: 12 }}>
        {defaultDeck === null ? (
          <div className="card" style={{ padding: 16, background: 'var(--panel-alt)' }}>
            <p style={{ margin: 0, color: 'var(--muted)' }}>Checking built-in demo deck availability…</p>
          </div>
        ) : null}

        {defaultDeck?.available ? (
          <div className="card" style={{ padding: 16, background: 'var(--panel-alt)', display: 'grid', gap: 10 }}>
            <div>
              <p style={{ margin: '0 0 4px', color: 'var(--accent)', fontWeight: 700 }}>Built-in demo deck</p>
              <p style={{ margin: 0, color: 'var(--muted)' }}>{defaultDeck.name}</p>
            </div>
            <button
              onClick={() => void handleUseDefaultDeck()}
              disabled={busy}
              style={{
                border: '1px solid rgba(15,23,42,0.1)',
                borderRadius: 14,
                minHeight: 46,
                padding: '0 18px',
                background: '#ffffff',
                color: 'var(--text)',
                fontWeight: 700,
                justifySelf: 'start',
              }}
            >
              {busy ? 'Working…' : 'Use default attached deck'}
            </button>
          </div>
        ) : null}

        <input
          type="file"
          accept="application/pdf"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setError('');
            setStatus(event.target.files?.[0] ? `Selected ${event.target.files[0].name}` : '');
          }}
          style={{ color: 'var(--text)' }}
        />
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>
          {file ? `Selected file: ${file.name}` : 'No file selected yet. PDF only, unless you use the built-in demo deck above.'}
        </p>
        <button
          onClick={() => void handleUpload()}
          disabled={busy || !file}
          style={{
            border: 'none',
            borderRadius: 14,
            minHeight: 48,
            padding: '0 18px',
            background: busy || !file ? 'rgba(148,163,184,0.14)' : 'linear-gradient(135deg, #2563eb, #0ea5e9)',
            color: busy || !file ? 'var(--muted)' : '#ffffff',
            fontWeight: 700,
            justifySelf: 'start',
          }}
        >
          {busy ? 'Working…' : 'Upload PDF deck'}
        </button>
      </div>

      {status ? (
        <div className="card" style={{ padding: 14, background: 'var(--success-bg)', border: '1px solid var(--success-border)' }}>
          <p style={{ margin: 0, color: 'var(--success-text)' }}>{status}</p>
        </div>
      ) : null}

      {deck ? (
        <div className="card" style={{ padding: 18, background: '#f8fbff' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>Deck</p>
              <strong>{deck.title}</strong>
            </div>
            <div>
              <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>Status</p>
              <strong style={{ textTransform: 'capitalize' }}>{deck.status}</strong>
            </div>
            <div>
              <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>Slides</p>
              <strong>{deck.slide_count}</strong>
            </div>
          </div>

          {slides.length ? (
            <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
              {slides.slice(0, 5).map((slide) => {
                const manifestSlide = manifestSlides.find((item) => item.index === slide.index);
                return (
                  <div key={slide.id} style={{ padding: 12, borderRadius: 12, background: '#ffffff', border: '1px solid rgba(15,23,42,0.06)' }}>
                    <p style={{ margin: '0 0 4px', color: 'var(--accent)', fontSize: 13 }}>Slide {slide.index + 1}</p>
                    <p style={{ margin: '0 0 4px', fontWeight: 700 }}>{slide.title}</p>
                    <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.5 }}>{slide.summary}</p>
                    {manifestSlide?.top_terms?.length ? (
                      <p style={{ margin: '8px 0 0', color: 'var(--muted)', fontSize: 12, lineHeight: 1.5 }}>
                        Key terms: {manifestSlide.top_terms.join(', ')}
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 18 }}>
            <button
              onClick={() => void handleCreateSession()}
              disabled={busy || !ready}
              style={{
                border: '1px solid rgba(15,23,42,0.1)',
                borderRadius: 12,
                minHeight: 46,
                padding: '0 16px',
                background: busy || !ready ? 'rgba(148,163,184,0.14)' : '#ffffff',
                color: busy || !ready ? 'var(--muted)' : 'var(--text)',
                fontWeight: 700,
              }}
            >
              Create live demo session
            </button>
            {!ready ? <span style={{ color: 'var(--muted)' }}>Wait for preprocessing to finish before creating the session.</span> : null}
          </div>
        </div>
      ) : null}

      {session ? (
        <div className="card" style={{ padding: 18, background: 'var(--panel-alt)', display: 'grid', gap: 12 }}>
          <div>
            <p style={{ marginTop: 0, color: 'var(--muted)' }}>Presentation URL</p>
            <a
              href={presentationUrl ?? session.public_url}
              style={{ color: 'var(--accent)', fontWeight: 700, wordBreak: 'break-all' }}
              target="_blank"
              rel="noreferrer"
            >
              {presentationUrl ?? session.public_url}
            </a>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <a
              href={session.public_url}
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 12,
                minHeight: 44,
                padding: '0 16px',
                background: 'linear-gradient(135deg, #2563eb, #0ea5e9)',
                color: '#ffffff',
                fontWeight: 700,
                textDecoration: 'none',
              }}
            >
              Open presentation tab
            </a>
            <button
              onClick={() => void handleCopyLink()}
              style={{
                border: '1px solid rgba(15,23,42,0.1)',
                borderRadius: 12,
                minHeight: 44,
                padding: '0 16px',
                background: '#ffffff',
                color: 'var(--text)',
                fontWeight: 700,
              }}
            >
              {copied ? 'Copied link' : 'Copy link'}
            </button>
          </div>
          <p style={{ color: 'var(--muted)', marginBottom: 0, marginTop: 0 }}>
            Open this in a new tab. That page is the operator surface for starting, pausing, advancing, and answering questions.
          </p>
        </div>
      ) : null}

      {error ? (
        <div className="card" style={{ padding: 14, background: 'var(--error-bg)', border: '1px solid var(--error-border)' }}>
          <p style={{ color: 'var(--error-text)', margin: 0 }}>{error}</p>
        </div>
      ) : null}
    </div>
  );
}
