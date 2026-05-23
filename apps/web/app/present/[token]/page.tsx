import { PresentationShell } from '@/components/PresentationShell';
import { getPublicSession } from '@/lib/api';

export default async function PresentationPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const data = await getPublicSession(token);

  return (
    <main className="page-shell">
      <PresentationShell initialData={data} />
    </main>
  );
}
