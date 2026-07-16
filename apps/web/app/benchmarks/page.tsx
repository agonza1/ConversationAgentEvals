import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

/** Full console for history/report workflows. Focused paths live on /simulate, /score, /runs. */
export default function BenchmarksPage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="simulate" />
      <section className="minimal-hero" aria-labelledby="benchmarks-title">
        <p className="eyebrow">Full console</p>
        <h1 id="benchmarks-title">Benchmark history and reports.</h1>
        <p>
          Saved runs, suite history, and report tooling. For day-to-day work use{' '}
          <a href="/simulate">Simulate</a>, <a href="/score">Score evidence</a>, or <a href="/runs">Run agent</a>.
        </p>
      </section>
      <BenchmarkRunner view="all" />
    </main>
  );
}
