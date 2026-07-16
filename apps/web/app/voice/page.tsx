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
        <Link href="/">Home</Link>
        <Link href="/benchmarks">Runner</Link>
        <Link href={'/voice' as Route}>Voice</Link>
      </nav>

      <section className="minimal-hero" aria-labelledby="voice-title">
        <h1 id="voice-title">Voice eval</h1>
        <p>Run cancellation-rescue and inspect transcript, recording, and vCon.</p>
      </section>

      <VoiceEvalPage />
    </main>
  );
}
