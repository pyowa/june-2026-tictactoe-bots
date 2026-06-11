# Mutation Testing Findings

## Overall Summary

**Mutation score: 915 killed / 1191 testable ≈ 77%**

**196 surviving mutants** (193 survived + 3 timeout) spread across:

| Module | Survived | Notes |
|--------|----------|-------|
| `entities/move/repository.py` | 3 | Actionable |
| `entities/match/repository.py` | 16 | Actionable |
| `db/session.py` | 1 | Actionable |
| `runner/match_loop.py` | 28 | Actionable |
| `runner/bot_subprocess.py` | 10 + 2 timeout | Actionable; 2 are timeouts |
| `web/submit.py` | 18 | Actionable |
| `web/templates.py` | 6 | Actionable |
| `web/utils.py` | 14 | Actionable |
| `web/main.py` | 3 | Actionable |
| `messaging/log.py` | 7 | External infra — see note |
| `messaging/client.py` | 1 | External infra — see note |
| `messaging/rpc_client.py` | 6 + 1 timeout | External infra — see note |
| `messaging/rabbitmq.py` | 11 | External infra — see note |
| `scripts/seed_example_bots.py` | 22 | External infra — see note |
| `scripts/reset_db.py` | 26 | External infra — see note |

> **Note on external-infra modules:** `messaging/rabbitmq.py`, `messaging/rpc_client.py`,
> `messaging/client.py`, `messaging/log.py`, `scripts/reset_db.py`, and
> `scripts/seed_example_bots.py` all require a live RabbitMQ broker and/or a real database
> connection to exercise end-to-end. The surviving mutants in these files are expected and
> acceptable: catching them would require integration tests running against a real broker /
> live DB, which is deliberately out of scope for the unit test suite.

---

## `entities/move/repository.py`

### ORDER BY clause and JOIN condition not verified

Tests fetch moves for a match but do not assert that the result is ordered by `move_number`,
nor do they verify that the JOIN uses the correct ON expression.

```diff
# mutmut_3 — ORDER BY stripped
-        .order_by(Move.move_number)
+        .order_by(None)

# mutmut_6 — JOIN condition replaced with None
-        .join(Bot, Move.bot_id == Bot.id)
+        .join(Bot, None)

# mutmut_8 — JOIN condition dropped entirely
-        .join(Bot, Move.bot_id == Bot.id)
+        .join(Bot, )
```

**What would catch it:** Assert that the returned list is sorted by `move_number` ascending
(e.g. insert moves out of order and check the order is corrected). For the JOIN, assert that
the returned `bot_name` field is populated with the correct bot's name and not `None`.

---

## `entities/match/repository.py`

### Table alias names not verified

The `_match_select` helper creates three SQL aliases (`bx`, `bo`, `bw`) for the `Bot` table.
Tests do not assert on the alias strings, so mutants that change or nullify them all survive.

```diff
# mutmut_2 — bx alias nulled
-    bx = Bot.__table__.alias("bx")
+    bx = Bot.__table__.alias(None)

# mutmut_3 / mutmut_4 — bx alias mangled
+    bx = Bot.__table__.alias("XXbxXX")
+    bx = Bot.__table__.alias("BX")

# mutmut_6 / mutmut_7 / mutmut_8 — bo alias mutated
-    bo = Bot.__table__.alias("bo")
+    bo = Bot.__table__.alias(None)   # _6
+    bo = Bot.__table__.alias("XXboXX")  # _7
+    bo = Bot.__table__.alias("BO")   # _8

# mutmut_10 / mutmut_11 / mutmut_12 — bw alias mutated
-    bw = Bot.__table__.alias("bw")
+    bw = Bot.__table__.alias(None)   # _10
+    bw = Bot.__table__.alias("XXbwXX")  # _11
+    bw = Bot.__table__.alias("BW")   # _12
```

**What would catch it:** The alias strings are internal SQL plumbing and are not observable
in the result set directly. These mutants are essentially equivalent — changing an alias name
does not change query semantics as long as the aliases are used consistently. They are safe
to accept.

