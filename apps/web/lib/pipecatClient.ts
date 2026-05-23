export interface PipecatSessionHandle {
  disconnect: () => Promise<void>;
  isConnected: () => boolean;
}

export interface PipecatSessionOptions {
  sessionId: string;
  connectUrl: string;
  stopUrl?: string;
  requestBody?: Record<string, unknown>;
  connectedStatuses?: string[];
  onStatusChange?: (status: string) => void;
  onRemoteAudioLevel?: (level: number) => void;
  onRemoteVideo?: () => void;
  onError?: (message: string) => void;
}

function toAbsoluteUrl(url: string) {
  if (typeof window === 'undefined') return url;
  return new URL(url, window.location.origin).toString();
}

function resolvePipecatUrl(url: string, connectUrl: string) {
  if (/^https?:\/\//i.test(url)) return url;

  const absoluteConnectUrl = toAbsoluteUrl(connectUrl);
  const connect = new URL(absoluteConnectUrl);
  const prefix = connect.pathname.match(/^(.*)\/sessions\/[^/]+\/live\/create$/)?.[1] ?? '';
  const path = url.startsWith('/') ? url : `/${url}`;

  return new URL(`${prefix}${path}`, connect.origin).toString();
}

function siblingLiveUrl(connectUrl: string, action: 'join' | 'ice' | 'stop' | 'state') {
  return toAbsoluteUrl(connectUrl).replace(/\/live\/create$/, `/live/${action}`);
}

async function fetchHelpfulLiveError(connectUrl: string): Promise<string | null> {
  try {
    const response = await fetch(siblingLiveUrl(connectUrl, 'state'));
    if (!response.ok) return null;
    const payload = await response.json();
    const live = payload?.live ?? {};
    const events = Array.isArray(live.events) ? live.events : [];
    const errorEvents = events
      .filter((event: any) => {
        const type = String(event?.type ?? '');
        const eventPayload = event?.payload ?? {};
        return type.includes('failed') || type.includes('error') || eventPayload.error || eventPayload.reason;
      })
      .slice(-3)
      .map((event: any) => {
        const eventPayload = event?.payload ?? {};
        return `${event.type}: ${eventPayload.error ?? eventPayload.reason ?? JSON.stringify(eventPayload)}`;
      });
    const details = [
      live.last_error ? `Runtime error: ${live.last_error}` : null,
      live.runtime_status && live.runtime_status !== 'running' ? `Runtime status: ${live.runtime_status}` : null,
      ...errorEvents,
    ].filter(Boolean);
    return details.length ? details.join(' | ') : null;
  } catch {
    return null;
  }
}

export async function connectPipecatSession(options: PipecatSessionOptions): Promise<PipecatSessionHandle> {
  let connected = false;
  let localStream: MediaStream | null = null;
  let dataChannel: RTCDataChannel | null = null;
  let pingInterval: number | null = null;
  let animationFrame: number | null = null;
  let audioContext: AudioContext | null = null;
  let remoteDescriptionSet = false;
  const pendingIceCandidates: RTCIceCandidateInit[] = [];
  const remoteStreams = new Set<MediaStream>();
  const remoteTracks = new Set<MediaStreamTrack>();
  const pc = new RTCPeerConnection();
  const audioEl = document.createElement('audio');
  audioEl.autoplay = true;
  audioEl.muted = false;
  audioEl.volume = 1;
  audioEl.setAttribute('playsinline', 'true');
  audioEl.style.display = 'none';
  document.body.appendChild(audioEl);
  const avatarVideoEl = document.getElementById('heygen-avatar-video') as HTMLVideoElement | null;

  const setStatus = (status: string) => options.onStatusChange?.(status);
  const fail = (message: string) => {
    setStatus('error');
    options.onError?.(message);
  };

  const stopAudioMeter = () => {
    if (animationFrame) {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = null;
    }
    if (audioContext) {
      void audioContext.close().catch(() => undefined);
      audioContext = null;
    }
    options.onRemoteAudioLevel?.(0);
  };

  const clearRemoteMedia = () => {
    remoteTracks.forEach((track) => {
      try {
        track.stop();
      } catch {}
    });
    remoteTracks.clear();
    remoteStreams.forEach((stream) => {
      stream.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch {}
      });
    });
    remoteStreams.clear();
    audioEl.srcObject = null;
    if (avatarVideoEl) {
      avatarVideoEl.pause();
      avatarVideoEl.srcObject = null;
      avatarVideoEl.removeAttribute('src');
      avatarVideoEl.load();
    }
  };

  const startAudioMeter = (stream: MediaStream) => {
    stopAudioMeter();
    try {
      audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.78;
      source.connect(analyser);
      const samples = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (const sample of samples) {
          const centered = (sample - 128) / 128;
          sum += centered * centered;
        }
        const rms = Math.sqrt(sum / samples.length);
        options.onRemoteAudioLevel?.(Math.min(1, rms * 7));
        animationFrame = window.requestAnimationFrame(tick);
      };
      tick();
    } catch {
      options.onRemoteAudioLevel?.(0.18);
    }
  };

  setStatus('connecting');

  try {
    const channel = pc.createDataChannel('pipecat-events');
    dataChannel = channel;
    channel.onopen = () => {
      pingInterval = window.setInterval(() => {
        if (channel.readyState === 'open') {
          channel.send(`ping:${Date.now()}`);
        }
      }, 1000);
    };
    channel.onclose = () => {
      if (pingInterval) {
        window.clearInterval(pingInterval);
        pingInterval = null;
      }
    };

    localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    for (const track of localStream.getTracks()) {
      pc.addTrack(track, localStream);
    }
    // The HeyGenVideoService publishes avatar video back through the same
    // Pipecat WebRTC connection. Explicitly request a receive-only video m-line
    // so the browser offer can negotiate that remote avatar track.
    pc.addTransceiver('video', { direction: 'recvonly' });

    pc.ontrack = (event) => {
      const [stream] = event.streams;
      remoteTracks.add(event.track);
      if (stream) remoteStreams.add(stream);

      if (event.track.kind === 'video' && avatarVideoEl) {
        const videoStream = stream ?? new MediaStream([event.track]);
        remoteStreams.add(videoStream);
        avatarVideoEl.srcObject = videoStream;
        avatarVideoEl.muted = true;
        avatarVideoEl.autoplay = true;
        avatarVideoEl.playsInline = true;
        event.track.onunmute = () => {
          options.onRemoteVideo?.();
          void avatarVideoEl.play().catch((error) => {
            fail(error instanceof Error ? `Pipecat avatar video playback failed: ${error.message}` : 'Pipecat avatar video playback failed');
          });
        };
        options.onRemoteVideo?.();
        void avatarVideoEl.play().catch((error) => {
          fail(error instanceof Error ? `Pipecat avatar video playback failed: ${error.message}` : 'Pipecat avatar video playback failed');
        });
        return;
      }

      if (event.track.kind === 'audio') {
        const audioStream = stream ?? new MediaStream([event.track]);
        remoteStreams.add(audioStream);
        audioEl.srcObject = audioStream;
        startAudioMeter(audioStream);
        void audioEl.play().catch((error) => {
          fail(error instanceof Error ? `Pipecat audio playback failed: ${error.message}` : 'Pipecat audio playback failed');
        });
      }
    };

    const sendIceCandidate = (candidate: RTCIceCandidateInit) => {
      const iceUrl = siblingLiveUrl(options.connectUrl, 'ice');
      return fetch(iceUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate }),
      }).catch(() => undefined);
    };

    pc.onicecandidate = (event) => {
      if (!event.candidate) return;
      const candidate = event.candidate.toJSON();
      if (!remoteDescriptionSet) {
        pendingIceCandidates.push(candidate);
        return;
      }
      void sendIceCandidate(candidate);
    };

    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      if (state === 'connected') {
        connected = true;
        setStatus('connected');
      } else if (state === 'disconnected' || state === 'failed' || state === 'closed') {
        connected = false;
        setStatus(state === 'failed' ? 'error' : 'disconnected');
        if (state === 'failed') {
          void fetchHelpfulLiveError(options.connectUrl).then((detail) => {
            fail(detail ? `Pipecat transport failed. ${detail}` : 'Pipecat transport failed. Check Docker logs for provider/runtime errors.');
          });
        }
      }
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const createUrl = toAbsoluteUrl(options.connectUrl);
    let createResponse: Response;
    try {
      createResponse = await fetch(createUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options.requestBody ?? {}),
      });
    } catch (error) {
      throw new Error(
        `Pipecat live create request failed for ${createUrl}. Use the same-origin /pipecat proxy or check that the web server can reach Pipecat. ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    const createPayload = await createResponse.json();
    if (!createResponse.ok) {
      throw new Error(createPayload?.detail || createPayload?.message || `Pipecat live create failed with ${createResponse.status}`);
    }

    const joinUrl = createPayload?.transport?.join_url
      ? resolvePipecatUrl(String(createPayload.transport.join_url), options.connectUrl)
      : siblingLiveUrl(options.connectUrl, 'join');

    let joinResponse: Response;
    try {
      joinResponse = await fetch(joinUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
      });
    } catch (error) {
      throw new Error(
        `Pipecat live join request failed for ${joinUrl}. Use the same-origin /pipecat proxy or check that the web server can reach Pipecat. ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    const joinPayload = await joinResponse.json();
    if (!joinResponse.ok) {
      throw new Error(joinPayload?.detail || joinPayload?.message || `Pipecat live join failed with ${joinResponse.status}`);
    }

    const answer = joinPayload?.answer;
    if (!answer?.sdp || !answer?.type) {
      throw new Error('Pipecat live join did not return a valid SDP answer');
    }

    await pc.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
    remoteDescriptionSet = true;
    for (const candidate of pendingIceCandidates.splice(0)) {
      void sendIceCandidate(candidate);
    }

    const connectedStatuses = new Set(options.connectedStatuses ?? ['ready', 'connected', 'listening']);
    connected = true;
    const initialStatus = String(joinPayload?.status ?? 'connected');
    setStatus(connectedStatuses.has(initialStatus) ? 'connected' : initialStatus);

    return {
      async disconnect() {
        try {
          if (options.stopUrl) {
            await fetch(toAbsoluteUrl(options.stopUrl), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
            });
          }
        } catch {
          // swallow stop errors during teardown
        }
        try {
          if (pingInterval) {
            window.clearInterval(pingInterval);
            pingInterval = null;
          }
          dataChannel?.close();
          pc.getSenders().forEach((sender) => sender.track?.stop());
          pc.getReceivers().forEach((receiver) => receiver.track?.stop());
          pc.getTransceivers().forEach((transceiver) => transceiver.stop());
          pc.close();
        } catch {}
        stopAudioMeter();
        clearRemoteMedia();
        try {
          localStream?.getTracks().forEach((track) => track.stop());
        } catch {}
        audioEl.remove();
        connected = false;
        setStatus('disconnected');
      },
      isConnected() {
        return connected;
      },
    };
  } catch (error) {
    try {
      if (pingInterval) {
        window.clearInterval(pingInterval);
        pingInterval = null;
      }
      dataChannel?.close();
      localStream?.getTracks().forEach((track) => track.stop());
      pc.close();
      stopAudioMeter();
      clearRemoteMedia();
      audioEl.remove();
    } catch {}
    const message = error instanceof Error ? error.message : 'Pipecat connection failed';
    fail(message);
    throw error;
  }
}
