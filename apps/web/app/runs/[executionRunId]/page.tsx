import { RunDetailPage } from '@/components/RunDetailPage';

export default async function RunDetailRoutePage({
  params,
}: {
  params: Promise<{ executionRunId: string }>;
}) {
  const { executionRunId } = await params;
  return <RunDetailPage executionRunId={executionRunId} />;
}
