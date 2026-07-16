import { ScenariosPage } from '@/components/ScenariosPage';
import Link from 'next/link';

export default function ScenariosRoutePage() {
  return (
    <main className="page-shell scenarios-page">
      <nav className="top-nav compact-nav" aria-label="Scenarios navigation">
        <Link className="brand" href="/">AgentBench</Link>
        <div>
          <Link href="/">Homepage</Link>
          <Link href="/benchmarks">Runner</Link>
          <Link href="/scenarios">Scenarios</Link>
        </div>
      </nav>

      <section className="minimal-hero" aria-labelledby="scenarios-title">
        <p className="eyebrow">Scenario library</p>
        <h1 id="scenarios-title">Create and review agent scenarios.</h1>
        <p>
          Define a simulated user prompt, expected output, and description. Created scenarios appear in the
          User Scenarios suite and can be selected from the benchmark runner.
        </p>
      </section>

      <ScenariosPage />
    </main>
  );
}
