# Firebase staging with private accounts and user API keys

Hosted staging uses `CAE_AUTH_MODE=firebase`, public Google signup, and private
per-user workspaces. Google login identifies the user; an OpenAI API key separately
enables model use. No ChatGPT subscription, local Codex login, deployment API key,
speech credential, or silent provider fallback is used.

Firebase is optional. With `CAE_AUTH_MODE` unset and all four Firebase web variables
unset or empty (`FIREBASE_PROJECT_ID`, `FIREBASE_WEB_API_KEY`, `FIREBASE_AUTH_DOMAIN`,
`FIREBASE_WEB_APP_ID`), the app retains its original local behavior, including existing
provider connections. This also applies when `APP_ENV=staging` or `production`, or
on Cloud Run; those environment names alone do not enable authentication.

Setting any of those Firebase variables automatically enables Firebase mode. Startup
then requires the complete Firebase, database, public-origin, and encryption settings
below; partial configuration fails instead of opening access. `CAE_AUTH_MODE=firebase`
explicitly enables the same mode and validation. `CAE_AUTH_MODE=local` explicitly
retains the original behavior even when Firebase variables are present. Use Firebase
mode for public multi-user staging with isolation, rate limits, and user-owned keys;
legacy local mode retains the original shared workspace and provider behavior.

## Configure Firebase

1. Create or select a Firebase project. Enable **Authentication → Sign-in method → Google**.
   No invitation or email allowlist is applied by CAE.
2. Register a Firebase web app and copy its `projectId`, `apiKey`, `authDomain`, and
   `appId` into the `FIREBASE_*` variables in `.env.staging.example`. This web
   configuration is public; do not supply a service-account private key here.
3. Add the staging web hostname to Firebase Authentication's authorized domains.
4. Run the API with a service account authorized for Firebase Authentication session
   creation, user lookup, and session revocation checks. The Admin SDK uses Application
   Default Credentials. Keep service-account JSON out of the repository and browser.
5. Set `CAE_PUBLIC_ORIGIN` to the exact HTTPS web origin, without a trailing path.

Google sign-in returns an ID token to `/api/auth/session`. The API verifies its
signature, audience/project, expiry, verified email, Google sign-in provider, and
recent authentication before issuing a five-day HttpOnly, Secure, SameSite=Lax
session cookie. Every protected request verifies the session, including revocation.
Mutation requests must supply the configured Origin. The Firebase emulator is
rejected in hosted mode.

## Configure the deployment

Use a **new staging PostgreSQL database** and a separate staging environment. Existing
local rows have no tenant owner and are invisible to hosted users. Schema startup
adds the nullable `tenant_id` column to legacy tables without assigning or copying
operator data. Back up an existing database before intentionally upgrading it.

Generate the credential-encryption secret once:

```bash
apps/api/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store that value as `CAE_CREDENTIAL_ENCRYPTION_KEY` in Secret Manager, along with
`DATABASE_URL`. Reuse the same encryption key on every API instance and release.
Replacing it without migrating encrypted records makes saved user keys unreadable.

Set the remaining values from `.env.staging.example`. Do **not** attach
`OPENAI_API_KEY`, `LLM_JUDGE_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, or
other deployment provider API keys/auth tokens. Hosted startup rejects them.
Keep local OAuth stores and local artifacts out of the image; `.dockerignore`
already excludes them. Do not mount the operator's `.local`, storage, or home directory.

Deploy the existing API and web Dockerfiles. Build the web image with `API_BASE_URL`
set to the deployed API URL; Next.js rewrites are resolved at build time. The browser
uses same-origin `/api` requests, including navigation and export links. Firebase
configuration is fetched at runtime, so it needs no `NEXT_PUBLIC_*` build secrets.

If using Cloud Run, the web image listens on port **3000**. The API honors `PORT`.
The API may now accept unauthenticated *Cloud Run invocation* because the CAE
application checks Firebase sessions itself. A private Cloud Run API still needs a
gateway that supplies Cloud Run IAM identity; Next rewrites alone do not supply it.
Use HTTPS and keep web/API environments paired with the same Firebase project.

