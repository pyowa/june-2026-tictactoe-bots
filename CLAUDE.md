# Agent guardrails for this project

These rules apply to every Claude Code session and to every subagent spawned via the Agent tool. They are not suggestions — if a constraint makes a task impossible, STOP and report rather than working around it.

## Workflow: TDD by default

When adding behavior, follow the red-green-refactor cycle:

1. **Red.** Write the test first. Run it and confirm it fails. Read the failure message and verify it's failing for the reason you expect — not, e.g., a missing import, a typo in the test itself, or a fixture you haven't wired up.
2. **Green.** Write the minimum production code that makes the test pass. Resist the urge to add adjacent features or "while I'm here" cleanups — those belong in the refactor step or a separate change.
3. **Refactor.** With the suite green, look for cleanups: duplicate logic worth extracting, names worth sharpening, dead code worth deleting. Run `uv run poe check` after each refactor to confirm nothing regressed.

The red step is non-skippable. A test that's never been seen to fail provides no evidence that it'll catch a future regression. If you find yourself writing production code first, stop and back the test up before you continue.

For non-feature work (renames, doc edits, dependency bumps) TDD doesn't apply — but `uv run poe check` still must be green at the end.

## After every change

- Run `uv run poe check` (lint + lint-md + typecheck + tests). Do not consider a step "done" until it's green.
- If `poe check` fails, fix the underlying issue before moving on. Never disable a test, suppress a lint rule, weaken a type, or `# noqa` your way out to get green.
- If you genuinely cannot reach green within a reasonable number of attempts, STOP and report what's blocking you.

## Coverage

- **100% line coverage** on `web/`, `db/`, `runner/`, `messaging/`, and `scripts/` must hold at all times. If you add code, add the tests for it in the same change.
- Genuinely unreachable lines (e.g., broker-connection wiring that only fires against a real RabbitMQ) may be marked `# pragma: no cover` with a one-line reason. Prefer real tests over pragmas.
- `if __name__ == "__main__":` blocks and `if TYPE_CHECKING:` imports are already excluded centrally (see `[tool.coverage.report] exclude_lines` in `pyproject.toml`) — don't add pragmas to those.

## Database query style

Default to SQLAlchemy **ORM** syntax, not Core. The ORM form reads top-to-bottom and is what we use across the codebase.

- **Inserts**: `obj = Model(field=value, ...); session.add(obj); session.flush(); return obj.id` — not `insert(Model).values({Model.field: value, ...}).returning(Model.id)`.
- **Updates**: `obj = session.get(Model, id); obj.field = value` — not `update(Model).where(Model.id == id).values(field=value)`.
- **Deletes**: `session.delete(obj)` (after loading), or `session.execute(delete(Model).where(...))` only when bulk-deleting without a loaded object.
- **Reads**: `select(Model).where(Model.field == value)` — this *is* ORM; Core is what would be `select(model_table.c.field)` against `Model.__table__`.
- **Raw SQL via `text(...)`**: only when no ORM expression is available (system catalog queries, DDL like `CREATE DATABASE`/`TRUNCATE`, broker-side admin) — per the `text()` policy in TODO bullet 4.

Why this is the default: ORM constructors give you readable code that reads in the order a human writes it, plus runtime kwarg validation (`Model(unknown_field=...)` raises immediately, not as a Postgres error after the round-trip).

Core's `insert(Model).values({Model.field: value})` form gets you static type-checking of column references — `ty` flags `Model.field` if you rename the column — but at the cost of readability. The ORM form's constructor kwargs are dynamic and won't be flagged by `ty`. That's a real loss, but readability wins for everyday use.

**If you want to reach for Core (`insert/update/delete` builders)**, surface it as a discussion *before* writing the code. Legitimate reasons: bulk operations on tens of thousands of rows where session.add() loops would be too slow; a `RETURNING` clause that doesn't fit the session lifecycle; performance-critical paths that the ORM can't express. Don't reach for Core to "make it more strongly typed" — that's the tradeoff we've explicitly accepted.

## Documentation that must stay in sync

When your change touches any of the below, update the docs **in the same change**, not "later":

- `README.md` — when you change setup steps, the `poe` task table, the project layout, or the bot-author contract.
- The mermaid sequence diagrams in `README.md` — when you change who talks to whom or in what order.
- `TODO.md` — when you complete a listed bullet (check it off) or the planned architecture changes meaningfully.

## Things you must not do without explicit user approval

- **Git operations on `main`.** Never `push`, `merge`, `rebase`, `reset`, `cherry-pick`, or force-anything onto `main`. `main` is the user's branch — treat it as read-only.
- **Committing on feature branches is allowed**, but only when the user has explicitly asked for a commit or has given standing permission for the current task (e.g. "commit as you go on this branch"). Always confirm the current branch with `git branch --show-current` before committing. Never commit on `main`. Never amend a commit the user didn't ask you to amend. Never `push` unless explicitly asked.
- **Destructive or hard-to-reverse git operations** (`reset --hard`, `restore`, `checkout -- .`, `clean -f`, `branch -D`, `stash drop`) require explicit user approval regardless of branch.
- Read-only inspection (`git log`, `git diff`, `git status`, `git show`, `git blame`) is fine and encouraged when you need to understand context.
- Drop database tables, run `poe reset-db`, or `docker compose down -v` outside of an explicitly-scoped task.
- Add a new top-level dependency without a real need. If you do need one, add it to `pyproject.toml` and run `uv sync`, and explain why in your response.
- Introduce a new technology (queueing system, ORM, test framework, web framework) when an existing one in this project would do.
- **Put code in `__init__.py`.** Package `__init__.py` files stay empty. No module-level state, no functions, no env-var resolution, no re-export shortcuts — if a package needs a public surface, callers import from the deeper module path (`from entities.bot.model import Bot`, not `from entities.bot import Bot`). Reason: `__init__.py` runs at import time and is easy to overlook as a code-search target; logic that lives there hides from readers, type-checkers (sometimes), and tests.

## Docker compose hygiene (during agent runs)

- If your task involves `docker compose up` (foreground or detached), end the task with `docker compose down` from the same directory you started it in. Leaving containers running on the host's Docker daemon collides with anything else using the same image/container names and forces the user to clean up by hand.
- If you need persistent state across multiple test cycles, use `docker compose down` between cycles too — it's cheap (~3 seconds) and removes the "did I leave anything dirty" question entirely.
- The compose file no longer pins `container_name:` on any service, so each compose project (parent checkout, agent worktree, etc.) gets its own auto-generated container names. That means parallel runs are safe *as long as* you clean up your own containers when finished. Don't rely on the host's name-collision detection to flag misuse.
- **If you change `Dockerfile`, `pyproject.toml`, `uv.lock`, or anything else baked into an image** (`CMD`, `ENV`, `EXPOSE`, `COPY`, `RUN`), `docker compose up -d` alone is not enough — it'll keep using the cached image and your change won't take effect at runtime. You need `docker compose up -d --build` (or `docker compose build` first). Source code under bind mounts (`web/`, `runner/`, `db/`, `messaging/`, `alembic/`) IS picked up live without a rebuild — that's the only thing that is.

## When you hit something unexpected

- Investigate the root cause. Don't paper over symptoms — if a test fails after your change, work out whether the test was right or the code was right before "fixing" either.
- If you encounter unfamiliar files, branches, or local changes you didn't make, surface them in your report instead of deleting or overwriting them.
- If `poe check` is green but the behavior feels off, run the actual scenario (bring the stack up via `docker compose up -d`, hit the endpoint, watch the orchestrator + worker logs with `docker compose logs -f orchestrator worker-py312`) before declaring success.
