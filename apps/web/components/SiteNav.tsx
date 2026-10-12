import { ApiAwareLink } from '@/components/ApiAwareLink';
import { NewEvaluationMenu } from '@/components/NewEvaluationMenu';

export function SiteNav({
  current,
}: {
  current?: 'home' | 'eval' | 'runs' | 'agents' | 'scenarios' | 'specs' | 'voice' | 'benchmarks' | 'results' | 'settings';
}) {
  return (
    <nav className="top-nav compact-nav" aria-label="Primary">
      <ApiAwareLink className="brand" href="/">VoiceAI QA</ApiAwareLink>
      <div>
        <ApiAwareLink href="/" aria-current={current === 'home' ? 'page' : undefined}>Overview</ApiAwareLink>
        <ApiAwareLink href="/tests" aria-current={current === 'scenarios' || current === 'specs' ? 'page' : undefined}>Tests</ApiAwareLink>
        <ApiAwareLink href="/targets" aria-current={current === 'agents' ? 'page' : undefined}>Agents</ApiAwareLink>
        <ApiAwareLink href="/results" aria-current={current === 'results' ? 'page' : undefined}>Results</ApiAwareLink>
        <ApiAwareLink href="/settings" aria-current={current === 'settings' ? 'page' : undefined}>Settings</ApiAwareLink>
        <NewEvaluationMenu />
      </div>
    </nav>
  );
}