For this staging slice, run **one API process and one API instance with CPU available
between requests**. Execution still uses FastAPI background tasks, not a durable job
queue. On Cloud Run that requires instance-based billing/CPU allocation and a warm
instance; ordinary request-only CPU allocation is unsuitable for these jobs. Interrupted
jobs are not automatically retried, avoiding ambiguous duplicate paid calls. Inspect
and explicitly rerun interrupted work. A queue and restart recovery remain required
before resilient autoscaled execution. SQL rate limits are shared across processes,
but this does not make execution jobs or artifact scratch files distributed.

## User experience and limits

- Anyone with a verified Google account can sign in.
- Users begin without a model connection. Browsing, manual test design, sample text
  execution, and deterministic evaluation of supplied evidence require no model key.
- In Settings, each user can save, replace, or remove an OpenAI API key. The key is
  encrypted in PostgreSQL, never returned by the API, and never stored in browser
  localStorage. Saving or checking it makes no provider request.
- Explicit OpenAI text runs, drafting, and ASSERT reviews use only the current user's
  key. Keys are resolved through request-local context, including background work.
  Removing a key prevents future calls; it cannot cancel an already-sent provider call.
- All application SQL records and private document stores are scoped to the verified
  Firebase UID. Client-supplied actor IDs are replaced at admission. Agents, scenarios,
  published catalogs, settings, execution records, and canonical manifests are private.
  This slice provides personal workspaces; cross-account team sharing is not enabled.
- Raw `/storage` downloads, local OAuth, presentation/session demos, voice execution,
  external target executors, and direct Pipecat proxying are blocked in hosted mode.
  They require a separate credential/media isolation implementation before being enabled.
- Defaults: 180 requests/minute, 30 mutations/minute, 5 execution/generation/review
  admissions/minute, and 100/day per user. Windows are fixed UTC windows; they are
  request quotas, not dollar budgets. A model action can contain multiple calls.
  Hosted text runs are bounded to 3 iterations and 3 exchanges per scenario.
- Login attempts are limited to 20/minute per direct API peer. Configure an ingress
  IP limit too. Forwarded IP headers are deliberately not trusted; behind a Next proxy
  the login counter is shared by requests from that proxy. Increase the login limit
  deliberately if staging traffic needs it. Config/health reads are public.

Auth/library pages and deterministic samples do not trigger paid inference. Hosting,
database, bandwidth, and Firebase usage can still incur infrastructure charges;
BYOK does not make the entire deployment free.

## Verify before sharing the staging URL

Run `npm run test:api:local`, `npm run lint:web`, and `npm run build:web`.
`test_hosted_multiuser.py` uses synthetic Firebase claims and mocked paid transport;
it tests cross-user reads/writes, actor spoofing, encrypted keys, key removal,
concurrent credentials, no operator fallback, free samples, CSRF, and atomic quotas.
To run it against a disposable PostgreSQL database:

```bash
CAE_TEST_POSTGRES_URL=postgresql://USER@HOST/DISPOSABLE_TEST_DATABASE \
  PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest apps/api/tests/test_hosted_multiuser.py -q
```

**That test option drops and recreates all CAE tables in the supplied database.**
Never point it at staging or any database containing needed records.

Then verify actual Google login/logout using two separate browser profiles against
the configured Firebase project. Confirm each account sees only its own agents,
tests, results, and settings; unauthenticated API requests fail; no-key model actions
fail; and sample evaluation succeeds without model traffic. Live Firebase consent,
ADC permissions, authorized domains, and cookie forwarding require this deployed check.
No paid model smoke test is necessary to verify login or isolation.

References: [Google sign-in](https://firebase.google.com/docs/auth/web/google-signin),
[ID-token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens),
[session cookies](https://firebase.google.com/docs/auth/admin/manage-cookies).