### Winner column label not verified

```diff
# mutmut_33 — winner column replaced with None
-            bw.c.versioned_name.label("winner"),
+            None,

# mutmut_43 — winner column dropped entirely
-            bw.c.versioned_name.label("winner"),

# mutmut_64 / mutmut_65 / mutmut_66 — winner label string mutated
+            bw.c.versioned_name.label(None)       # _64
+            bw.c.versioned_name.label("XXwinnerXX")  # _65
+            bw.c.versioned_name.label("WINNER")   # _66
```

**What would catch it:** Assert that the `winner` attribute of a returned row matches the
expected winner's `versioned_name`. Tests that access `row.winner` directly will fail if the
column is dropped or renamed.

### JOIN operator for winner (outerjoin condition) not verified

```diff
# mutmut_69 — == flipped to != in outerjoin condition
-        .outerjoin(bw, Match.winner_id == bw.c.id)
+        .outerjoin(bw, Match.winner_id != bw.c.id)
```

**What would catch it:** Assert that `winner` is `None` when there is no winner (draw/forfeit)
and matches the winning bot's name when there is one. This requires at least two test cases:
a completed match with a winner, and a drawn match.

### `list_for_bot` OR filter not fully tested

```diff
# mutmut_3 — order_by stripped
-    ).order_by(Match.played_at.desc())
+    ).order_by(None)

# mutmut_6 — second OR arm nulled
-        or_(bx.c.base_name == base_name, bo.c.base_name == base_name)
+        or_(bx.c.base_name == base_name, None)

# mutmut_8 — second OR arm dropped
+        or_(bx.c.base_name == base_name, )

# mutmut_10 — second OR arm flipped to !=
+        or_(bx.c.base_name == base_name, bo.c.base_name != base_name)
```

**What would catch it:** Test a bot that appears as **O** (not X) and verify it is included
in results. Assert ordering (newest first) by inserting two matches at different timestamps.

### `record` — move error field not stored

```diff
# mutmut_35 — move.error replaced with None
-                error=move.error,
+                error=None,

# mutmut_40 — error kwarg dropped entirely
-                error=move.error,
```

**What would catch it:** Record a match containing a forfeit move (where `error` is non-None)
and then read back the moves; assert the `error` field on the stored row matches the original.

---

## `db/session.py`

### `expire_on_commit` flag not tested

```diff
# mutmut_9 — expire_on_commit=False replaced with None
-    session_factory = async_sessionmaker(_engine, expire_on_commit=False)
+    session_factory = async_sessionmaker(_engine, expire_on_commit=None)
```

**What would catch it:** After committing a session, access an attribute on a previously
fetched ORM object and assert it is still available without triggering a new DB round-trip.
This is hard to unit-test without a live DB; it is reasonable to accept this survivor.

---

## `runner/match_loop.py`

### RPC payload key names not verified

The `_request_turn` function serialises the turn request as JSON. Tests do not inspect the
exact key names in the serialised payload, so any key renaming survives.

```diff
# mutmut_3 / mutmut_4 — "symbol" key mangled
-            "symbol": symbol,
+            "XXsymbolXX": symbol,   # _3
+            "SYMBOL": symbol,        # _4

# mutmut_5 / mutmut_6 — "board" key mangled
-            "board": board_to_str(board),
+            "XXboardXX": board_to_str(board),  # _5
+            "BOARD": board_to_str(board),       # _6

# mutmut_14 / mutmut_15 — "correlation_id" key mangled
+            "XXcorrelation_idXX": correlation_id,  # _14
+            "CORRELATION_ID": correlation_id,       # _15

# mutmut_16 / mutmut_17 — "move_number" key mangled
+            "XXmove_numberXX": move_number,  # _16
+            "MOVE_NUMBER": move_number,       # _17
```

