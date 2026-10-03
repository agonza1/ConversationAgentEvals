import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'ConVoice QA | Voice agent quality assurance',
  description:
    'Quality assurance for conversational voice agents: evaluate conversation quality, tool execution, task outcomes, and evidence across real-world workflows.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
