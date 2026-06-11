# Plan: dynamic per-language bot runners on Kubernetes

Sketch of moving the worker fleet from a hardcoded set of `worker-pyX.Y` compose services to a data-driven pool of per-message Kubernetes Jobs. Goal: adding a new language (or Python version) becomes "add one row to a server-side allowlist + publish an image", not "edit `docker-compose.yml`".

## Goals / non-goals

**Goals**

- One generic worker plane. No more `worker-py3.10` ... `worker-py3.14` services.
- Bot author picks a runtime by name (`language: python-3.13` or `language: rust-1.75`); the server maps that to an image from a curated allowlist.
- Each bot turn runs in a fresh, sandboxed, network-isolated, time-bounded container.
- Local dev experience stays close to what we have now (one command to bring everything up, fast feedback).
- Production path lands on Azure AKS without re-architecting.

**Non-goals (for this pass)**

- Letting bot authors specify *arbitrary* images. The allowlist stays server-controlled.
- Replacing `db` / `rabbitmq` / `web` / `orchestrator` with k8s. They stay in compose locally.
- Auto-scaling the dispatcher. Single replica is fine for an event-sized workload; revisit if it becomes a bottleneck.

## Hybrid local architecture

```
┌──────────── docker compose (unchanged) ────────────┐    ┌──────── kind cluster ─────────┐
│                                                    │    │                               │
│   db (Postgres)     ─┐                             │    │   dispatcher (Deployment)     │
│   rabbitmq          ─┤                             │    │     consumes turn.requests    │
│   web (FastAPI)      │ <─── turn.requests ────────────────▶  creates Job per message    │
│   orchestrator      ─┘ <─── turn.replies   ──────── │ ←──     reads logs                │
│                                                    │    │     publishes reply           │
└────────────────────────────────────────────────────┘    │                               │
                                                          │   bot-runner-python:3.13      │
                                                          │   bot-runner-rust:1.75   ─── ephemeral Jobs
                                                          │   bot-runner-go:1.22          │
                                                          └───────────────────────────────┘
```

- Compose continues to host the stateful + long-lived app services (db, rabbit, web, orchestrator). Bind mounts + `--reload` for web/orchestrator stay.
- A `kind` cluster runs on the same Docker daemon. It hosts the `dispatcher` Deployment and the ephemeral per-turn bot Jobs.
- The two infrastructures talk over RabbitMQ. From inside kind, the compose rabbit is reachable at `host.docker.internal:5672` (or via a kind-to-compose network attach).

## Component changes

### 1. Bot frontmatter

Replace the existing `python: 3.13` field with a more general `language: <key>`:

```python
"""
name: My Awesome Bot
language: python-3.13
"""
```

`language:` is validated at upload time against a server-side allowlist (see web change below). The previous `python: X` form can be supported as an alias mapping to `python-X` for backwards compatibility, or deprecated outright.

### 2. Server-side allowlist

Single source of truth for "which runtimes does this site support". Lives in code (not compose, not k8s YAML):

