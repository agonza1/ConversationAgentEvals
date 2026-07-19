import { ApiAwareLink } from '@/components/ApiAwareLink';

export function SiteNav({
  current,
}: {
  current?: 'home' | 'eval' | 'runs' | 'agents' | 'scenarios' | 'specs' | 'voice' | 'benchmarks';
}) {
  return (
    <nav className="top-nav compact-nav" aria-label="Primary">
      <ApiAwareLink className="brand" href="/">AgentBench</ApiAwareLink>
      <div>
        <ApiAwareLink href="/" aria-current={current === 'home' ? 'page' : undefined}>Homepage</ApiAwareLink>
        <ApiAwareLink href="/scenarios" aria-current={current === 'scenarios' ? 'page' : undefined}>Scenarios</ApiAwareLink>
        <ApiAwareLink href="/specs/new" aria-current={current === 'specs' ? 'page' : undefined}>Evaluation design</ApiAwareLink>
        <ApiAwareLink href="/targets" aria-current={current === 'agents' ? 'page' : undefined}>Targets</ApiAwareLink>
        <ApiAwareLink href="/runs" aria-current={current === 'runs' ? 'page' : undefined}>Run agent</ApiAwareLink>
        <ApiAwareLink href="/eval" aria-current={current === 'eval' ? 'page' : undefined}>Eval</ApiAwareLink>
        <ApiAwareLink href="/voice" aria-current={current === 'voice' ? 'page' : undefined}>Voice</ApiAwareLink>
        <ApiAwareLink href="/benchmarks" aria-current={current === 'benchmarks' ? 'page' : undefined}>Full console</ApiAwareLink>
      </div>
    </nav>
  );
}