**What would catch it:** In `handle_turn` tests (bot_subprocess side), feed a payload with
the correct key names and assert failure with corrupted ones. On the `_request_turn` side,
spy on the bytes sent to the RPC queue and assert the JSON keys.

### `decode("ascii")` case sensitivity not verified

```diff
# mutmut_13 — codec string uppercased
-            "source_b64": base64.b64encode(source).decode("ascii"),
+            "source_b64": base64.b64encode(source).decode("ASCII"),
```

**What would catch it:** This is an equivalent mutant — Python's codec names are
case-insensitive, so `"ASCII"` and `"ascii"` behave identically. Safe to accept.

### RPC timeout argument not verified

```diff
# mutmut_33 — timeout=None passed instead of timeout value
-        response_bytes = await rpc.call(queue_name, payload, timeout=timeout)
+        response_bytes = await rpc.call(queue_name, payload, timeout=None)

# mutmut_36 — timeout kwarg dropped entirely
+        response_bytes = await rpc.call(queue_name, payload, )
```

**What would catch it:** Mock `rpc.call` and assert it is called with the expected `timeout`
keyword argument value.

### Forfeit error message arg not tested

```diff
# mutmut_65 — move_error replaced with None in _BotForfeit raise
-        raise _BotForfeit(move_error)
+        raise _BotForfeit(None)
```

**What would catch it:** After a rule-violating move, check that the recorded move's `error`
field contains a meaningful message (not `None`).

### Log call arguments not verified

Several mutants change or drop structured-log keyword arguments in `play_match_rpc`. These
survive because tests do not assert on log output.

```diff
# mutmut_37 / mutmut_47 / mutmut_48 — log event name mutated
-                    "turn_result",
+                    None            # _37
+                    "XXturn_resultXX"  # _47
+                    "TURN_RESULT"   # _48

# mutmut_39 / mutmut_44 — move_number kwarg nulled/dropped (forfeit log)
+                    move_number=None,  # _39
# (dropped in _44)

# mutmut_67 / mutmut_71 — correlation_id nulled/dropped (success log)
+                correlation_id=None,  # _67
# (dropped in _71)

# mutmut_68 / mutmut_72 — move_number nulled/dropped (success log)
+                move_number=None,  # _68
# (dropped in _72)
```

**What would catch it:** Capture structlog output (e.g. via `structlog.testing.capture_logs`)
and assert on the event name and key fields for both forfeit and normal-turn paths.

### Forfeit `Move` constructor arguments not tested

```diff
# mutmut_52 — move_number replaced with None
-                    Move(move_number, player, board_to_str(board), forfeit.error)
+                    Move(None, player, board_to_str(board), forfeit.error)

# mutmut_53 — player replaced with None
+                    Move(move_number, None, board_to_str(board), forfeit.error)

# mutmut_54 — board replaced with None
+                    Move(move_number, player, None, forfeit.error)
```

**What would catch it:** After a forfeit, inspect `match_result.moves[-1]` and assert that
`move_number`, `player`, and `board` fields all carry the expected values.

### Player token string for "o" not verified

```diff
# mutmut_10 / mutmut_11 — first element of "o" turn tuple mangled
-        ("o", "O", bot_o_source),
+        ("XXoXX", "O", bot_o_source),  # _10
+        ("O", "O", bot_o_source),       # _11
```

**What would catch it:** Assert that the `player` field of moves made by the O bot is
exactly `"o"` (lowercase), not `"O"` or anything else.

### `_BotForfeit.__init__` super() call not tested

```diff
# mutmut_1 — passes None to super().__init__ instead of the error string
-    super().__init__(error)
+    super().__init__(None)
```

**What would catch it:** Catch a `_BotForfeit` as an `Exception` and assert that
`str(exc)` or `exc.args[0]` equals the original error string.

---

## `runner/bot_subprocess.py`

### NamedTemporaryFile parameters not verified

Tests do not check the properties of the temp file used to write bot source.

