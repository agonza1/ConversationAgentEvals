import { redirect } from 'next/navigation';
import type { Route } from 'next';

/** Old voice links use the shared launcher, never a second execution workflow. */
export default async function VoiceQuickStart({ searchParams }: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
    else if (value !== undefined) query.set(key, value);
  });
  const defaults = { suite_id: 'call-center-voice-ai', scenario_id: 'cancellation-rescue', agent_id: 'generalist-voice-agent' };
  Object.entries(defaults).forEach(([key, value]) => { if (!query.has(key)) query.set(key, value); });
  // Opening an old demo link is configuration, not authorization to start a call.
  query.delete('launch');
  redirect(`/runs?${query.toString()}` as Route);
}
