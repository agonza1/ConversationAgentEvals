import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import { SiteNav } from '@/components/SiteNav';

export default function SimulatePage() {
  return (
    <main className="page-shell compact-shell">
      <SiteNav current="simulate" />
      <section className="minimal-hero" aria-labelledby="simulate-title">
        <p className="eyebrow">Simulate</p>
        <h1 id="simulate-title">Simulate a scenario or suite.</h1>
        <p>Generate repeatable sample behavior for one scenario, the full suite, or a queued simulated suite run.</p>
      </section>
      <BenchmarkRunner view="simulate" />
    </main>
  );
}