```diff
# mutmut_1 — suffix nulled
-        suffix=".py", delete=False, mode="wb"
+        suffix=None, delete=False, mode="wb"

# mutmut_2 — delete=None instead of False
+        suffix=".py", delete=None, mode="wb"

# mutmut_4 — suffix dropped entirely
+        delete=False, mode="wb"

# mutmut_6 — mode kwarg dropped entirely
+        suffix=".py", delete=False,

# mutmut_7 / mutmut_8 — suffix string mangled
+        suffix="XX.pyXX", delete=False, mode="wb"  # _7
+        suffix=".PY", delete=False, mode="wb"       # _8
```

**What would catch it:** Pass bot source that relies on being in a `.py` file (e.g., check
that the subprocess `python` invocation receives a `.py` path). Assert the temp file is
cleaned up after use (i.e., `delete=False` followed by an explicit `os.unlink`).

### subprocess timeout argument not forwarded — TIMEOUT mutants

```diff
# mutmut_19 (timeout) — timeout=None passed to subprocess.run
-            timeout=timeout,
+            timeout=None,

# mutmut_24 (timeout) — timeout kwarg dropped entirely
-            timeout=timeout,
```

These two are **timeout mutants**: mutmut itself timed out running the test suite against
them (likely because the subprocess then ran indefinitely). They confirm the timeout is
load-bearing but the test harness can't kill the bot fast enough when the param is missing.

**What would catch it:** Run `run_bot_subprocess` with a bot that sleeps forever and assert
it returns within a short wall-clock time with the `"timeout after Xs"` error.

### `handle_turn` default sentinel values not verified

```diff
# mutmut_1 — correlation_id default changed to None
-    correlation_id = ""
+    correlation_id = None

# mutmut_2 — correlation_id default changed to "XXXX"
+    correlation_id = "XXXX"

# mutmut_3 — move_number default changed to None
-    move_number = 0
+    move_number = None

# mutmut_4 — move_number default changed to 1
+    move_number = 1
```

**What would catch it:** Call `handle_turn` with a malformed payload (missing
`correlation_id`/`move_number` keys) and assert the response JSON contains sensible default
values (`""` and `0` respectively), not `None` or off-by-one values.

### `correlation_id` fallback default in payload parsing not tested

```diff
# mutmut_14 — .get fallback changed from "" to None
-        correlation_id = str(payload.get("correlation_id", ""))
+        correlation_id = str(payload.get("correlation_id", None))

# mutmut_16 — fallback arg dropped
+        correlation_id = str(payload.get("correlation_id", ))

# mutmut_19 — fallback changed to "XXXX"
+        correlation_id = str(payload.get("correlation_id", "XXXX"))
```

### `move_number` fallback default in payload parsing not tested

```diff
# mutmut_28 — fallback changed from 0 to 1
-        move_number = int(payload.get("move_number", 0))
+        move_number = int(payload.get("move_number", 1))
```

**What would catch it:** Send a payload that omits `correlation_id` and/or `move_number`,
then inspect the structured log output or the reply JSON to confirm the fallback values are
the expected defaults.

---

## `web/submit.py`

### `_SubmissionError.__init__` super() call not tested

```diff
# mutmut_1 — passes None to super().__init__
-    super().__init__(message)
+    super().__init__(None)
```

**What would catch it:** Catch a `_SubmissionError` as an `Exception` and assert `str(exc)`
or `exc.args[0]` matches the message string.

### Source decoding parameters not verified

```diff
# mutmut_4 — encoding arg dropped (falls back to system default)
-    source = source_bytes.decode("utf-8", errors="replace")
+    source = source_bytes.decode(errors="replace")

# mutmut_5 — errors kwarg dropped
+    source = source_bytes.decode("utf-8", )

# mutmut_7 — encoding uppercased (equivalent mutant)
+    source = source_bytes.decode("UTF-8", errors="replace")

# mutmut_8 / mutmut_9 — errors value mangled
+    source = source_bytes.decode("utf-8", errors="XXreplaceXX")  # _8
+    source = source_bytes.decode("utf-8", errors="REPLACE")       # _9
```

