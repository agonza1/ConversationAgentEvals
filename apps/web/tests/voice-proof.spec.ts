import { test, expect } from '@playwright/test';

const DEFAULT_DECK_PDF_BASE64 = [
  'JVBERi0xLjcKJcK1wrYKCjEgMCBvYmoKPDwvVHlwZS9DYXRhbG9nL1BhZ2VzIDIgMCBSPj4KZW5kb2JqCgoy',
  'IDAgb2JqCjw8L1R5cGUvUGFnZXMvQ291bnQgMy9LaWRzWzQgMCBSIDggMCBSIDExIDAgUl0+PgplbmRvYmoK',
  'CjMgMCBvYmoKPDwvRm9udDw8L2hlbHYgNSAwIFI+Pj4+CmVuZG9iagoKNCAwIG9iago8PC9UeXBlL1BhZ2Uv',
  'TWVkaWFCb3hbMCAwIDU5NSA4NDJdL1JvdGF0ZSAwL1Jlc291cmNlcyAzIDAgUi9QYXJlbnQgMiAwIFIvQ29u',
  'dGVudHNbNiAwIFJdPj4KZW5kb2JqCgo1IDAgb2JqCjw8L1R5cGUvRm9udC9TdWJ0eXBlL1R5cGUxL0Jhc2VG',
  'b250L0hlbHZldGljYS9FbmNvZGluZy9XaW5BbnNpRW5jb2Rpbmc+PgplbmRvYmoKCjYgMCBvYmoKPDwvTGVu',
  'Z3RoIDE1MS9GaWx0ZXIvRmxhdGVEZWNvZGU+PgpzdHJlYW0KeNotjj0LgjEMhPf8isyC2qTtxYI4iC5uQjdx',
  '8f3gHXRw8febVgkc5Lh7EnrTsZJw8BE2ZbPA9UXbZXp+WITrzLd9DqaYoRiQMR7u9UKB15I3IonriTyRsEOx',
  'qOGnFiEYLbSGe9HbkyVvu/oOzKY9NaAgtUwUnTq5rtpFRTb0fO4Eax+Ydba4V5z5wGDl3zpXutIXrx0uvwpl',
  'bmRzdHJlYW0KZW5kb2JqCgo3IDAgb2JqCjw8L0ZvbnQ8PC9oZWx2IDUgMCBSPj4+PgplbmRvYmoKCjggMCBv',
  'YmoKPDwvVHlwZS9QYWdlL01lZGlhQm94WzAgMCA1OTUgODQyXS9Sb3RhdGUgMC9SZXNvdXJjZXMgNyAwIFIv',
  'UGFyZW50IDIgMCBSL0NvbnRlbnRzWzkgMCBSXT4+CmVuZG9iagoKOSAwIG9iago8PC9MZW5ndGggMTUyL0Zp',
  'bHRlci9GbGF0ZURlY29kZT4+CnN0cmVhbQp42i2OsQoCQQxE+3xFakHdZDeJC2Ih2tgJ6cTG844rtLDx+83u',
  'SWBIwsxL4ANHB8IURWiMZgn9Ddt5fH2RCH3C216yTjqYWNEa3Xi4+wUSrkk2RAX9BOEputNqmdOilpX0aUkH',
  'ldg1whh5aRqz6mTcXUMwS/Nk5oXsq3aTVUy7XzrBjCNjnU2xq8F8xFf1nzo7XOEHL9ovagplbmRzdHJlYW0K',
  'ZW5kb2JqCgoxMCAwIG9iago8PC9Gb250PDwvaGVsdiA1IDAgUj4+Pj4KZW5kb2JqCgoxMSAwIG9iago8PC9U',
  'eXBlL1BhZ2UvTWVkaWFCb3hbMCAwIDU5NSA4NDJdL1JvdGF0ZSAwL1Jlc291cmNlcyAxMCAwIFIvUGFyZW50',
  'IDIgMCBSL0NvbnRlbnRzWzEyIDAgUl0+PgplbmRvYmoKCjEyIDAgb2JqCjw8L0xlbmd0aCAxNTEvRmlsdGVy',
  'L0ZsYXRlRGVjb2RlPj4Kc3RyZWFtCnjaJY67CgJBDEX7fEVqQZ3M42YHxEK0sRPSiY3rLBZa2Pj9ZkYCgYST',
  'c0MfOhgJBy9hjawa2N60fbbXl0XYFr7uimLSGgMaFtX9zc4UeC1lI5LZjuRExoSqyZnRNUHw0IAZxXcJC5pml',
  'N59hnvioGZU5M6kFNsw26onRnjo4MswqMaePdziu+rOO2b/6n91MrrQD5xHLqIKZW5kc3RyZWFtCmVuZG9i',
  'agoKeHJlZgowIDEzCjAwMDAwMDAwMDAgMDAwMDEgZiAKMDAwMDAwMDAxNiAwMDAwMCBuIAowMDAwMDAwMDYy',
  'IDAwMDAwIG4gCjAwMDAwMDAxMjcgMDAwMDAgbiAKMDAwMDAwMDE2OCAwMDAwMCBuIAowMDAwMDAwMjc1IDAw',
  'MDAwIG4gCjAwMDAwMDAzNjQgMDAwMDAgbiAKMDAwMDAwMDU4NCAwMDAwMCBuIAowMDAwMDAwNjI1IDAwMDAw',
  'IG4gCjAwMDAwMDA3MzIgMDAwMDAgbiAKMDAwMDAwMDk1MyAwMDAwMCBuIAowMDAwMDAwOTk1IDAwMDAwIG4g',
  'CjAwMDAwMDExMDUgMDAwMDAgbiAKCnRyYWlsZXIKPDwvU2l6ZSAxMy9Sb290IDEgMCBSL0lEWzwyRDBDMUI0',
  'MzA2QzNBM0MzQUZDMzg5NkU0OUMyQTJDMj48NTFDNDM3Njk1MDA5OEIxNzk5RTI3OERGQUJGQzE3Njg+XT4+',
  'CnN0YXJ0eHJlZgoxMzI2CiUlRU9GCg==',
].join('');

