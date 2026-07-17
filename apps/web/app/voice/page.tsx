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
            Test whether a voice agent can rescue a cancellation request, then inspect the transcript,
            recording metadata, and vCon evidence in one place.
          </p>
        </div>
        <div className="voice-eval-proof-strip" aria-label="Voice evaluation outputs">
          <span>1 scenario</span>
          <span>Live or fixture</span>
          <span>Scored evidence</span>
        </div>
      </section>

      <VoiceEvalPage />
    </main>
  );
}