**What would catch it:** Submit source bytes containing a lone surrogate or invalid UTF-8
byte and assert the request succeeds (replacement char used, not exception) and the name is
still extracted. Alternatively, the `"UTF-8"` / `"REPLACE"` cases are equivalent mutants —
Python's codec strings are case-insensitive — and can be accepted.

### Error message text not verified

Tests assert that a `_SubmissionError` is raised, but not its message content.

```diff
# mutmut_13 — no-name error message nulled
-            "Your bot must start with a docstring containing 'name: <name>'."
+            None

# mutmut_14 / mutmut_15 / mutmut_16 — message string mutated
+            "XXYour bot must start with a docstring containing 'name: <name>'.XX"
+            "your bot must start with a docstring containing 'name: <name>'."
+            "YOUR BOT MUST START WITH A DOCSTRING CONTAINING 'NAME: <NAME>'."

# mutmut_19 / mutmut_20 — reserved-name error second-line mutated
+            "XXreserved for auto-versioning. Pick a different name.XX"
+            "reserved for auto-versioning. pick a different name."

# mutmut_26 / mutmut_29 / mutmut_30 / mutmut_31 — invalid python error mutated
+            "XXInvalid 'python:' value in docstring. XX"
+            "XXUse a version like '3', '3.11', or '3.12'.XX"
+            "use a version like '3', '3.11', or '3.12'."
+            "USE A VERSION LIKE '3', '3.11', OR '3.12'."
```

**What would catch it:** Assert `exc.message` (or the HTTP response body text) contains the
exact expected error string for each failure path. A substring check suffices for most.

### Owner token length not tested

```diff
# mutmut_6 — token_hex(32) becomes token_hex(None)
-    return secrets.token_hex(32)
+    return secrets.token_hex(None)
```

**What would catch it:** Assert that the returned owner token has length 64 (hex of 32 bytes).

### Success-response template name and context keys not tested

```diff
# mutmut_10 — template name uppercased
-        "index.html",
+        "INDEX.HTML",

# mutmut_11 / mutmut_12 — "bots" context key mangled
+        {"XXbotsXX": bots, ...}
+        {"BOTS": bots, ...}

# mutmut_22 — samesite kwarg dropped from set_cookie
-        samesite="lax",
```

**What would catch it:** In integration tests, check that the response uses the correct
template name (or renders successfully), that the `bots` variable is accessible in the
template context, and that the cookie's `SameSite` attribute is `lax`.

### `handle_submission` log kwargs and call arguments not fully tested

```diff
# mutmut_35 — bot_id nulled in log
-            bot_id=new_bot_id,
+            bot_id=None,

# mutmut_36 — python_version nulled in log
-            python_version=python_version,
+            python_version=None,

# mutmut_45 — listing fetch replaced with None
-        listing = await bots.list_for_homepage()
+        listing = None

# mutmut_51 — name arg nulled in _success_response call
-    return _success_response(request, name, ...)
+    return _success_response(request, None, ...)

# mutmut_55 — listing arg nulled in _success_response call
+    return _success_response(request, name, owned, owner_token, bot_name, None)
```

**What would catch it:** Capture log output and assert `bot_id` and `python_version` fields.
For the listing/name args: assert the success page displays the bot's name and the updated
leaderboard (i.e., the listing is not empty).

---

## `web/templates.py`

### Template filename string not verified

```diff
# mutmut_8 — render_index_response template uppercased
-    return templates.TemplateResponse(request, "index.html", {...})
+    return templates.TemplateResponse(request, "INDEX.HTML", {...})

# mutmut_10 — not_found template uppercased
-    return templates.TemplateResponse(request, "404.html", {}, status_code=404)
+    return templates.TemplateResponse(request, "404.HTML", {}, status_code=404)
```

