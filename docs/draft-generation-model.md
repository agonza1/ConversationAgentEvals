# Draft-generation model

Console Settings (`/benchmarks`) lets you persist a deployment-wide model for both
Evaluation design draft buttons. This does not change voice-agent models, ASSERT
judges, existing saved designs, or previous run provenance.

Runnable case authoring can now use ASSERT's native test-set generation or the
legacy CAE custom prompt. Both use this model selection; generated cases record
their actual engine/provider/model. See [the ASSERT generation workflow](assert-test-generation.md).

The connected Codex provider uses its existing account model discovery, with a
clearly identified fallback list. API-key users get chat-model suggestions and may
enter a custom compatible text model ID. Model discovery or saving does not prove
account access: generation reports provider errors rather than inventing a draft.
CAE's existing Codex alias mapping still applies; settings show the effective ID.

Precedence: saved Console selection, `SPEC_GENERATION_MODEL`, existing default
`gpt-5.4-mini` (mapped to `gpt-6-luna` by the Codex provider). Select “Use deployment
default” to reset. Settings are atomically persisted as `spec-generation-settings.json`
beside the OAuth token path, in the existing `.local` volume. Do not store keys here.
These are local deployment settings with the same trust boundary as the existing
OAuth console controls, not user-specific preferences. Other LLM providers are not
added by this change.
