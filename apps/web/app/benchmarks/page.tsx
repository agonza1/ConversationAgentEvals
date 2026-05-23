import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import Link from 'next/link';

export default function BenchmarksPage() {
  return (
    <main className="page-shell compact-shell">
      <nav className="top-nav compact-nav" aria-label="Benchmark navigation">
        <Link className="brand" href="/">AgentBench</Link>
        <Link href="/">Homepage</Link>
      </nav>

      <section className="minimal-hero" aria-labelledby="benchmark-title">
        <p className="eyebrow">Benchmark runner</p>
        <h1 id="benchmark-title">Run an agentic scenario test.</h1>
        <p>
          Choose a domain scenario, simulate a run, and inspect task completion, action trace, policy, and final-state scores.
        </p>
      </section>

      <BenchmarkRunner />
    </main>
  );
}