**What would catch it:** These are equivalent mutants on case-insensitive file systems
(macOS). On Linux (Docker) the filesystem is case-sensitive and the mutant would crash.
Consider adding an integration test that hits a 404 route and checks the 404 status code.

### Context dict key not verified in `render_index_response`

```diff
# mutmut_9 — "bots" key mangled
-    return templates.TemplateResponse(request, "index.html", {"bots": [], **ctx})
+    return templates.TemplateResponse(request, "index.html", {"XXbotsXX": [], **ctx})
```

**What would catch it:** Assert that the rendered template does not raise a `UndefinedError`
for `bots` (i.e., the template has access to that variable). An integration test that
renders the response body and checks for the leaderboard section will catch this.

### `not_found` context arg and status code not fully tested

```diff
# mutmut_3 — empty dict replaced with None
-    return templates.TemplateResponse(request, "404.html", {}, status_code=404)
+    return templates.TemplateResponse(request, "404.html", None, status_code=404)

# mutmut_7 — empty dict dropped entirely
+    return templates.TemplateResponse(request, "404.html", status_code=404)
```

**What would catch it:** Hit a non-existent route in an integration test and assert the
response status is 404 and the body renders without errors.

---

## `web/utils.py`

### Docstring field extraction off-by-one not caught

```diff
# mutmut_17 (extract_bot_name) — slice offset wrong
-            name = stripped[5:].strip()
+            name = stripped[6:].strip()

# mutmut_17 (extract_python_version) — slice offset wrong
-            version = stripped[7:].strip()
+            version = stripped[8:].strip()
```

**What would catch it:** Test with a single-character bot name (e.g. `name: A`). With
`stripped[6:]`, the first character of the name is consumed and the result becomes empty,
so `None` is returned. A test asserting `extract_bot_name` returns `"A"` from
`'"""name: A"""'` will catch this.

### `encode_cookie` `safe=""` argument not tested

```diff
# mutmut_4 — safe kwarg dropped
-    return urllib.parse.quote(json.dumps(owned), safe="")
+    return urllib.parse.quote(json.dumps(owned), )

# mutmut_6 — safe value mangled
+    return urllib.parse.quote(json.dumps(owned), safe="XXXX")
```

**What would catch it:** Encode a dict containing characters that `urllib.parse.quote`
would pass through by default (e.g., `/` or `:`), then assert the result is fully percent-
encoded. `safe=""` ensures `/` → `%2F`; without it (or with `safe="XXXX"`), `/` is left
as-is and cookie parsing breaks.

### Match-job correlation ID length not tested

```diff
# mutmut_13 — token_hex(None) for first enqueue
# mutmut_18 — token_hex(None) for first enqueue (same group)
# mutmut_19 — token_hex(17) for first enqueue (off-by-one)
# mutmut_28 — token_hex(None) for reverse enqueue
# mutmut_33 — token_hex(None) for reverse enqueue
# mutmut_34 — token_hex(17) for reverse enqueue (off-by-one)
```

```diff
-            MatchJob(new_bot_id, other.id, py, secrets.token_hex(16))
+            MatchJob(new_bot_id, other.id, py, None)             # _13
+            MatchJob(new_bot_id, other.id, py, secrets.token_hex(None))  # _18
+            MatchJob(new_bot_id, other.id, py, secrets.token_hex(17))    # _19

-                MatchJob(other.id, new_bot_id, py, secrets.token_hex(16))
+                MatchJob(other.id, new_bot_id, py, None)                  # _28
+                MatchJob(other.id, new_bot_id, py, secrets.token_hex(None))  # _33
+                MatchJob(other.id, new_bot_id, py, secrets.token_hex(17))    # _34
```

**What would catch it:** Capture the `MatchJob` objects enqueued and assert the
`correlation_id` field has length 32 (hex of 16 bytes).

### Return count arithmetic not tested

```diff
# mutmut_20 — first count += 1 changed to count = 1 (resets)
-        count += 1
+        count = 1

# mutmut_35 — second count += 1 changed to count = 1 (resets)
-            count += 1
+            count = 1

# mutmut_36 — second count += 1 changed to count -= 1
+            count -= 1

# mutmut_37 — second count += 1 changed to count += 2
+            count += 2
```