```python
# web/runtimes.py
RUNTIMES: dict[str, "Runtime"] = {
    "python-3.10":  Runtime(image="pyowa/bot-runner-python:3.10",   interpreter="python",     ext=".py"),
    "python-3.11":  Runtime(image="pyowa/bot-runner-python:3.11",   interpreter="python",     ext=".py"),
    "python-3.12":  Runtime(image="pyowa/bot-runner-python:3.12",   interpreter="python",     ext=".py"),
    "python-3.13":  Runtime(image="pyowa/bot-runner-python:3.13",   interpreter="python",     ext=".py"),
    "python-3.14":  Runtime(image="pyowa/bot-runner-python:3.14",   interpreter="python",     ext=".py"),
    "rust-1.75":    Runtime(image="pyowa/bot-runner-rust:1.75",     interpreter="cargo-run",  ext=".rs"),
    "go-1.22":      Runtime(image="pyowa/bot-runner-go:1.22",       interpreter="go-run",     ext=".go"),
    "java-26":      Runtime(image="pyowa/bot-runner-java:26",       interpreter="java",       ext=".java"),   # JDK 26 (Mar 2026) — single-file mode (`java Foo.java`). For LTS use `java-25` instead.
    "csharp-14":    Runtime(image="pyowa/bot-runner-csharp:14",     interpreter="dotnet-run", ext=".cs"),     # C# 14 / .NET 10 (LTS, Nov 2025) — file-based apps (`dotnet run app.cs`). C# 15 is still preview-only.
    "node-26":      Runtime(image="pyowa/bot-runner-node:26",       interpreter="node",       ext=".js"),     # Node.js 26 (May 2026, Current; LTS promotion in Oct 2026). For production-stable use `node-24` (Active LTS).
    "kotlin-2.4":   Runtime(image="pyowa/bot-runner-kotlin:2.4",    interpreter="kotlin",     ext=".kts"),    # Kotlin 2.4.0 (Jun 2026) — script mode (`.kts`) avoids the compile step
    "clojure-1.12": Runtime(image="pyowa/bot-runner-clojure:1.12",  interpreter="clojure",    ext=".clj"),    # Clojure 1.12.5 (latest stable) via the `clojure` CLI
    "cpp-gcc16":    Runtime(image="pyowa/bot-runner-cpp:gcc16",     interpreter="g++-run",    ext=".cpp"),    # GCC 16.1 (Apr 2026) — C++20 default, compile-then-exec wrapper
    "dart-3.12":    Runtime(image="pyowa/bot-runner-dart:3.12",     interpreter="dart-run",   ext=".dart"),   # Dart 3.12 (May 2026) via `dart run`
    "ruby-4.0":     Runtime(image="pyowa/bot-runner-ruby:4.0",      interpreter="ruby",       ext=".rb"),     # Ruby 4.0.5 (May 2026)
}
```

Adding a language is now: append one row + publish the image.

### 3. Web upload validation

`web/main.py` already rejects unknown Python versions. Generalize that check to `RUNTIMES.keys()`. On success, store the chosen runtime key on the bot row alongside `versioned_name` / `source` / etc.

### 4. Orchestrator: one queue, not five

Today, the orchestrator picks the per-version queue (`turn.py313.requests`) based on the bot's Python version. With per-message images we collapse to a single queue:

- `turn.requests` — every turn for every language. Payload carries `{symbol, board, source, image, runtime_key}` plus the existing correlation_id + reply_to.
- `turn.replies.<orchestrator-instance>` — unchanged (exclusive reply queue per orchestrator).

The per-version queues go away entirely.

### 5. New: the dispatcher

A small Python service running as a k8s Deployment inside kind/AKS. Responsibilities:

1. Consume one message from `turn.requests`.
2. Look up the runtime from the message payload, fail-fast if not in the allowlist (defense-in-depth — the web layer already validated, but trust nothing).
3. Build a `batch/v1 Job` manifest with:
   - `spec.template.spec.containers[0].image` = the runtime's image
   - source code injected as a Secret (cleaner than env-var for multi-line) or downward-API file
   - board + symbol passed via env vars
   - sandboxing (see next section)
4. `kubectl create` (via the k8s Python client), wait for completion or deadline.
5. Read the pod's stdout via the logs API. That stdout is the bot's reply.
6. Publish `{result, correlation_id}` to the orchestrator's reply queue.
7. Delete the Job (or rely on `ttlSecondsAfterFinished`).
8. Ack the original message.

Why a dispatcher and not KEDA's `ScaledJob`? `ScaledJob`'s `jobTargetRef` is static — it can't pick the image per message. We need a tiny controller that *reads* the message and *then* builds the Job. KEDA could still scale the dispatcher itself if throughput matters; not needed initially.

### 6. New: per-runtime images

Each `pyowa/bot-runner-<lang>:<version>` is a tiny image: the language base (`python:3.13-slim`, `rust:1.75-slim`, etc.) plus a uniform entrypoint:

```bash
#!/bin/sh
# /entrypoint.sh — same shape for every runtime image, parameterized at build time.
exec "$INTERPRETER" /bot/source < /dev/stdin
```

