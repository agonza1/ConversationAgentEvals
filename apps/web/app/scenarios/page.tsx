import { ScenariosPage } from '@/components/ScenariosPage';
import { SiteNav } from '@/components/SiteNav';

export default function ScenariosRoutePage() {
  return (
    <main className="page-shell scenarios-page">
      <SiteNav current="scenarios" />

      <section className="minimal-hero" aria-labelledby="scenarios-title">
        <p className="eyebrow">Tests · rules and cases</p>
        <h1 id="scenarios-title">Choose what your agent must prove.</h1>
        <p>
          Browse saved test sets, define rules and review cases, then test an agent or analyze an existing conversation.
        </p>
      </section>

      <ScenariosPage />
    </main>
  );
}
