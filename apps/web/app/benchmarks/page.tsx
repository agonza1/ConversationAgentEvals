import Link from 'next/link';

import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

/** Full console retained for advanced history/report workflows. */
export default function BenchmarksPage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav />
      <section className="minimal-hero" aria-labelledby="benchmarks-title">
        <p className="eyebrow">Full console</p>
        <h1 id="benchmarks-title">Benchmark history and reports.</h1>
        <p>
          Saved runs, suite history, and report tooling. For day-to-day work use{' '}
          <Link href="/scenarios">Scenarios</Link>, <Link href="/runs">Run agent</Link>, or{' '}
          <Link href="/eval">Eval</Link>.
        </p>
      </section>
      <BenchmarkRunner view="all" />
    </main>
  );
}
