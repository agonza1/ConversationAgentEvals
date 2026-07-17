import { ScenariosPage } from '@/components/ScenariosPage';
import { SiteNav } from '@/components/SiteNav';

export default function ScenariosRoutePage() {
  return (
    <main className="page-shell scenarios-page">
      <SiteNav current="scenarios" />

      <section className="minimal-hero" aria-labelledby="scenarios-title">
        <p className="eyebrow">Scenario library</p>
        <h1 id="scenarios-title">Choose what your agent must prove.</h1>
        <p>
          Browse scenarios by evaluation suite, inspect their requirements, then run an agent or evaluate sample
          evidence. You can also add scenarios for your own workflows.
        </p>
      </section>

      <ScenariosPage />
    </main>
  );
}
