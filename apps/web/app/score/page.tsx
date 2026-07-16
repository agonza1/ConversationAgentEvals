import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

export default function ScorePage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="score" />
      <section className="minimal-hero" aria-labelledby="score-title">
        <p className="eyebrow">Score evidence</p>
        <h1 id="score-title">Upload evidence and score it.</h1>
        <p>
          Upload a vCon or transcript file, or simulate Call Center Voice AI sample evidence, then grade it against a
          scenario contract.
        </p>
      </section>
      <BenchmarkRunner view="score" />
    </main>
  );
}
