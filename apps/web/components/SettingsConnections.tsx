'use client';
import { useHostedAuth } from './AuthGate';
import { UserApiKeySettings } from './UserApiKeySettings';
import { OpenAIConnectionSettings } from './OpenAIConnectionSettings';
import { ChatGPTJudgeSettings } from './ChatGPTJudgeSettings';
import { SpeechProviderSettings } from './SpeechProviderSettings';
import { SpecGenerationSettings } from './SpecGenerationSettings';

export function SettingsConnections() {
  const { enabled } = useHostedAuth();
  return <>{enabled ? <UserApiKeySettings /> : <><OpenAIConnectionSettings /><SpeechProviderSettings /></>}
    <SpecGenerationSettings />
    {enabled ? <section id="evaluation-judge" className="card settings-card"><h2>Evaluation judge</h2>
      <p>Optional ASSERT reviews use your OpenAI API key only when you request a review. No key means AI reviews are disabled.</p>
      <p>Voice and local-service connections are disabled in hosted staging.</p></section> : <ChatGPTJudgeSettings />}
    <p className="settings-footnote">{enabled ? 'Settings and credentials are private to your account.' : 'These settings apply to this CAE deployment.'} Choosing or saving a model does not run an evaluation.</p>
  </>;
}