async function eventuallyFetch(url: string, init?: RequestInit, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, init);
      if (response.ok || response.status < 500) return response;
      lastError = new Error(`${url} returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw lastError instanceof Error ? lastError : new Error(`Timed out fetching ${url}`);
}

async function setupSession(baseApi: string) {
  const deckResponse = await eventuallyFetch(`${baseApi}/api/decks/use-default`, { method: 'POST' });
  if (deckResponse.ok) {
    const deck = await deckResponse.json();
    return { deck, session: await createSession(baseApi, deck.id) };
  }

  expect(deckResponse.status).toBe(404);
  const uploadResponse = await eventuallyFetch(`${baseApi}/api/decks`, {
    method: 'POST',
    body: buildDeckUpload(),
  });
  expect(uploadResponse.ok).toBeTruthy();
  const deck = await uploadResponse.json();

  return { deck, session: await createSession(baseApi, deck.id) };
}

async function createSession(baseApi: string, deckId: string) {
  const sessionResponse = await eventuallyFetch(`${baseApi}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ deck_id: deckId }),
  });
  expect(sessionResponse.ok).toBeTruthy();
  const session = await sessionResponse.json();
  return session;
}

function buildDeckUpload() {
  const bytes = Buffer.from(DEFAULT_DECK_PDF_BASE64, 'base64');
  const form = new FormData();
  form.append('file', new Blob([bytes], { type: 'application/pdf' }), 'voice-proof-default-deck.pdf');
  return form;
}

