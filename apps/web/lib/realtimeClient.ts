export type RealtimeConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'listening'
  | 'responding'
  | 'disconnected'
  | 'error';

export interface RealtimeToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface RealtimeBrowserSessionConfig {
  sessionId: string;
  ephemeralKey: string;
  model?: string | null;
  instructions?: string | null;
  tools?: RealtimeToolDefinition[];
  toolHandler?: (name: string, args: Record<string, unknown>) => Promise<unknown>;
  onStatusChange?: (status: RealtimeConnectionStatus) => void;
  onError?: (message: string) => void;
  onTranscript?: (transcript: string, final: boolean) => void;
  onOutputTranscript?: (transcript: string, final: boolean) => void;
}

export interface RealtimeBrowserSession {
  disconnect: () => Promise<void>;
  mute: (muted: boolean) => void;
  isConnected: () => boolean;
}

interface PendingToolCall {
  callId: string;
  name: string;
  argumentsJson: string;
}

const SDP_URL = 'https://api.openai.com/v1/realtime?model=';

export async function connectRealtimeBrowserSession(
  config: RealtimeBrowserSessionConfig,
): Promise<RealtimeBrowserSession> {
  const pc = new RTCPeerConnection();
  const audioEl = document.createElement('audio');
  audioEl.autoplay = true;
  audioEl.muted = false;
  audioEl.volume = 1;
  audioEl.setAttribute('playsinline', 'true');
  audioEl.style.display = 'none';
  document.body.appendChild(audioEl);

  let localStream: MediaStream | null = null;
  let dataChannel: RTCDataChannel | null = null;
  let connected = false;
  let currentOutputTranscript = '';
  const pendingToolCalls = new Map<string, PendingToolCall>();

  const setStatus = (status: RealtimeConnectionStatus) => config.onStatusChange?.(status);
  const fail = (message: string) => {
    setStatus('error');
    config.onError?.(message);
  };

  try {
    setStatus('connecting');
    localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    for (const track of localStream.getTracks()) {
      pc.addTrack(track, localStream);
    }

    pc.ontrack = (event) => {
      const [stream] = event.streams;
      if (stream) {
        audioEl.srcObject = stream;
        void audioEl.play().catch((error) => {
          fail(error instanceof Error ? `Audio playback failed: ${error.message}` : 'Audio playback failed');
        });
      }
    };

    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      if (state === 'connected') {
        connected = true;
        setStatus('connected');
      } else if (state === 'disconnected' || state === 'failed' || state === 'closed') {
        connected = false;
        setStatus(state === 'failed' ? 'error' : 'disconnected');
      }
    };

    dataChannel = pc.createDataChannel('oai-events');
    dataChannel.onopen = () => {
      setStatus('listening');
      const sessionUpdate = {
        type: 'session.update',
        session: {
          instructions: config.instructions ?? undefined,
          modalities: ['audio', 'text'],
          input_audio_transcription: { model: 'whisper-1' },
          tools: config.tools,
          tool_choice: config.tools?.length ? 'auto' : undefined,
        },
      };
      dataChannel?.send(JSON.stringify(sessionUpdate));
      dataChannel?.send(
        JSON.stringify({
          type: 'response.create',
          response: {
            modalities: ['audio', 'text'],
            instructions: config.instructions ?? undefined,
          },
        }),
      );
    };
    dataChannel.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data));
        const type = String(message?.type ?? '');

        if (type === 'response.created') {
          currentOutputTranscript = '';
          setStatus('responding');
        }
        if (type.includes('input_audio_buffer.speech_started')) {
          setStatus('listening');
        }
        if (type.includes('response.output_item.added')) {
          setStatus('responding');
        }
        if ((type === 'response.audio_transcript.delta' || type === 'response.text.delta') && message.delta) {
          currentOutputTranscript += String(message.delta);
          config.onOutputTranscript?.(currentOutputTranscript, false);
        }
        if (type === 'response.audio_transcript.done' || type === 'response.text.done' || type === 'response.done') {
          const finalTranscript = String(message.transcript ?? currentOutputTranscript).trim();
          if (finalTranscript) {
            config.onOutputTranscript?.(finalTranscript, true);
          }
          currentOutputTranscript = '';
          setStatus('listening');
        }
        if (
          (type === 'conversation.item.input_audio_transcription.completed' || type === 'input_audio_buffer.transcription.completed') &&
          message.transcript
        ) {
          config.onTranscript?.(String(message.transcript), true);
        }

        const item = message.item ?? message.response?.output?.[0] ?? null;
        if (type === 'response.function_call_arguments.delta') {
          const callId = String(item?.call_id ?? message.call_id ?? item?.id ?? '');
          if (callId) {
            const existing = pendingToolCalls.get(callId);
            pendingToolCalls.set(callId, {
              callId,
              name: String(item?.name ?? message.name ?? existing?.name ?? ''),
              argumentsJson: `${existing?.argumentsJson ?? ''}${String(message.delta ?? '')}`,
            });
          }
        }
        if ((type === 'response.function_call_arguments.done' || type === 'response.output_item.done') && config.toolHandler) {
          const callId = String(item?.call_id ?? message.call_id ?? item?.id ?? '');
          const existing = callId ? pendingToolCalls.get(callId) : undefined;
          const name = String(item?.name ?? message.name ?? existing?.name ?? '');
          const rawArgs = item?.arguments ?? message.arguments ?? existing?.argumentsJson ?? '{}';
          if (name && callId) {
            pendingToolCalls.delete(callId);
            void (async () => {
              try {
                const args = typeof rawArgs === 'string' ? JSON.parse(rawArgs || '{}') : rawArgs;
                const result = await config.toolHandler?.(name, (args ?? {}) as Record<string, unknown>);
                dataChannel?.send(
                  JSON.stringify({
                    type: 'conversation.item.create',
                    item: {
                      type: 'function_call_output',
                      call_id: callId,
                      output: JSON.stringify(result ?? {}),
                    },
                  }),
                );
                dataChannel?.send(JSON.stringify({ type: 'response.create' }));
              } catch (error) {
                dataChannel?.send(
                  JSON.stringify({
                    type: 'conversation.item.create',
                    item: {
                      type: 'function_call_output',
                      call_id: callId,
                      output: JSON.stringify({
                        error: error instanceof Error ? error.message : 'Tool call failed',
                      }),
                    },
                  }),
                );
                dataChannel?.send(JSON.stringify({ type: 'response.create' }));
              }
            })();
          }
        }
      } catch {
        // Ignore non-JSON channel chatter.
      }
    };
    dataChannel.onerror = () => fail('Realtime data channel error');

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const response = await fetch(`${SDP_URL}${encodeURIComponent(config.model || 'gpt-4o-realtime-preview')}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.ephemeralKey}`,
        'Content-Type': 'application/sdp',
      },
      body: offer.sdp ?? '',
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    const answerSdp = await response.text();
    await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

    return {
      async disconnect() {
        try {
          dataChannel?.close();
        } catch {}
        try {
          pc.getSenders().forEach((sender) => sender.track?.stop());
          pc.close();
        } catch {}
        try {
          localStream?.getTracks().forEach((track) => track.stop());
        } catch {}
        audioEl.srcObject = null;
        audioEl.remove();
        connected = false;
        setStatus('disconnected');
      },
      mute(muted: boolean) {
        localStream?.getAudioTracks().forEach((track) => {
          track.enabled = !muted;
        });
      },
      isConnected() {
        return connected;
      },
    };
  } catch (error) {
    try {
      localStream?.getTracks().forEach((track) => track.stop());
      pc.close();
      audioEl.remove();
    } catch {}
    fail(error instanceof Error ? error.message : 'Realtime connection failed');
    throw error;
  }
}
