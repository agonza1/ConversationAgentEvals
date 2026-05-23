import process from 'node:process';

const apiBase = (process.env.API_BASE_URL || 'http://127.0.0.1:8025').replace(/\/$/, '');
const pipecatBase = (process.env.PIPECAT_SERVICE_URL || 'http://127.0.0.1:8110').replace(/\/$/, '');

async function json(url, init) {
  const res = await fetch(url, init);
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    throw new Error(`${res.status} ${url} :: ${typeof data === 'string' ? data : JSON.stringify(data)}`);
  }
  return data;
}

function assertProof(condition, message, context = undefined) {
  if (condition) return;
  const suffix = context === undefined ? '' : ` :: ${JSON.stringify(context)}`;
  throw new Error(`voice proof failed: ${message}${suffix}`);
}

async function main() {
  const deck = await json(`${apiBase}/api/decks/use-default`, { method: 'POST' });
  const session = await json(`${apiBase}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ deck_id: deck.id }),
  });

  const sessionId = session.session_id;
  const publicToken = session.public_token;

  await json(`${apiBase}/api/sessions/${sessionId}/start`, { method: 'POST' });

  const bootstrap = await json(`${pipecatBase}/sessions/${sessionId}/bootstrap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ publicToken }),
  });
  assertProof(bootstrap.status === 'ready', 'Pipecat bootstrap did not become ready', bootstrap);

  const liveCreate = await json(`${pipecatBase}/sessions/${sessionId}/live/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ publicToken }),
  });

  const offerSdp = ['v=0','o=- 0 0 IN IP4 127.0.0.1','s=-','t=0 0','a=group:BUNDLE 0','a=msid-semantic: WMS','m=audio 9 UDP/TLS/RTP/SAVPF 111','c=IN IP4 0.0.0.0','a=mid:0','a=recvonly','a=rtcp-mux','a=ice-ufrag:test','a=ice-pwd:testpassword1234567890','a=fingerprint:sha-256 00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF','a=setup:actpass'].join('\r\n') + '\r\n';

  const join = await json(`${pipecatBase}/sessions/${sessionId}/live/join`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sdp: offerSdp, type: 'offer' }),
  });
  assertProof(join.status === 'ready', 'live join did not return ready', join);
  assertProof(Boolean(join?.answer?.sdp) && join?.answer?.type === 'answer', 'live join did not return an SDP answer', join?.answer);

  const ice = await json(`${pipecatBase}/sessions/${sessionId}/live/ice`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate: { candidate: '', sdpMid: '0', sdpMLineIndex: 0 } }),
  });
  assertProof(ice.status === 'ok', 'ICE endpoint rejected end-of-candidates marker', ice);

  const liveState = await json(`${pipecatBase}/sessions/${sessionId}/live/state`);
  assertProof(liveState?.live?.transport_ready === true, 'live transport was not marked ready', liveState?.live);

  const askSlide = await json(`${pipecatBase}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'what slide am I on?' }),
  });
  const askNext = await json(`${pipecatBase}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'next slide' }),
  });
  const askGrounded = await json(`${pipecatBase}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript: 'What is the main value proposition?' }),
  });
  const publicState = await json(`${apiBase}/api/public/${publicToken}`);

  assertProof(/current slide|slide\s+1/i.test(askSlide?.answer || ''), 'current-slide question did not get a slide-aware answer', askSlide);
  assertProof(/slide\s+2/i.test(askNext?.answer || ''), 'next-slide directive did not advance to slide 2', askNext);
  assertProof(askNext?.tool_state?.last_tool_result?.tool_name === 'next_slide', 'next-slide directive did not expose the resolved tool call', askNext?.tool_state);
  assertProof(Boolean(askGrounded?.answer) && Array.isArray(askGrounded?.citations) && askGrounded.citations.length > 0, 'grounded ask did not return answer citations', askGrounded);
  assertProof(publicState?.session?.current_slide_index === 1, 'public session state did not reflect slide navigation', publicState?.session);

  const disconnect = await json(`${pipecatBase}/sessions/${sessionId}/disconnect`, { method: 'POST' });
  assertProof(disconnect.status === 'disconnected' && disconnect.connected === false, 'logical disconnect failed', disconnect);
  assertProof(disconnect?.live?.state === 'ended' && disconnect?.live?.transport_ready === false, 'disconnect did not stop the live transport', disconnect?.live);

  console.log(JSON.stringify({
    sessionId,
    publicToken,
    liveCreateStatus: liveCreate.status,
    joinStatus: join.status,
    hasAnswerSdp: Boolean(join?.answer?.sdp),
    answerType: join?.answer?.type ?? null,
    iceStatus: ice.status,
    liveTransportReady: liveState?.live?.transport_ready ?? null,
    liveRuntimeStatus: liveState?.live?.runtime_status ?? null,
    livePipelineReady: liveState?.live?.pipeline_ready ?? null,
    disconnectStatus: disconnect.status,
    currentSlideAnswer: askSlide?.answer ?? null,
    nextSlideAnswer: askNext?.answer ?? null,
    groundedAnswer: askGrounded?.answer ?? null,
    finalSlideIndex: publicState?.session?.current_slide_index ?? null,
  }, null, 2));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