**What would catch it:** Call `enqueue_match_pairs` with N bots and assert the returned
count equals the expected number of jobs enqueued (e.g. N bots → N + (N-1) reverse jobs =
2N-1 total for a newly submitted bot).

---

## `web/main.py`

### Template name and context key names not verified

```diff
# mutmut_17 — template filename uppercased
-        "match_detail.html",
+        "MATCH_DETAIL.HTML",

# mutmut_22 / mutmut_23 — back_url key mangled
-            "back_url": back_url,
+            "XXback_urlXX": back_url,  # _22
+            "BACK_URL": back_url,       # _23
```

**What would catch it:** Integration-test the match-detail route; assert it returns 200,
and that the template has access to `back_url` (check the rendered HTML contains the
expected back-link href). On Linux the filename case is also load-bearing.

---

## `messaging/log.py`

> All survivors in this module are **expected / acceptable**. The `configure_logging`
> function configures structlog at process startup. Unit tests call application code that
> uses `_log`, but they do not assert on the structlog configuration itself (processor list,
> logger factory, timestamp format). Catching these would require either inspecting
> structlog's internal state after configuration or running tests that capture and parse
> structured log JSON output.

```diff
# mutmut_1 — processors list replaced with None
-        processors=[...],
+        processors=None,

# mutmut_2 — logger_factory nulled
-        logger_factory=structlog.PrintLoggerFactory(),
+        logger_factory=None,

# mutmut_3 — processors list dropped entirely

# mutmut_4 — logger_factory kwarg dropped entirely

# mutmut_5 — TimeStamper fmt=None
-            structlog.processors.TimeStamper(fmt="iso"),
+            structlog.processors.TimeStamper(fmt=None),

# mutmut_6 / mutmut_7 — TimeStamper fmt mangled
+            structlog.processors.TimeStamper(fmt="XXisoXX")
+            structlog.processors.TimeStamper(fmt="ISO")
```

---

## `messaging/client.py`

> Survivor is **expected / acceptable** — requires a live broker.

```diff
# mutmut_1 — BROKER_URL replaced with None
-    return RabbitMQQueue(BROKER_URL)
+    return RabbitMQQueue(None)
```

---

## `messaging/rpc_client.py`

> All survivors are **expected / acceptable** — the RPC client only functions against a
> live RabbitMQ connection; unit tests mock the underlying queue.

```diff
# mutmut_1 — default timeout changed from 10.0 to 11.0
-    self, target_queue: str, payload: bytes, timeout: float = 10.0
+    self, target_queue: str, payload: bytes, timeout: float = 11.0

# mutmut_7 / mutmut_9 — routing_key nulled or dropped
-        routing_key=target_queue,
+        routing_key=None,   # _7
# (dropped in _9)

# mutmut_12 / mutmut_16 — reply_to nulled or dropped
-            reply_to=self._reply_queue_name,
+            reply_to=None,  # _12
# (dropped in _16)

# mutmut_22 (timeout) — wait_for timeout=None (causes infinite wait)
-        return await asyncio.wait_for(future, timeout=timeout)
+        return await asyncio.wait_for(future, timeout=None)

# mutmut_27 — pending.pop default arg dropped
-        self._pending.pop(correlation_id, None)
+        self._pending.pop(correlation_id, )
```

---

## `messaging/rabbitmq.py`

> All survivors are **expected / acceptable** — require a live RabbitMQ connection.
> All paths under `_ensure_connected` and `close` are marked `# pragma: no cover`.

