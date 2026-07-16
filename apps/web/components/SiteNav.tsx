import Link from 'next/link';

export function SiteNav({ current }: { current?: 'home' | 'runner' | 'runs' | 'agents' | 'scenarios' }) {
  return (
    <nav className="top-nav compact-nav" aria-label="Primary">
      <Link className="brand" href="/">AgentBench</Link>
      <div>
        <Link href="/" aria-current={current === 'home' ? 'page' : undefined}>Homepage</Link>
        <Link href="/benchmarks" aria-current={current === 'runner' ? 'page' : undefined}>Runner</Link>
        <Link href="/runs" aria-current={current === 'runs' ? 'page' : undefined}>Runs</Link>
        <Link href="/agents" aria-current={current === 'agents' ? 'page' : undefined}>Agents</Link>
        <Link href="/scenarios" aria-current={current === 'scenarios' ? 'page' : undefined}>Scenarios</Link>
      </div>
    </nav>
  );
}
