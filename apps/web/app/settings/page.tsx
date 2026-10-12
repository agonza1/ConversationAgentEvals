import { SiteNav } from '@/components/SiteNav';
import { OpenAIConnectionSettings } from '@/components/OpenAIConnectionSettings';
import { SpecGenerationSettings } from '@/components/SpecGenerationSettings';
import { ChatGPTJudgeSettings } from '@/components/ChatGPTJudgeSettings';
import { ApiAwareLink } from '@/components/ApiAwareLink';
import { SpeechProviderSettings } from '@/components/SpeechProviderSettings';

export default function SettingsPage() {
  return <main className="page-shell settings-shell">
    <SiteNav current="settings" />
    <section className="settings-hero">
      <p className="eyebrow">Settings</p>
      <h1>Connections and models</h1>
      <p>Choose the connection and model CAE uses for each job.</p>
    </section>
    <div className="settings-layout">
      <aside className="settings-sidebar">
        <nav aria-label="Settings sections">
          <p className="eyebrow">On this page</p>
          <a href="#agent-execution">Agent execution</a>
          <a href="#speech">Speech · STT / TTS</a>
          <a href="#draft-generation">Test drafting</a>
          <a href="#evaluation-judge">Evaluation judge</a>
        </nav>
        <div className="settings-sidebar-note">
          <strong>Remote agent configuration</strong>
          <p>Manage target endpoints and adapters separately. Speech settings here do not reconfigure remote agents.</p>
          <ApiAwareLink href="/targets">Manage agents →</ApiAwareLink>
        </div>
      </aside>
      <div className="settings-content">
        <div className="settings-notice">
          <strong>Separate jobs, separate settings</strong>
          <p>Your agent connection does not activate the evaluation judge. Draft and judge models are selected independently.</p>
        </div>
        <OpenAIConnectionSettings />
        <SpeechProviderSettings />
        <SpecGenerationSettings />
        <ChatGPTJudgeSettings />
        <p className="settings-footnote">These settings apply to this CAE deployment. Choosing or saving a model does not run an evaluation.</p>
      </div>
    </div>
  </main>;
}
