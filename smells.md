# Code Smells

Candidate refactoring targets. Ordered roughly by severity within each section.

---

## Files over 100 lines

| File | Lines | Note |
|------|-------|------|
| `entities/bot/repository.py` | 321 | Combines bot queries, leaderboard stats, and family stats |
| `web/submit.py` | 173 | Validation, persistence, cookie handling all mixed together |
| `web/main.py` | 150 | Route configuration — probably fine |
| `web/utils.py` | 141 | Utility functions — probably fine |
| `dispatcher/pod_builder.py` | 137 | Pod creation, health checks, and DB updates in one place |
| `dispatcher/pods.py` | 134 | Pure pod-lifecycle helpers — probably fine |
| `scripts/reset_db.py` | 131 | Orchestration script — probably fine |
| `entities/match/repository.py` | 115 | Complex CTEs |
| `dispatcher/match_runner.py` | 112 | Long game loop function |
| `dispatcher/ondeck_handler.py` | 103 | Borderline |

---

## Long functions

| File | Function | Lines | Issue |
|------|----------|-------|-------|
| ~~`entities/bot/repository.py:104`~~ | ~~`leaderboard()`~~ | ~~157~~ | ~~4 CTEs, 6 correlated subqueries, 4 Bot aliases — hard to follow~~ |
| ~~`entities/bot/repository.py:262`~~ | ~~`family()`~~ | ~~60~~ | ~~4 correlated subqueries mirroring `leaderboard()`~~ |
| ~~`dispatcher/match_runner.py:34`~~ | ~~`run_match_from_pods()`~~ | ~~79~~ | ~~Game loop + error classification + move recording~~ |
| ~~`dispatcher/pod_builder.py:54`~~ | ~~`handle_build_pod_message()`~~ | ~~58~~ | ~~See "functions doing too many things"~~ |
| ~~`scripts/reset_db.py:36`~~ | ~~`purge_rabbitmq_queues()`~~ | ~~42~~ | ~~HTTP auth, queue listing, filtering, URL encoding, deletion~~ |
| `web/submit.py:140` | `handle_submission()` | 34 | See "functions doing too many things" |
| `dispatcher/pods.py:80` | `wait_for_pod_ready()` | 26 | Polling loop with nested exception handling |

---

## Deeply nested code

| File | Lines | Depth | Pattern |
|------|-------|-------|---------|
| `entities/bot/repository.py:213` | 213–235 | 5 | SELECT within SELECT within WHERE with OR chains |
| `dispatcher/match_runner.py:56` | 56–112 | 4 | `while True` → player turns → `try/except` → conditionals |
| `scripts/reset_db.py:36` | 36–77 | 4 | `for queues` → `if not amq` → `try/except` → error code check |
| `dispatcher/pod_builder.py:54` | 54–111 | 4 | `async with` → `try/except` → null checks → repo calls |

---

## Primitive obsession / magic literals

- **Match result strings** — `"x_wins"`, `"o_wins"`, `"x_forfeit"`, `"o_forfeit"`, `"cat"` scattered across `entities/match/repository.py`, `dispatcher/match_runner.py`, `runner/engine.py`, and the test suite. An `Enum` or constants module would make mutations and misspellings compile-time errors. `check_winner()` (`runner/engine.py:43`) and `validate_move()` (`runner/engine.py:56`) both return/consume these strings directly.
- **`dispatcher/pods.py:95`** — pod phase checked against magic strings `"Failed"`, `"Unknown"`.
- **`dispatcher/match_runner.py:50–53`** — turns represented as `(("x", "X", ip_x), ...)`: positional tuples with no names. A small `@dataclass Turn` would clarify.
- **`web/utils.py:43`** — `stripped[5:].strip()` / `stripped[7:].strip()` hard-codes character offsets for `"name:"` / `"python:"`. A named constant or `removeprefix()` would make this self-documenting and mutation-resistant.
- **`entities/bot/repository.py:137–138`** — `lb_id`, `lb_base` — abbreviations that obscure meaning inside an already complex query.

---

## Duplication

- ~~**Stats subquery pattern** — `clean_wins`, `forfeit_wins`, `draws`, `losses` are each built as a correlated scalar subquery. The same pattern appears once in `leaderboard()` (lines 141–176) and again in `family()` (lines 268–303) — 8 nearly-identical COUNT subqueries total. A helper that builds the subquery given the filter arguments could collapse this significantly.~~
- **Message JSON validation** — `dispatcher/pod_builder.py:61` and `dispatcher/ondeck_handler.py:30` both do identical `try: model_validate_json() / except: nack` blocks.
- **Pod name construction** — `f"bot-{msg.bot_id}"` appears in both `pod_builder.py` and `match_runner.py`.

