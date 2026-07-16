import Link from 'next/link';

export function SiteNav({ current }: { current?: 'home' | 'simulate' | 'score' | 'runs' | 'agents' }) {
  return (
    <nav className="top-nav compact-nav" aria-label="Primary">
      <Link className="brand" href="/">AgentBench</Link>
      <div>
        <Link href="/" aria-current={current === 'home' ? 'page' : undefined}>Homepage</Link>
        <Link href="/simulate" aria-current={current === 'simulate' ? 'page' : undefined}>Simulate</Link>
        <Link href="/score" aria-current={current === 'score' ? 'page' : undefined}>Score evidence</Link>
        <Link href="/runs" aria-current={current === 'runs' ? 'page' : undefined}>Run agent</Link>
        <Link href="/agents" aria-current={current === 'agents' ? 'page' : undefined}>Agents</Link>
      </div>
    </nav>
  );
}
