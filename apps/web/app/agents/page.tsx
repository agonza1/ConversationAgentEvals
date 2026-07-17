import { redirect } from 'next/navigation';
import type { Route } from 'next';

/** Legacy path — agent targets live at /targets. */
export default function AgentsRoutePage() {
  redirect('/targets' as Route);
}