```diff
# mutmut___init___1 — self._url = None
# mutmut___init___3 — self._channel initial value changed to ""

# mutmut__ensure_connected_1 — "or" changed to "and" in guard condition
-    if self._connection is None or self._connection.is_closed:
+    if self._connection is None and self._connection.is_closed:

# mutmut__ensure_connected_3 — connection assignment nulled
-        self._connection = await aio_pika.connect_robust(self._url)
+        self._connection = None

# mutmut__ensure_connected_4 — URL replaced with None
+        self._connection = await aio_pika.connect_robust(None)

# mutmut__ensure_connected_5 — channel assignment nulled
-        self._channel = await self._connection.channel()
+        self._channel = None

# mutmut__ensure_connected_6 — queue name nulled
-        await self._channel.declare_queue(MATCHES_QUEUE, durable=True)
+        await self._channel.declare_queue(None, durable=True)

# mutmut__ensure_connected_7 — durable=None
+        await self._channel.declare_queue(MATCHES_QUEUE, durable=None)

# mutmut__ensure_connected_8 — MATCHES_QUEUE arg dropped
+        await self._channel.declare_queue(durable=True)

# mutmut__ensure_connected_9 — durable kwarg dropped
+        await self._channel.declare_queue(MATCHES_QUEUE, )

# mutmut__ensure_connected_10 — durable=False
+        await self._channel.declare_queue(MATCHES_QUEUE, durable=False)

# mutmut_close_3 — "not" removed from is_closed guard
-    if self._connection is not None and not self._connection.is_closed:
+    if self._connection is not None and self._connection.is_closed:
```

---

## `scripts/seed_example_bots.py`

> All survivors are **expected / acceptable** — this script runs against a live database
> and RabbitMQ broker; unit-testing it would require full integration infrastructure.

**`enqueue_all_pairs`** — correlation ID for each match job not checked:
```diff
# mutmut_13 / mutmut_18 / mutmut_19
-            await queue.enqueue_match(MatchJob(x.id, o.id, py, secrets.token_hex(16)))
+            # token_hex(None), token_hex(17), or None
```

**`main`** — print strings mutated (many mutants), decode args mutated, control-flow changed:
```diff
# mutmut_1-4 — first print() message mutated
# mutmut_26-31 — source_bytes.decode() encoding/errors args mutated
# mutmut_36 — continue changed to break in "no name" guard
# mutmut_53 / mutmut_59 — source=source_bytes nulled or dropped from Bot constructor
# mutmut_60 / mutmut_61 — owner_token token_hex length mutated
# mutmut_65 — inserted-bot print() nulled
# mutmut_74-79 — final print() message strings mutated
```

---

## `scripts/reset_db.py`

> All survivors are **expected / acceptable** — requires a live RabbitMQ Management API
> and database. The function makes real HTTP calls and SQL DDL operations.

**`purge_rabbitmq_queues`** — HTTP auth, headers, timeouts, and URL quoting not tested:
```diff
# mutmut_1 — auth nulled
# mutmut_3 — auth_header nulled
# mutmut_8-11 — headers dict dropped or Authorization key mangled
# mutmut_13 / mutmut_15 / mutmut_16 — urlopen timeout mutated
# mutmut_29 / mutmut_33 / mutmut_36 — vhost URL quoting args mutated
# mutmut_41 / mutmut_42 — encoded name URL quoting args mutated
# mutmut_50 / mutmut_53-55 — DELETE request headers mutated
# mutmut_57 / mutmut_59 / mutmut_60 — per-queue urlopen timeout mutated
# mutmut_63 / mutmut_65 / mutmut_68 — messages default in print() mutated
```

**`_drop_all_tables`** — raw SQL string case not tested:
```diff
# mutmut_7 — SQL lowercased
-        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
+        await conn.execute(text("drop table if exists alembic_version"))

# mutmut_8 — SQL uppercased
+        await conn.execute(text("DROP TABLE IF EXISTS ALEMBIC_VERSION"))
```

These are equivalent mutants — SQLite and PostgreSQL both accept case-insensitive SQL keywords.

**`main`** — print strings mutated:
```diff
# mutmut_3 — "Running migrations..." message mangled
# mutmut_19 — "Done." message mangled
```
