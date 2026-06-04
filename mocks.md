# Test Doubles

Catalog of every mock, patch, fake, stub, and dependency override in the test suite. Generated 2026-06-04.

## Summary

- Total entries: 50
- By type:
  - 17 `MagicMock` / `AsyncMock` instances (inline)
  - 12 `monkeypatch.setattr` calls
  - 2 `unittest.mock.patch` context managers
  - 1 FastAPI `app.dependency_overrides[...]` assignment (in a reusable fixture, applied to every `client`-using test)
  - 4 custom fake/stub/recording classes (1 reusable in `conftest.py`, 3 local: 1 module-level + 2 inline)
  - 1 ad-hoc `SimpleNamespace`-based fake Request
  - 1 helper that constructs a real `urllib.error.HTTPError` (borderline; used like a fake response payload)

## Reusable test doubles (defined in conftest.py)

### `_RecordingQueue` (custom fake class)

- **Location:** `tests/conftest.py:93-100`
- **Replaces:** The `messaging.queue.Queue` protocol (specifically the `enqueue_match(job)` method).
- **Mechanism:** Captures published `MatchJob`s into an in-memory `list[MatchJob]` so tests can assert without a real broker.
- **Used by:** the `mock_queue` fixture (`tests/conftest.py:103-108`); the `client` fixture (`tests/conftest.py:111-116`) depends on `mock_queue`, so every test that takes `client` also gets a `_RecordingQueue` wired in transitively (notably across `tests/test_pages.py`, `tests/test_submission.py`, and `tests/test_seed_example_bots.py`).

### `mock_queue` fixture — FastAPI dependency override

- **Location:** `tests/conftest.py:103-108` (override at line 106)
- **Mock type:** `app.dependency_overrides[get_queue] = lambda: queue` (FastAPI dependency_override)
- **Replaces:** `web.dependencies.get_queue` — the production dependency that would hand back the live `RabbitMQQueue` from `request.app.state.queue`.
- **Reason:** Inject `_RecordingQueue` for the duration of each test so HTTP endpoints + scripts can publish "match jobs" without touching RabbitMQ. Cleared at teardown.

## Per-file mocks

### tests/test_dependencies.py

- **`tests/test_dependencies.py:10-12`** — Ad-hoc fake (`SimpleNamespace`) standing in for `fastapi.Request`. Replaces: a real `Request` (plus its `.app.state.queue` chain). Reason: drive `get_queue(request)` directly without spinning up an HTTP server.

### tests/test_engine.py

(No test doubles — pure-functional tests over `runner.engine`.)

### tests/test_messaging.py

