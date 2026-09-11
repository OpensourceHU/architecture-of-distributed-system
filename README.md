# architecture-of-distributed-system

Word Count Service — lab assignment for Architecture of Distributed Systems.

## Tech Stack

- **Language**: Python
- **Package management**: [uv](https://docs.astral.sh/uv/)
- **RPC framework**: [RPyC](https://rpyc.readthedocs.io/) — used for client-server (and load balancer-server) communication
- **Containerization**: Docker, orchestrated via Docker Compose — the cluster is built and started with `docker compose up` on every run
- **Cache**: Redis, run as its own container

## Running Locally

Each service (e.g. `phase2-implementation-of-the-service/app/dispatcher`,
`phase2-implementation-of-the-service/app/worker`) is its own `uv` project.
To work on one outside of Docker Compose:

```bash
cd phase2-implementation-of-the-service/app/dispatcher   # or app/worker
uv sync
```

`uv sync` creates a `.venv/` in that service's directory and installs its
dependencies from `pyproject.toml` / `uv.lock`.

### Activating the virtual environment

`uv run` activates the venv for you per-command, so it's usually not
necessary to activate it manually:

```bash
uv run python main.py         # dispatcher
uv run python src/main.py     # worker (uses a src/ layout, see Testing below)
```

If you want an activated shell instead (to use `python`, `pip`, etc.
directly), do it the normal way:

```bash
source .venv/bin/activate
# ...
deactivate
```

### Debugging

The dispatcher runs an RPyC `ThreadedServer`: the main thread blocks in
`server.start()`, and each incoming client connection is handled on its own
worker thread. Keep that in mind before debugging:

- **Plain `pdb`/`breakpoint()` works**, but a breakpoint inside an
  `exposed_*` method pauses the *connection's* thread, not your terminal's
  main thread. Run the service directly (not through a process manager that
  captures stdin) so the pdb prompt is usable, then trigger the RPC call
  from a second terminal to hit it.
- **Preferred: attach a real debugger** (e.g. `debugpy`) instead of relying
  on stdin:

  ```bash
  uv add --dev debugpy
  uv run python -m debugpy --listen 5678 --wait-for-client main.py
  ```

  Then attach from VS Code with a `launch.json` entry:

  ```json
  {
    "name": "Attach to dispatcher",
    "type": "debugpy",
    "request": "attach",
    "connect": { "host": "localhost", "port": 5678 }
  }
  ```

  Set breakpoints, then trigger a call via an `rpyc.connect(...)` client to
  hit them.
- A background thread (e.g. the dispatcher's heartbeat-timeout monitor loop)
  pausing at a breakpoint while the RPC server keeps accepting connections
  on the main thread is expected, not a hang.

## Testing

Services that communicate over RPyC are tested in three layers, trading off
speed against fidelity to the real deployment:

1. **Unit tests** — pure functions only (e.g. chunk splitting, keyword
   counting), no sockets, no Redis. Fastest, run constantly.
2. **Service-level integration tests** — start the real RPyC
   `ThreadedServer` in-process (a background thread inside the test) on an
   ephemeral port, backed by an in-memory fake Redis (`fakeredis`), and talk
   to it with a real `rpyc.connect(...)` client. This exercises the actual
   RPC wire protocol and cache read/write logic without paying the cost of
   building/starting Docker containers, so it's still fast enough to run on
   every local test invocation. See
   `phase2-implementation-of-the-service/app/worker/tests/test_service_integration.py`.
3. **Docker/compose end-to-end tests** — the full cluster (worker, real
   Redis, dispatcher) built and started via `docker compose`, exercised over
   the network exactly as it runs in production. Slowest and closest to
   reality; not yet implemented for this project, intended to run in CI or
   before a submission/demo rather than on every local change.

Run a service's tests with `uv`, from that service's directory:

```bash
cd phase2-implementation-of-the-service/app/worker
uv sync --group dev   # installs pytest + fakeredis alongside the app deps
uv run pytest
```

## Phase 3: Running the Cluster (Docker Compose)

Phase 3 replaces the dispatcher↔worker RPyC link with a plain WebSocket
(workers connect out to the dispatcher's `/ws/worker` route), so the whole
cluster — dispatcher, three workers, and Redis — comes up with Compose
alone:

```bash
cd phase3-scalability-and-load-balancing
docker compose up --build -d
```

This starts:

- `dispatcher` — REST API on `localhost:8080` (`POST /v1/wordcount`), plus
  the `/ws/worker` WebSocket route workers register on
- `worker-1`, `worker-2`, `worker-3` — named services (not Compose
  `replicas`, so container names stay stable) that connect out to the
  dispatcher and register themselves
- `redis` — result cache, on `localhost:6379`

Confirm all three workers registered:

```bash
docker compose logs dispatcher | grep registered
```

Tear the cluster down with:

```bash
docker compose down
```

## Phase 3: End-to-End Testing

With the cluster running, exercise the real HTTP + WebSocket path with a
plain `curl` request against the dispatcher:

```bash
curl -s -X POST http://localhost:8080/v1/wordcount \
  -H "Content-Type: application/json" \
  -d '{"file_url": "https://www.gutenberg.org/cache/epub/79552/pg79552.txt", "keyword": "the"}'
```

Scenarios worth checking manually:

- **Success path** — response is `200` with a `count`, and `worker_id`
  names one of the three running workers.
- **Caching** — send the same request again; `"cached"` should flip from
  `false` to `true`.
- **Failover** — stop one worker (`docker compose stop worker-1`) and send
  another request; it should still succeed, served by a remaining worker.
  Restart it with `docker compose start worker-1`.
- **No workers online** — `docker compose stop worker-1 worker-2
  worker-3`, then the same request should return `503 no_worker_available`.
- **Bad input** — omitting `file_url` or `keyword` returns `400
  bad_request`.

This is currently a manual checklist rather than an automated suite;
promoting it to a scripted `pytest` e2e test (driving `docker compose`
itself and asserting on these same scenarios over the real network) is a
natural next step.
