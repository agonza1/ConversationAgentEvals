'use client';

import { useEffect, useRef } from 'react';
import { ApiAwareLink } from './ApiAwareLink';

export function NewEvaluationMenu() {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function dismiss(event: PointerEvent) {
      if (menu.current && !menu.current.contains(event.target as Node)) menu.current.open = false;
    }
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, []);
  return <details ref={menu} className="new-evaluation-menu" onKeyDown={(event) => {
    if (event.key === 'Escape' && menu.current?.open) {
      menu.current.open = false;
      menu.current.querySelector('summary')?.focus();
    }
  }} onClick={(event) => {
    if ((event.target as Element).closest('a') && menu.current) menu.current.open = false;
  }}>
    <summary>New evaluation</summary>
    <div className="new-evaluation-options">
      <ApiAwareLink href="/runs"><strong>Test an agent</strong><span>Run reviewed cases against a text or voice agent.</span></ApiAwareLink>
      <ApiAwareLink href="/eval"><strong>Analyze a conversation</strong><span>Upload a transcript or vCon. No agent is invoked.</span></ApiAwareLink>
    </div>
  </details>;
}
