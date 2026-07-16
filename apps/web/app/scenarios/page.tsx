import { ScenariosPage } from '@/components/ScenariosPage';
import { SiteNav } from '@/components/SiteNav';

export default function ScenariosRoutePage() {
  return (
    <main className="page-shell scenarios-page">
      <SiteNav current="scenarios" />

      <section className="minimal-hero" aria-labelledby="scenarios-title">
        <p className="eyebrow">Scenario library</p>
        <h1 id="scenarios-title">Create and review agent scenarios.</h1>
        <p>
          Define a simulated user prompt, expected output, and description. Created scenarios appear in the
          User Scenarios suite and can be selected from Simulate and Score.
        </p>
      </section>

      <ScenariosPage />
    </main>
  );
}
