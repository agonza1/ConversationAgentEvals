import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';
import { ApiAwareLink } from '@/components/ApiAwareLink';

/** Compatibility console: keep existing query-driven benchmark workflows. */
export default function BenchmarksPage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="benchmarks" />
      <section className="minimal-hero" aria-labelledby="benchmarks-title">
        <p className="eyebrow">Advanced · legacy console</p>
        <h1 id="benchmarks-title">Benchmark console</h1>
        <p>
          Existing benchmark links and simulation tools still work here. For day-to-day work use{' '}
          <ApiAwareLink href="/tests">Tests</ApiAwareLink>, <ApiAwareLink href="/results">Results</ApiAwareLink>, or{' '}
          <ApiAwareLink href="/settings">Settings</ApiAwareLink>.
        </p>
      </section>
      <BenchmarkRunner view="all" />
    </main>
  );
}
