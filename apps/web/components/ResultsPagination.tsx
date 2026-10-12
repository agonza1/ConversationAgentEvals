import type { ReactNode } from 'react';

export const RESULTS_PAGE_SIZE = 10;

export function ResultsEvidenceDetails({ collapsed, children }: { collapsed: boolean; children: ReactNode }) {
  return collapsed ? <details className="results-evidence-details"><summary>Evidence and actions</summary>{children}</details> : <>{children}</>;
}

export function ResultsPagination({ page, hasNext, loading, onChange, label = 'Results' }: {
  page: number; hasNext: boolean; loading: boolean; onChange: (page: number) => void; label?: string;
}) {
  return <nav className="results-pagination" aria-label={`${label} pagination`}>
    <span aria-live="polite">Page {page + 1}</span>
    <div>
      <button type="button" disabled={loading || page === 0} onClick={() => onChange(page - 1)}>Previous</button>
      <button type="button" disabled={loading || !hasNext} onClick={() => onChange(page + 1)}>Next</button>
    </div>
  </nav>;
}
