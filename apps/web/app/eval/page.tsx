import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

export default function EvalPage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="eval" />
      <section className="minimal-hero" aria-labelledby="eval-title">
        <p className="eyebrow">Eval</p>
        <h1 id="eval-title">Evaluate conversation evidence.</h1>
        <p>
          Upload a vCon or transcript, paste evidence, or load clearly labeled sample evidence, then evaluate it
          against a scenario contract.
        </p>
      </section>
      <BenchmarkRunner view="score" />
    </main>
  );
}
