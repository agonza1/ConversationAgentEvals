'use client';

import Link from 'next/link';
import { type ComponentProps, useEffect, useState } from 'react';

type ApiAwareLinkProps = Omit<ComponentProps<typeof Link>, 'href'> & {
  href: string;
};
type LinkHref = ComponentProps<typeof Link>['href'];

function withApiBase(path: string, apiBase: string) {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}api_base=${encodeURIComponent(apiBase)}`;
}

export function ApiAwareLink({ href, ...props }: ApiAwareLinkProps) {
  const [resolvedHref, setResolvedHref] = useState<LinkHref>(href as LinkHref);

  useEffect(() => {
    const apiBase = new URLSearchParams(window.location.search).get('api_base');
    setResolvedHref((apiBase ? withApiBase(href, apiBase) : href) as LinkHref);
  }, [href]);

  return <Link href={resolvedHref} {...props} />;
}
