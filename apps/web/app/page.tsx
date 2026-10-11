import Image from 'next/image';

import { ApiAwareLink } from '@/components/ApiAwareLink';
import { SiteNav } from '@/components/SiteNav';

const workspaceLinks = [
  { title: 'Tests', copy: 'Choose scenarios and success criteria.', href: '/tests', cta: 'Browse test sets' },
  { title: 'Agents', copy: 'Configure the targets you want to test.', href: '/targets', cta: 'Manage agents' },
  { title: 'Results', copy: 'Review runs, evidence, and saved evaluations.', href: '/results', cta: 'View results' },
  { title: 'Settings', copy: 'Manage providers, drafting, and judging.', href: '/settings', cta: 'Open settings' },
] as const;

const workflow = [
  { title: 'Define success', copy: 'Choose a scenario with required actions, policy boundaries, and an expected outcome.' },
  { title: 'Capture evidence', copy: 'Run a connected agent or import an existing transcript or vCon.' },
  { title: 'Inspect the result', copy: 'Review the verdict, supporting evidence, and any missing checks.' },
];

const evidence = [
  { title: 'Task & policy checks', copy: 'Compare the conversation, recorded actions, and final state with the scenario requirements.' },
  { title: 'Voice & transcript evidence', copy: 'Inspect captured audio, transcripts, latency, and word error rate when the run provides them.' },
  { title: 'Traceable verdicts', copy: 'See how the evaluation was calculated. Missing proof stays unverified, not silently passed.' },
];

export default function HomePage() {
  return (
    <main className="saas-shell home-shell">
      <SiteNav current="home" />

      <section className="home-start" aria-labelledby="hero-title">
        <header className="home-intro">
          <p className="eyebrow">Your evaluation workspace</p>
          <h1 id="hero-title">Better conversations.<br /><span>Proven outcomes.</span></h1>
          <p>Test an agent or analyze a conversation. Turn what happened into evidence you can inspect.</p>
        </header>

        <div className="home-paths">
          <article className="home-path home-path-live" aria-labelledby="live-path-title">
            <span className="home-path-icon" aria-hidden="true">↗</span>
            <div>
              <p className="eyebrow">Create new evidence</p>
              <h2 id="live-path-title">Test an agent</h2>
              <p>Choose a scenario and a connected text or voice agent. Run the exchange, then review the outcome.</p>
            </div>
            <div className="home-path-footer">
              <ApiAwareLink className="primary-link" href="/runs">Start an agent test</ApiAwareLink>
              <span>You review the setup before running.</span>
            </div>
          </article>
          <article className="home-path" aria-labelledby="import-path-title">
            <span className="home-path-icon" aria-hidden="true">≡</span>
            <div>
              <p className="eyebrow">Use existing evidence</p>
              <h2 id="import-path-title">Analyze a conversation</h2>
              <p>Upload a transcript or vCon, choose the evaluation criteria, and inspect the verdict. No live agent required.</p>
            </div>
            <div className="home-path-footer">
              <ApiAwareLink className="secondary-link" href="/eval">Analyze existing evidence</ApiAwareLink>
              <span>Bring a conversation you already have.</span>
            </div>
          </article>
        </div>
      </section>

      <nav className="home-workspace" aria-label="Workspace shortcuts">
        {workspaceLinks.map((link) => (
          <ApiAwareLink className="home-workspace-link" href={link.href} key={link.title} aria-label={link.cta}>
            <strong>{link.title}<span aria-hidden="true">→</span></strong>
            <span>{link.copy}</span>
            <span className="home-workspace-cta">{link.cta}</span>
          </ApiAwareLink>
        ))}
      </nav>

      <section className="home-process" id="product" aria-labelledby="product-title">
        <div>
          <p className="eyebrow">One evaluation flow</p>
          <h2 id="product-title">From conversation to a clear verdict.</h2>
          <ol className="home-steps">
            {workflow.map((step) => (
              <li key={step.title}>
                <h3>{step.title}</h3>
                <p>{step.copy}</p>
              </li>
            ))}
          </ol>
        </div>
        <div className="home-flow-visual">
          <Image
            src="/images/convoice-qa-evaluation-pipeline.png"
            alt="Run an agent or import transcript or vCon evidence, then evaluate and report"
            width={1774}
            height={887}
            sizes="(max-width: 760px) calc(100vw - 60px), (max-width: 1160px) 50vw, 580px"
          />
          <p>Two ways to capture evidence. The same scenario-based evaluation.</p>
        </div>
      </section>

      <section className="home-evidence" id="coverage" aria-labelledby="coverage-title">
        <div className="home-section-header">
          <div>
            <p className="eyebrow">What you can review</p>
            <h2 id="coverage-title">Evidence, not just a score.</h2>
          </div>
          <ApiAwareLink className="secondary-link" href="/results">Review past results</ApiAwareLink>
        </div>
        <div className="home-evidence-grid">
          {evidence.map((item) => (
            <article key={item.title}>
              <h3>{item.title}</h3>
              <p>{item.copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="home-test-library" id="benchmarks" aria-labelledby="benchmarks-title">
        <div>
          <h2 id="benchmarks-title">Start with a test that matches the job.</h2>
          <p>Explore call center, telehealth, teaching, and fintech scenarios — or define your own.</p>
        </div>
        <ApiAwareLink className="secondary-link" href="/tests">Explore scenarios</ApiAwareLink>
      </section>
    </main>
  );
}
