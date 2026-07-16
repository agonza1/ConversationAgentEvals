import { VoiceEvalPage } from '@/components/VoiceEvalPage';
import Link from 'next/link';
import type { Route } from 'next';

export default function VoicePage() {
  return (
    <main className="page-shell compact-shell">
      <nav className="top-nav compact-nav" aria-label="Voice evaluation navigation">
        <Link className="brand" href="/">
          AgentBench
        </Link>
        <Link href="/">Homepage</Link>
        <Link href="/benchmarks">Runner</Link>
        <Link href={'/voice' as Route}>Voice eval</Link>
      </nav>

      <section className="minimal-hero" aria-labelledby="voice-title">
        <p className="eyebrow">Voice evaluation</p>
        <h1 id="voice-title">Evaluate voice agent behavior with audio evidence.</h1>
        <p>
          Run Execute-stage voice scenarios, capture recording and transcription into vCon, and inspect results. Live
          browser WebRTC and FreeSWITCH Verto SIP are follow-on; fixture and in-process Pipecat hooks ship today.
        </p>
      </section>

      <VoiceEvalPage />
    </main>
  );
}
