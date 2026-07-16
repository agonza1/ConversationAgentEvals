import Link from 'next/link';

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
          <Link href="/simulate">Simulate</Link>, <Link href="/score">Score evidence</Link>, or{' '}
          <Link href="/runs">Run agent</Link>.
        </p>
      </section>
      <BenchmarkRunner view="all" />
    </main>
  );
}