- **`tests/test_messaging.py:70`** — `MagicMock()` standing in for `aio_pika.AbstractChannel`. Replaces: live AMQP channel. Reason: avoid broker.
- **`tests/test_messaging.py:71`** — `AsyncMock()` assigned to `channel.default_exchange.publish`. Replaces: the awaitable publish call. Reason: capture/inspect published message + correlation_id without a broker.
- **`tests/test_messaging.py:90-92`** — `MagicMock()` as `fake_reply`, with manual `.correlation_id` / `.body` attributes. Replaces: an inbound AMQP message (`aio_pika.IncomingMessage`). Reason: simulate a worker reply.
- **`tests/test_messaging.py:100`** — `MagicMock()` channel. Replaces: AMQP channel. Reason: avoid broker (timeout-path test).
- **`tests/test_messaging.py:101`** — `AsyncMock()` on `channel.default_exchange.publish`. Replaces: awaitable publish. Reason: avoid broker.
- **`tests/test_messaging.py:111`** — `MagicMock()` channel. Replaces: AMQP channel. Reason: avoid broker (unknown-correlation-id test).
- **`tests/test_messaging.py:112`** — `AsyncMock()` on `channel.default_exchange.publish`. Replaces: awaitable publish. Reason: avoid broker.
- **`tests/test_messaging.py:114-116`** — `MagicMock()` fake_reply with stray `correlation_id`. Replaces: inbound AMQP message. Reason: simulate orphan reply.
- **`tests/test_messaging.py:126`** — `MagicMock()` channel. Replaces: AMQP channel. Reason: avoid broker (late-reply-on-done-future test).
- **`tests/test_messaging.py:131-133`** — `MagicMock()` fake_reply with `correlation_id="late-cid"`. Replaces: inbound AMQP message arriving after the awaiting future is already cancelled. Reason: exercise the "done future" guard in `_on_reply`.
- **`tests/test_messaging.py:142`** — `MagicMock()` channel. Replaces: AMQP channel. Reason: avoid broker (None-correlation_id test).
- **`tests/test_messaging.py:143-145`** — `MagicMock()` fake_reply with `correlation_id=None`. Replaces: a misrouted incoming AMQP message. Reason: exercise None-id guard.
- **`tests/test_messaging.py:152`** — `MagicMock()` channel. Replaces: AMQP channel. Reason: avoid broker (`RpcClient.create` test).
- **`tests/test_messaging.py:153-155`** — `MagicMock()` `reply_queue` plus `AsyncMock()` on `reply_queue.consume`. Replaces: the declared reply queue object returned by `channel.declare_queue`. Reason: verify create() wires up the consumer without a broker.
- **`tests/test_messaging.py:156`** — `AsyncMock(return_value=reply_queue)` on `channel.declare_queue`. Replaces: AMQP queue declaration. Reason: avoid broker.
- **`tests/test_messaging.py:177`** — `MagicMock()` channel assigned into `queue._channel`. Replaces: the `aio_pika.AbstractChannel` held by `RabbitMQQueue`. Reason: drive `enqueue_match` without a broker.
- **`tests/test_messaging.py:178`** — `AsyncMock()` on `channel.default_exchange.publish`. Replaces: awaitable publish. Reason: verify publish shape (routing key, content-type, delivery mode).
- **`tests/test_messaging.py:180`** — `MagicMock(is_closed=False)` assigned into `queue._connection`. Replaces: the `aio_pika.RobustConnection` so `RabbitMQQueue` thinks it's connected. Reason: avoid broker connect.

### tests/test_orchestrator.py

- **`tests/test_orchestrator.py:18-36`** — Custom fake class `_ScriptedRpc`. Replaces: the `RpcCaller` (`messaging.rpc_client.RpcClient`) protocol exposed by `.call(target_queue, payload, timeout)`. Reason: drive `play_match_rpc` through scripted turn responses without a broker; also records each call for assertions.
- **`tests/test_orchestrator.py:100-102`** — Inline custom fake class `_EmptyDictRpc` (defined inside the test). Replaces: `RpcCaller.call`. Reason: simulate a worker reply of `{}` (no board, no error) to exercise the "no output" fallback.
- **`tests/test_orchestrator.py:123-125`** — Inline custom fake class `_TimeoutRpc` (defined inside the test). Replaces: `RpcCaller.call`. Reason: simulate the worker timing out (raising `TimeoutError`) to exercise the forfeit-on-timeout branch.

### tests/test_pages.py