test('voice-only transcript loop and live transport handshake work', async ({ page, baseURL }) => {
  const resolvedBaseUrl = baseURL ?? process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:13000';
  const webBase = new URL(resolvedBaseUrl);
  const apiBase = process.env.PLAYWRIGHT_API_BASE_URL
    ?? process.env.NEXT_PUBLIC_API_BASE_URL
    ?? process.env.API_BASE_URL
    ?? `${webBase.protocol}//${webBase.hostname}:8025`;
  const pipecatBase = process.env.PLAYWRIGHT_PIPECAT_BASE_URL
    ?? process.env.NEXT_PUBLIC_PIPECAT_SERVICE_URL
    ?? process.env.PIPECAT_SERVICE_URL
    ?? `${webBase.protocol}//${webBase.hostname}:8110`;
  const browserPipecatBase = `${webBase.origin}/pipecat`;

  const { session } = await setupSession(apiBase);
  const sessionId = session.session_id as string;
  const publicToken = session.public_token as string;

  const startRes = await fetch(`${apiBase}/api/sessions/${sessionId}/start`, { method: 'POST' });
  expect(startRes.ok).toBeTruthy();
  const startedSession = await startRes.json();
  expect(startedSession.status).toBe('presenting');

  const bootstrapRes = await fetch(`${pipecatBase}/sessions/${sessionId}/bootstrap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ publicToken }),
  });
  expect(bootstrapRes.ok).toBeTruthy();

  const liveCreateRes = await fetch(`${pipecatBase}/sessions/${sessionId}/live/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ publicToken }),
  });
  expect(liveCreateRes.ok).toBeTruthy();
  const liveCreate = await liveCreateRes.json();
  expect(liveCreate.live).toBeTruthy();

  await eventuallyFetch(`${resolvedBaseUrl}/`, undefined, 60_000);
  await page.goto(`${resolvedBaseUrl}/`);

  const handshake = await page.evaluate(async ({ pipecatBase, sessionId, publicToken }) => {
    const pc = new RTCPeerConnection();
    const events: string[] = [];
    const remoteTracks: string[] = [];
    let gatheredCandidate = false;
    let remoteDescriptionSet = false;
    const pendingIceCandidates: RTCIceCandidateInit[] = [];
    const audioContext = new AudioContext();
    const destination = audioContext.createMediaStreamDestination();
    const oscillator = new OscillatorNode(destination.context, { type: 'sine', frequency: 440 });
    const gain = new GainNode(destination.context, { gain: 0.0001 });
    oscillator.connect(gain);
    gain.connect(destination);
    oscillator.start();

    const [audioTrack] = destination.stream.getAudioTracks();
    if (audioTrack) {
      pc.addTrack(audioTrack, destination.stream);
    }
    pc.createDataChannel('probe');

    pc.ontrack = (event) => {
      remoteTracks.push(...event.streams.flatMap((s) => s.getTracks().map((t) => t.kind)));
      events.push(`track:${event.track.kind}`);
    };

    pc.onconnectionstatechange = () => {
      events.push(`connection:${pc.connectionState}`);
    };

    const sendIceCandidate = async (candidate: RTCIceCandidateInit) => {
      await fetch(`${pipecatBase}/sessions/${sessionId}/live/ice`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate }),
      });
    };

    pc.onicecandidate = async (event) => {
      if (!event.candidate) return;
      gatheredCandidate = true;
      events.push('ice:local');
      const candidate = event.candidate.toJSON();
      if (!remoteDescriptionSet) {
        pendingIceCandidates.push(candidate);
        return;
      }
      await sendIceCandidate(candidate);
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    let joinRes: Response;
    try {
      joinRes = await fetch(`${pipecatBase}/sessions/${sessionId}/live/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type, publicToken }),
      });
    } catch (error) {
      throw new Error(`join fetch failed: ${error instanceof Error ? error.message : String(error)}`);
    }

    const joinText = await joinRes.text();
    let joinPayload: any = null;
    try {
      joinPayload = joinText ? JSON.parse(joinText) : null;
    } catch {
      joinPayload = { raw: joinText };
    }
    if (!joinRes.ok) {
      throw new Error(joinPayload?.detail || joinPayload?.message || joinPayload?.raw || `join failed with ${joinRes.status}`);
    }

    await pc.setRemoteDescription({ type: joinPayload.answer.type, sdp: joinPayload.answer.sdp });
    remoteDescriptionSet = true;
    for (const candidate of pendingIceCandidates.splice(0)) {
      await sendIceCandidate(candidate);
    }

    await new Promise((resolve) => setTimeout(resolve, 3000));
    const state = pc.connectionState;
    const remoteReceivers = pc.getReceivers().map((receiver) => ({
      kind: receiver.track?.kind,
      readyState: receiver.track?.readyState,
      muted: receiver.track?.muted,
    }));
    oscillator.stop();
    destination.stream.getTracks().forEach((track) => track.stop());
    await audioContext.close();
    pc.close();
    return {
      answerType: joinPayload.answer.type,
      hasAnswerSdp: Boolean(joinPayload.answer.sdp),
      gatheredCandidate,
      remoteTracks,
      remoteReceivers,
      events,
      connectionState: state,
    };
  }, { pipecatBase: browserPipecatBase, sessionId, publicToken });

  expect(handshake.answerType).toBe('answer');
  expect(handshake.hasAnswerSdp).toBeTruthy();
  expect(handshake.gatheredCandidate).toBeTruthy();
  expect(handshake.remoteTracks).toContain('audio');
  expect(handshake.remoteReceivers).toContainEqual(expect.objectContaining({ kind: 'audio', readyState: 'live' }));
  expect(['connected', 'connecting']).toContain(handshake.connectionState);

  const liveStateRes = await fetch(`${pipecatBase}/sessions/${sessionId}/live/state`);
  expect(liveStateRes.ok).toBeTruthy();
  const liveState = await liveStateRes.json();
  expect(liveState.live.transport_ready).toBeTruthy();
  expect(['ready', 'degraded', 'connecting']).toContain(liveState.live.state);

  const askCurrentSlideRes = await fetch(`${pipecatBase}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'what slide am I on?' }),
  });
  expect(askCurrentSlideRes.ok).toBeTruthy();
  const askCurrentSlide = await askCurrentSlideRes.json();
  expect(String(askCurrentSlide.answer).toLowerCase()).toMatch(/slide\s+is\s+1|slide\s+1/);

  const askNextSlideRes = await fetch(`${pipecatBase}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'next slide' }),
  });
  expect(askNextSlideRes.ok).toBeTruthy();
  const askNextSlide = await askNextSlideRes.json();
  expect(String(askNextSlide.answer).toLowerCase()).toContain('slide 2');
  expect(askNextSlide.tool_state?.last_tool_result?.tool_name).toBe('next_slide');

  const askGroundedRes = await fetch(`${pipecatBase}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'What is the main value proposition?' }),
  });
  expect(askGroundedRes.ok).toBeTruthy();
  const askGrounded = await askGroundedRes.json();
  expect(String(askGrounded.answer).length).toBeGreaterThan(20);
  expect(Array.isArray(askGrounded.citations)).toBeTruthy();
  expect(askGrounded.citations.length).toBeGreaterThan(0);

  const publicStateRes = await fetch(`${apiBase}/api/public/${publicToken}`);
  expect(publicStateRes.ok).toBeTruthy();
  const publicState = await publicStateRes.json();
  expect(publicState.session.current_slide_index).toBe(1);

  const disconnectRes = await fetch(`${pipecatBase}/sessions/${sessionId}/disconnect`, { method: 'POST' });
  expect(disconnectRes.ok).toBeTruthy();
  const disconnect = await disconnectRes.json();
  expect(disconnect.status).toBe('disconnected');
  expect(disconnect.connected).toBeFalsy();
  expect(disconnect.live).toEqual(expect.objectContaining({ state: 'ended', transport_ready: false }));
});
