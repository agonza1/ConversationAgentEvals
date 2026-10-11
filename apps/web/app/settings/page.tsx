import { SiteNav } from '@/components/SiteNav';
import { OpenAIConnectionSettings } from '@/components/OpenAIConnectionSettings';
import { SpecGenerationSettings } from '@/components/SpecGenerationSettings';
import { ChatGPTJudgeSettings } from '@/components/ChatGPTJudgeSettings';

export default function SettingsPage() {
  return <main className="page-shell compact-shell">
    <SiteNav current="settings" />
    <section className="minimal-hero">
      <p className="eyebrow">Settings</p>
      <h1>Connections and models</h1>
      <p>Configure agent execution, test generation, and evaluation judging. Results and history live under Results.</p>
    </section>
    <OpenAIConnectionSettings />
    <SpecGenerationSettings />
    <ChatGPTJudgeSettings />
  </main>;
}