The dispatcher mounts `/bot/source` from a Secret and writes the board to stdin via the Job spec. Each image only needs the language toolchain installed — no RabbitMQ client, no app code.

### 7. Sandboxing primitives on the Job

The Job spec uses pod-level isolation rather than docker flags:

```yaml
spec:
  activeDeadlineSeconds: 5            # hard kill at 5s
  template:
    spec:
      automountServiceAccountToken: false
      restartPolicy: Never
      containers:
      - name: bot
        image: pyowa/bot-runner-python:3.13
        resources:
          limits: { cpu: "500m", memory: "256Mi" }
          requests: { cpu: "100m", memory: "64Mi" }
        securityContext:
          readOnlyRootFilesystem: true
          runAsNonRoot: true
          allowPrivilegeEscalation: false
          capabilities: { drop: ["ALL"] }
        volumeMounts:
        - name: source
          mountPath: /bot
          readOnly: true
      volumes:
      - name: source
        secret:
          secretName: bot-source-<correlation-id>
```

Plus a default-deny `NetworkPolicy` in the bot-runner namespace:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: bot-isolation, namespace: bots }
spec:
  podSelector: { matchLabels: { app: bot-runner } }
  policyTypes: [Ingress, Egress]
  ingress: []   # nothing reaches the bot pod
  egress: []    # bot pod reaches nothing
```

That covers the "no outside world" requirement at the cluster level — even DNS is blocked.

## What changes in `docker-compose.yml`

Removed:

- `worker-py310`, `worker-py311`, `worker-py312`, `worker-py313`, `worker-py314`
- The `x-worker-base` anchor

Stays:

- `db`, `rabbitmq`, `migrate`, `web`, `orchestrator`, `mutmut` (under the `mutmut` profile)

The orchestrator's turn-RPC code changes target queue from `turn.py{X}{Y}.requests` to a single `turn.requests`. That's the only application code change for the existing stack.

## What's new on disk

```
flake.nix                       # pins kind, kubectl, helm (and uv) — see "Tooling via Nix" below
flake.lock                      # generated; commit this so everyone gets the same versions

k8s/
├── dispatcher/
│   ├── Deployment.yaml
│   ├── ServiceAccount.yaml
│   ├── Role.yaml              # create/get/delete Jobs + Secrets in `bots` namespace
│   └── RoleBinding.yaml
├── network-policy.yaml         # default-deny for the `bots` namespace
└── kind-cluster.yaml           # cluster config (one control-plane, one worker node)

dispatcher/                      # new Python module — runs inside the dispatcher pod
├── __init__.py
├── main.py                     # rabbit consumer loop
├── jobs.py                     # build Job manifest, watch for completion, fetch logs
└── runtimes.py                 # shared allowlist (mirrors web/runtimes.py)

