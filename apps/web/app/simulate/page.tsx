import { redirect } from 'next/navigation';
import type { Route } from 'next';

export default function LegacySimulatePage() {
  redirect('/scenarios' as Route);
}
