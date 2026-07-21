import { BrowserListenerPage } from '@/components/BrowserListenerPage';

export default async function ListenerRoutePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <BrowserListenerPage token={token} />;
}