---

## Functions doing too many things

- **`handle_submission()` (`web/submit.py:140`)** — reads file, validates source, parses cookie, resolves owner token, persists bot, enqueues match jobs, logs, renders template. At least three distinct concerns (validate → persist → respond).
- **`handle_build_pod_message()` (`dispatcher/pod_builder.py:54`)** — deserializes JSON, validates runtime, queries DB, creates pod, waits for readiness, updates DB, publishes reply. Seven concerns.
- **`handle_match_ondeck()` (`dispatcher/ondeck_handler.py:23`)** — deserializes JSON, validates bots, checks pod existence, runs match, persists result.
- **`run_match_from_pods()` (`dispatcher/match_runner.py:34`)** — looks up IPs, drives game loop, classifies errors, records moves.
- **`leaderboard()` (`entities/bot/repository.py:104`)** — latest-version filtering, per-version stats, lifetime rollup, intra-family exclusion. These are logically distinct queries stitched into one method.
- **`handle_pod_ready_message()` (`match_scheduler/main.py:28`)** — deserializes JSON, validates message, queries DB, triggers match scheduling. Mixes I/O and orchestration.
- **`serve_rpc()` (`messaging/rpc_server.py:14`)** — sets up queue consumption, processes messages, and publishes replies. The handler coupling makes it hard to test the transport and logic separately.
- **`main()` (`scripts/seed_example_bots.py:35`)** — clears DB, reads files from disk, parses bots, and inserts them. Multiple distinct phases in one function.

---

## Long parameter lists

| File | Function | Params | Suggestion |
|------|----------|--------|------------|
| `dispatcher/pod_builder.py:38` | `_build_pod_and_wait()` | 6 | Group into a `BotPodSpec` dataclass |
| `web/submit.py:115` | `_success_response()` | 6 | `owned` + `owner_token` could be a single `OwnerContext` |
| `dispatcher/match_runner.py:34` | `run_match_from_pods()` | 5 | Group bot-side data into a `BotPodInfo` pair |

---

## Deep / untyped data structures

- **`entities/match/repository.py:19–44`** — `_match_select()` returns a 3-tuple `(stmt, bx, bo)` with positional meaning. A named dataclass or `TypedDict` would clarify.
- **`dispatcher/pods.py:117–129`** — `request_turn()` returns `dict[str, Any]` where callers must know to check for `"error"` vs `"board"` keys. A small `TurnResult` dataclass with a discriminated union would help.
- **`dispatcher/match_runner.py:50–53`** — `turns` is a tuple of 3-tuples with positional semantics (symbol string, board character, IP). No names.
- **`web/utils.py:118–136`** — `group_matches_by_version()` returns `dict[str, list[Any]]`; the `Any` hides the row structure.

---

## Other notable smells

- **Implicit state machine in `run_match_from_pods()`** (`dispatcher/match_runner.py:56–112`) — a `while True` loop with player-alternation and early returns. The flow would be clearer as an explicit loop over a fixed sequence of turns.
- **Exception for control flow** (`web/submit.py:38–44`) — `_SubmissionError` is raised for expected validation failures, not exceptional conditions. A `Result`-style return or early return with a response would be more idiomatic.
- **`session.commit()` inside `BotRepository.create()`** (`entities/bot/repository.py:88`) — the repository committing removes the caller's ability to batch or roll back. Flush + let the caller commit is the pattern used elsewhere.
- **`run_in_executor` wrapping a sync game loop** (`dispatcher/ondeck_handler.py:58–67`) — suggests the game loop could be made async rather than bridged.
- **Multiple `Bot` aliases in one query** (`entities/bot/repository.py`) — `bx`, `bo`, `bw`, `bw_inner` are all aliased from `Bot` within `leaderboard()`. Combined with the CTE complexity, this is the single hardest function to read in the codebase.
- **Silent error printing in `reset_db.py`** (`scripts/reset_db.py:96–110`) — kubectl failures print instead of raising, creating inconsistent error semantics with the rest of the script.
- **Dead/skipped acceptance test** (`tests/acceptance/test_turn_rpc.py:12`) — entire test file skipped with "Old RPC architecture removed; new pipeline acceptance tests TBD". Either replace or delete.
- **Test file organisation** (`tests/test_reset_db.py:69`) — `purge_rabbitmq_queues` tests mixed into a general reset_db file; the comment asks whether they deserve their own file.
- **Nested `with (` grouping in test helpers** (`tests/test_ondeck_handler.py:96,202`) — multiple `patch` calls stacked in a single `with` block; extracting fixtures or using `pytest.mock.patch` as a decorator would reduce the visual nesting.
