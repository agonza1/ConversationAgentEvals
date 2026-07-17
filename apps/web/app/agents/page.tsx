import { redirect } from 'next/navigation';

/** Legacy path — agent targets live at /targets. */
export default function AgentsRoutePage() {
  redirect('/targets');
}
