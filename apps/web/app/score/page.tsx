import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

export default function ScorePage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="score" />
      <section className="minimal-hero" aria-labelledby="score-title">
        <p className="eyebrow">Score evidence</p>
        <h1 id="score-title">Score transcript and execution evidence.</h1>
        <p>Paste conversation, action-trace, final-state, call, group-call, or vCon evidence against a scenario contract.</p>
      </section>
      <BenchmarkRunner view="score" />
    </main>
  );
}
