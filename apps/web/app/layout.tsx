import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Agentic AI Benchmark Runner',
  description:
    'Test whether AI agents can actually do the job with domain benchmarks for support, telehealth, teaching, fintech workflows, and text-first progression to voice and WebRTC.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