bot-runner-images/
├── python/Dockerfile           # ARGS: PY_VERSION
├── rust/Dockerfile
└── go/Dockerfile
```

## Tooling via Nix

The entire dev toolchain is pinned in a `flake.nix` at the repo root — Python interpreter, `uv`, Postgres client, container runtime (Colima), Docker CLI, k8s tools, and the handful of small utilities you actually reach for daily (`jq`, etc.). No `brew install`, no Homebrew taps, no version drift between machines. Nix is already installed on the dev machine (`nix 2.34.7` with flakes enabled).

```nix
# flake.nix (sketch)
{
  description = "pyowa tic-tac-toe-event dev shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            # Python side — matches the version used by web/orchestrator in compose
            python313          # interpreter on PATH; uv discovers it via UV_PYTHON below
            uv                 # package + virtualenv manager (already the project's tool)

            # Postgres client — `psql`, `pg_isready`, `pg_dump`. pg_isready is what
            # the `poe check` precheck would call to fail-fast when the DB isn't up.
            postgresql_16      # client tools (server isn't started; matches the compose `postgres:latest` major)

            # Container runtime + CLI (replaces Docker Desktop)
            colima             # Lima VM on macOS, exposes a Docker-compatible socket
            docker-client      # the `docker` CLI
            docker-compose     # compose v2

            # Kubernetes side
            kind               # local k8s in Docker
            kubectl            # k8s CLI
            kubernetes-helm    # `helm` (the package is named `kubernetes-helm` in nixpkgs)
            k9s                # TUI for inspecting cluster state

            # Small utilities used in scripts / day-to-day
            jq                 # JSON wrangling — k8s/docker output, RabbitMQ mgmt API
            curl               # health checks, RabbitMQ mgmt API hits in scripts/reset_db.py
          ];

          # Pin uv to Nix's Python so it doesn't silently download its own —
          # otherwise the flake stops being the single source of truth for the
          # interpreter version.
          shellHook = ''
            export UV_PYTHON=${pkgs.python313}/bin/python3.13
            export UV_PYTHON_DOWNLOADS=never
          '';
        };
      });
}
```

Enter the shell with `nix develop` (or set up direnv with `use flake` in `.envrc` so it auto-loads on `cd`). Everything the project needs is then on `PATH` — no host installs required beyond Nix itself.

A few choices worth flagging:

- **One Python version in the flake, many in containers.** The flake pins `python313` because that's what the host actually runs against (uv + tests + IDE language server). The 3.10–3.14 fleet for bot execution lives in the per-runtime images built into kind / pushed to ACR — those don't need to be on the host. If a contributor ever needs to run tests against a different Python locally, they can `nix shell nixpkgs#python312` ad-hoc; the canonical version stays one.
- **`UV_PYTHON_DOWNLOADS=never`** is deliberate. Without it, uv will happily fetch its own Python the first time it sees a `requires-python` it doesn't recognize, and you've quietly lost the Nix-pinned version. The shellHook makes that a hard error instead of a silent drift.
- **`postgresql_16` is client-only here.** Including the package gives you `psql`/`pg_isready` on PATH; the actual server still runs in compose. Picking `_16` matches the compose `postgres:latest` major at the time of writing — bump in lockstep if/when compose upgrades.

### Colima sizing for the kind cluster

The default `colima start` profile is 2 CPU / 2 GiB RAM, which is fine for the existing compose stack but will be tight once a kind cluster + KEDA + ephemeral bot Jobs are also running inside the same VM. Bump it once on first start:

```bash
colima start --cpu 4 --memory 8 --disk 60
```

Adjust upward if `kubectl top nodes` shows pressure during a real workload. Colima persists this profile, so subsequent `colima start` calls reuse the same sizing.

### Colima ↔ kind ↔ compose plumbing

Two specifics to be aware of:

- **`host.docker.internal`** is what the dispatcher inside the kind cluster uses to reach RabbitMQ in compose on the host. Colima sets this up by default (it points at the host from inside the VM), same as Docker Desktop — no extra config needed.
- **Bind mounts** (the live-reload story for `web/` and `orchestrator/`) pass through Colima's `--mount-type` (`sshfs` by default, `virtiofs` for faster I/O on newer Colima versions). Compose bind mounts work either way; if file-watch reload feels sluggish, swap to `virtiofs`.

## Local bootstrap (one-time)

```bash
nix develop                     # drops you into a shell with kind/kubectl/helm/uv on PATH
kind create cluster --config k8s/kind-cluster.yaml
kubectl create namespace bots
kubectl apply -f k8s/network-policy.yaml
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --namespace keda --create-namespace   # optional initially
# build + load the runtime images into kind so pods can pull them without a registry
docker build -t pyowa/bot-runner-python:3.13 -f bot-runner-images/python/Dockerfile --build-arg PY_VERSION=3.13 .
kind load docker-image pyowa/bot-runner-python:3.13
# ...repeat for other runtimes
kubectl apply -f k8s/dispatcher/
```

Day-to-day:

```bash
nix develop                     # (or direnv auto-loads it)
docker compose up -d            # db, rabbit, web, orchestrator
kind get clusters || kind create cluster --config k8s/kind-cluster.yaml
# dispatcher is already running in the kind cluster
```

## Azure path (AKS)

The shape ports over with minimal change:

- `kind` → AKS cluster (managed by Azure; you only manage node pools).
- Local registry (`kind load docker-image`) → Azure Container Registry (ACR). `pyowa/bot-runner-*:*` images get pushed to ACR; AKS pulls from ACR via managed identity.
- Compose stack → Container Apps (or AKS Deployments) for web/orchestrator; Azure Database for PostgreSQL for `db`; RabbitMQ on AKS (or swap to Azure Service Bus, which would mean changing `messaging/`).
- Manifests under `k8s/` apply unchanged against AKS. The `Job` / `NetworkPolicy` / `RBAC` primitives are identical.
- KEDA add-on on AKS is a checkbox in the portal / one-line in Terraform if/when we want autoscaling.

## Open questions / things to nail down

1. **Source delivery.** Secret-per-job works but creates K8s API churn (create/delete per turn). Alternatives: env-var (limited to ~1MB, fine for bots), `emptyDir` + init container that writes the source, or a ReadWriteMany PVC keyed by correlation_id. Default to Secret + `ttlSecondsAfterFinished` for now; reassess if API server load is a problem.
2. **Compiled languages.** Rust/Go can't just `run /bot/source` — they need a compile step. Options: (a) require source to be a single file and `cargo script` / `go run`-equivalent it; (b) accept a tarball + Makefile-style contract; (c) only support interpreted languages initially. Easiest start: (a) or (c).
3. **Cold-start latency.** Each Job pulls the image (cached on the node after the first pull) + cold-starts a container. Expect 1–3s overhead per turn. Acceptable for a game with sub-second move budgets? Might push the per-turn timeout up or warm a pool of pods per popular runtime.
4. **Result correlation.** Dispatcher reads pod logs to get the bot's output. Need to decide: does the bot's stdout *only* contain the new board, or can it print debug lines? Current contract is "stdout = new board", so dispatcher takes the last non-empty line. Document in `README.md` if it changes.
5. **What about the bot's stderr?** Today subprocess captures stderr; with pod logs we get both interleaved unless we redirect. Probably want the entrypoint to send stderr somewhere we ignore so the result parse stays simple.
6. **Mutation testing.** `mutmut` currently runs through the compose `mutmut` profile and doesn't touch workers. Unchanged here.
7. **Test strategy.** Existing tests mock the queue and don't hit a real broker. The dispatcher gets its own unit tests (mock the k8s client + the rabbit consumer). A separate live-stack acceptance suite hits the real cluster — see the dedicated section below.

## Live-stack acceptance tests

The unit-test world (which mocks the queue and the k8s client) covers the *code* — does the dispatcher build a sane Job manifest, does it parse stdout right, does it publish the reply with the correlation_id. It doesn't cover the *infrastructure*: is the kind cluster actually up, is KEDA's RBAC right, does the NetworkPolicy actually block egress, do the runtime images we built actually run a bot. Those are the questions the acceptance suite answers.

### Scope

A small set of opt-in tests that act as **external clients** against the real running stack — they don't import `web/`, `orchestrator/`, or `dispatcher/`. They publish messages, wait for replies, hit `/submit`, query the leaderboard. If any of them fail, something about the *deployment* is broken even if every unit test is green.

Worth having:

1. **Plumbing smoke test.** Publish a synthetic turn request directly to `turn.requests` with a known-good Python bot source. Subscribe to a reply queue. Assert: a reply arrives within N seconds, with the matching correlation_id, with a valid board. Catches: dispatcher down, KEDA misconfig, image not loaded into kind, RBAC denying Job creation.
2. **Per-runtime smoke test.** For each entry in `RUNTIMES`, ship a "place in the first empty cell" reference bot in that language and assert the move is correct. One parametrized test, N cases. Catches: runtime image broken, entrypoint wrong, single-file invocation contract drifted.
3. **Sandbox enforcement.** A bot whose source attempts `socket.connect(("8.8.8.8", 53))` (or the language equivalent) — assert it forfeits with a network error, not a successful move. Catches: NetworkPolicy missing, allowing egress, applied to the wrong namespace.
4. **Timeout enforcement.** A bot that sleeps past `activeDeadlineSeconds` — assert it's killed and the orchestrator records a forfeit. Catches: Job deadline misconfigured, dispatcher waiting forever instead of timing out.
5. **Full-stack match.** Upload two bots via `POST /submit` against the real `web`, wait for the match to play through, assert the leaderboard updates. Catches: any breakage in the seam between web/orchestrator/dispatcher.

