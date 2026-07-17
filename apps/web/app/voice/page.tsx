import { VoiceEvalPage } from '@/components/VoiceEvalPage';
import { SiteNav } from '@/components/SiteNav';

export default function VoicePage() {
  return (
    <main className="page-shell voice-eval-shell">
      <SiteNav current="voice" />

      <section className="voice-eval-hero" aria-labelledby="voice-title">
        <div className="voice-eval-hero-copy">
          <p className="eyebrow">Call center benchmark · guided run</p>
          <h1 id="voice-title">Voice eval</h1>
          <p>
            Run a target-backed call-center scenario through Run Agent, then inspect the transcript,
            scoring, recording metadata, and vCon evidence without confusing fixture proof for a live call.
          </p>
        </div>
        <div className="voice-eval-proof-strip" aria-label="Voice evaluation outputs">
          <span>Run Agent target</span>
          <span>Pipecat capture proof</span>
          <span>Fixture-backed score</span>
        </div>
      </section>

      <VoiceEvalPage />
    </main>
  );
}
