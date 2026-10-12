import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

export default function EvalPage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="eval" />
      <section className="minimal-hero" aria-labelledby="eval-title">
        <p className="eyebrow">New evaluation · existing evidence</p>
        <h1 id="eval-title">Analyze a conversation</h1>
        <p>
          Upload a vCon or transcript, paste evidence, or load clearly labeled sample evidence, then evaluate it
          against your test criteria. This does not start an agent or a voice call.
        </p>
      </section>
      <BenchmarkRunner view="score" />
    </main>
  );
}
