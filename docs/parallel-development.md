# Parallel Development

Use one Git worktree and one CAE development stack per active coding task. Share
heavyweight inference services such as rtc-asr and Kokoro when the machine cannot
comfortably run duplicates.

## Worktree and branch ownership

- Give each editing agent its own worktree. Do not run two editing agents in the
  same checkout.
- Start from a clean `main` unless the task explicitly depends on another branch.
- Use one branch per worktree, named for the task, such as
  `codex/123-run-history`.
- State the task's likely file ownership before work begins. Sequence tasks that
  must edit the same central files.
- Stage explicit paths. Do not use `git add -A` in a checkout containing another
  task's changes.
- Agents push feature branches. One integrator rebases, reviews, merges, and runs
  the combined validation before updating `main`.

The Codex desktop app can create a managed worktree for a new task. Git also
supports manual worktrees:

```bash
git fetch origin
git worktree add ../ConversationAgentEvals-task \
  -b codex/task-name origin/main
```

Git allows a branch to be checked out in only one worktree at a time. Use Codex
Handoff or switch the original worktree away from the branch instead of forcing a
second checkout.

## Initialize each worktree independently

Install dependencies inside each worktree. Do not copy or symlink `.venv`,
`node_modules`, Next.js caches, databases, or generated artifacts between
worktrees.

```bash
npm ci
./scripts/ensure-venv.sh apps/api/.venv apps/api/requirements.txt
./scripts/ensure-venv.sh apps/pipecat/.venv apps/pipecat/requirements.txt
```

Codex-managed worktrees can copy selected ignored local files through a
`.worktreeinclude` file. Only include required configuration such as `.env` when
every local worktree is trusted; never commit secrets.

## Assign one runtime slot per CAE stack

Run each worktree's web, API, and Pipecat processes on its own ports:

| Slot | Web | API | Pipecat |
| ---: | ---: | ---: | ---: |
| 0 | 3012 | 8025 | 8110 |
| 1 | 3112 | 8125 | 8210 |
| 2 | 3212 | 8225 | 8310 |

The launcher applies the same `100 * slot` offset to the three base ports and
sets the matching Playwright URL:

```bash
npm run dev:slot -- 1
```

Use `npm run dev:slot -- 1 --print` to inspect a slot without starting services.
The underlying development supervisor still checks whether each selected port is
available.

## Share heavyweight services

Run a single host instance of expensive, mostly stateless dependencies:

- rtc-asr;
- Kokoro or another TTS/model server;
- other local inference servers;
- optionally a target such as ACC, but only when its sessions and state are
  isolated by run or session ID.

Point every CAE worktree at the shared services:

```bash
RTC_ASR_BASE_URL=http://127.0.0.1:8080
KOKORO_BASE_URL=http://127.0.0.1:8880
```

For a CAE stack running in containers, use host-reachable URLs such as
`http://host.docker.internal:8080`.

One designated owner starts, stops, or reconfigures shared infrastructure.
Agents may call it but should not restart it. If the shared ASR, TTS, model, or
audio device cannot sustain concurrent inference, serialize only full
voice-intensive runs; text, fixture, API, and UI work can continue in parallel.

## Keep writable CAE state isolated

Each worktree must retain its own:

- CAE database;
- `storage/` and `artifacts/`;
- `.local/` provider state;
- Next.js build caches;
- API-to-Pipecat internal capability token;
- Playwright output.

Do not point parallel CAE APIs at the same SQLite file. Shared Postgres is
acceptable only with separate databases or an explicit tenant-safe test design.

OpenAI Codex OAuth uses the fixed callback port `1455`. Only one stack should
start a connection flow at a time. Other stacks may use existing worktree-local
credentials or an API-key configuration.

## Validation and cleanup

- Each agent runs focused tests for its task.
- The integrator runs the full API, web, and relevant voice validation after
  combining branches.
- Stop the worktree's services before removal.
- Remove completed worktrees with `git worktree remove <path>` and periodically
  run `git worktree prune`.

For Docker-based parallel stacks, assign a unique Compose project name and unique
host ports. Compose project names isolate its networks and named volumes, but
they do not resolve this repository's fixed host OAuth callback port.

References:

- [Codex worktrees](https://developers.openai.com/codex/app/worktrees)
- [Git worktrees](https://git-scm.com/docs/git-worktree.html)
- [Docker Compose project names](https://docs.docker.com/compose/how-tos/project-name/)
- [Python virtual environments](https://docs.python.org/3/library/venv.html)
- [npm clean installs](https://docs.npmjs.com/cli/v11/commands/npm-ci/)