(No test doubles — uses the `client` fixture, which inherits `mock_queue`'s dependency override transitively, but no inline mocks. All DB interactions hit real `ttt_test` Postgres.)

### tests/test_reset_db.py

- **`tests/test_reset_db.py:32-47`** — Custom fake class `_FakeResponse`. Replaces: the return value of `urllib.request.urlopen` (context-manager + `.read()` returning JSON bytes). Reason: drive `purge_rabbitmq_queues` through scripted RabbitMQ management API responses.
- **`tests/test_reset_db.py:50-57`** — Helper `_http_error` that builds a real `urllib.error.HTTPError`. Not a mock per se — it's a real exception — but used purely to feed the "DELETE failed" branches of `purge_rabbitmq_queues`. Listed as "?" because it's a real object, not a stub. (Resolution: treat as test-data helper, not a double — flagging here so the user can dismiss.)
- **`tests/test_reset_db.py:81`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen` as seen by `scripts.reset_db`. Reason: avoid real HTTP to RabbitMQ management API.
- **`tests/test_reset_db.py:113`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen`. Reason: avoid live RabbitMQ; route through fake that skips `amq.*` queues.
- **`tests/test_reset_db.py:134`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen`. Reason: simulate 404 on DELETE.
- **`tests/test_reset_db.py:156`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen`. Reason: simulate 500 on DELETE.
- **`tests/test_reset_db.py:170`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen`. Reason: simulate `URLError` (broker unreachable).
- **`tests/test_reset_db.py:184`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen`. Reason: simulate `TimeoutError` on GET.
- **`tests/test_reset_db.py:206-208`** — Inline `fake_subprocess_run` closure that returns `MagicMock(returncode=0)`. Replaces: `subprocess.run`'s `CompletedProcess` return value. Reason: alembic shouldn't actually fire during `main()` test.
- **`tests/test_reset_db.py:210`** — `monkeypatch.setattr(reset_db.subprocess, "run", fake_subprocess_run)`. Replaces: `subprocess.run` as imported in `scripts.reset_db`. Reason: prevent real alembic invocation.
- **`tests/test_reset_db.py:216`** — `monkeypatch.setattr(reset_db.urllib.request, "urlopen", fake_urlopen)`. Replaces: `urllib.request.urlopen`. Reason: zero-queue stub so the purge phase of `main()` is a no-op.

### tests/test_seed_example_bots.py

- **`tests/test_seed_example_bots.py:79`** — `monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)`. Replaces: the `EXAMPLE_BOTS_DIR` module attribute in `scripts.seed_example_bots`. Reason: point the seeder at a `tmp_path` instead of the repo's real `example-bots/` directory.
- **`tests/test_seed_example_bots.py:80`** — `monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)`. Replaces: `scripts.seed_example_bots.make_queue`. Reason: inject `_RecordingQueue` so `main()` doesn't connect to RabbitMQ.
- **`tests/test_seed_example_bots.py:122`** — `monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)`. Replaces: `EXAMPLE_BOTS_DIR`. Reason: tmp dir for duplicate-name versioning test.
- **`tests/test_seed_example_bots.py:123`** — `monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)`. Replaces: `make_queue`. Reason: inject `_RecordingQueue`.
- **`tests/test_seed_example_bots.py:148`** — `monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)`. Replaces: `EXAMPLE_BOTS_DIR`. Reason: tmp dir for nameless-file test.
- **`tests/test_seed_example_bots.py:149`** — `monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)`. Replaces: `make_queue`. Reason: inject `_RecordingQueue`.
- **`tests/test_seed_example_bots.py:178`** — `monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)`. Replaces: `EXAMPLE_BOTS_DIR`. Reason: tmp dir for unsupported-python-version test.
- **`tests/test_seed_example_bots.py:179`** — `monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)`. Replaces: `make_queue`. Reason: inject `_RecordingQueue`.
- **`tests/test_seed_example_bots.py:201`** — `monkeypatch.setattr(seed, "EXAMPLE_BOTS_DIR", tmp_path)`. Replaces: `EXAMPLE_BOTS_DIR`. Reason: empty tmp dir for the empty-directory test.
- **`tests/test_seed_example_bots.py:202`** — `monkeypatch.setattr(seed, "make_queue", lambda: mock_queue)`. Replaces: `make_queue`. Reason: inject `_RecordingQueue`.

### tests/test_submission.py

(No inline test doubles. Tests use the `client` fixture which carries the `mock_queue` dependency override transitively; `mock_queue` is also taken directly by the "queue enqueue behavior" tests at lines 320, 329, 343 to assert on messages — that's not a *new* double, just direct access to the already-installed `_RecordingQueue`. The `client.cookies.set(...)` call at `tests/test_submission.py:138` writes a real cookie into the in-process `TestClient`, not a mock.)

### tests/test_turn_worker.py

- **`tests/test_turn_worker.py:75`** — `unittest.mock.patch("runner.turn_worker.tempfile.NamedTemporaryFile", capturing_factory)` (context manager). Replaces: `tempfile.NamedTemporaryFile` as seen by `runner.turn_worker`, with a `capturing_factory` that delegates to the real implementation while recording the produced path. Reason: capture the tmpfile path so the test can assert cleanup afterwards.
- **`tests/test_turn_worker.py:91`** — `unittest.mock.patch("runner.turn_worker.subprocess.run", side_effect=boom)` (context manager). Replaces: `subprocess.run` as seen by `runner.turn_worker`, with a side-effect that raises `OSError`. Reason: exercise the catch-all "runtime error" branch without an actual broken environment.