### Where the tests live

```
tests/acceptance/
├── conftest.py            # fixtures for the live broker connection + HTTP client
├── test_plumbing.py
├── test_runtimes.py       # parametrized over RUNTIMES.keys()
├── test_sandbox.py
├── test_timeout.py
└── test_full_match.py
```

Separate directory from the existing `tests/` because:

- Different fixtures (real `aiormq` connection, real `httpx` client against `http://localhost:8000`) — no DB engine, no `mock_queue`.
- Different pytest config — no coverage instrumentation, longer timeouts, opt-in via marker.
- Different runtime requirements — needs the kind cluster + dispatcher + compose stack all live; the existing `tests/` only needs Postgres.

`pyproject.toml` gets a new poe task:

```toml
[tool.poe.tasks.acceptance]
cmd = "pytest tests/acceptance/ --no-cov -m acceptance"
```

`poe check` does **not** invoke it — it stays fast and unit-only. Acceptance runs on demand locally and in CI nightly (or pre-release).

### How a contributor runs it locally

```bash
nix develop
docker compose up -d --wait              # web + orchestrator + db + rabbit
kind create cluster --config k8s/kind-cluster.yaml
make load-runtime-images                 # build + `kind load` every image in RUNTIMES
kubectl apply -f k8s/
kubectl rollout status -n bots deploy/dispatcher
uv run poe acceptance
```

(A `poe acceptance-up` task can wrap the bring-up so it's one command.)

### Stack lifecycle: shared vs. fresh

Bringing up a kind cluster + KEDA + dispatcher takes 30–60s. Doing that per-test would dominate runtime. The acceptance suite assumes the stack is **already up** and tests clean up after themselves (each test publishes with a unique correlation_id, asserts its own reply, and ignores everything else on the reply queue). CI brings the stack up once per workflow run, runs the suite, tears down.

If a test leaves the cluster in a bad state, the next test's failure is the symptom, not the cause — so each test should be defensive about timeouts and assert specifically on its own correlation_id rather than "any message on the queue."

### CI

GitHub Actions has `engineerd/setup-kind` (or the newer `helm/kind-action`) that brings up a kind cluster in a runner. The CI workflow looks like: `nix develop` → start compose → create kind cluster → load images → apply manifests → `poe acceptance`. Run nightly and on PRs that touch `k8s/`, `dispatcher/`, `runner/`, `messaging/`, or `web/main.py` (i.e., anything that could affect the live-stack contract).

## Migration order (rough)

1. Land the `language:` frontmatter + `RUNTIMES` allowlist + web validation, mapping every existing `python: X` value to the equivalent `python-X` entry. No behavior change yet.
2. Switch the orchestrator to publish on `turn.requests` instead of `turn.pyXY.requests`. Add a temporary compose `worker` that consumes from `turn.requests` and runs Python only — keep the lights on while k8s pieces land.
3. Build the per-runtime images and load them into a local kind cluster. Verify a single Python bot runs end-to-end via the dispatcher.
4. **Stand up the acceptance harness** with at least the plumbing test + one per-runtime smoke test. This locks in "k8s side is provably working" before any further runtimes are added. New runtimes get a parametrized case here, and `poe acceptance` is the gate for considering them shipped.
5. Add more runtimes to the allowlist (3.10–3.14, plus a non-Python one as a proof-point) — each one adds a passing case to the per-runtime test.
6. Delete the compose `worker-*` services + the temporary `worker` from step 2.
7. Repeat the deployment story against AKS once happy locally. The acceptance suite runs against AKS too (point it at the AKS ingress / Service Bus / managed DB endpoints) — same suite, different target, same pass/fail definition of "the stack works."
