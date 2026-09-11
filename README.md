# architecture-of-distributed-system

Word Count Service — lab assignment for Architecture of Distributed Systems.

## Tech Stack

- **Language**: Python
- **Package management**: [uv](https://docs.astral.sh/uv/)
- **RPC framework**: [RPyC](https://rpyc.readthedocs.io/) — used for client-server (and load balancer-server) communication
- **Containerization**: Docker, orchestrated via Docker Compose — the cluster is built and started with `docker compose up` on every run
- **Cache**: Redis, run as its own container

## Running Locally

Each service (e.g. `phase2/app/dispatcher`, `phase2/app/worker`) is its own
`uv` project. To work on one outside of Docker Compose:

```bash
cd phase2/app/dispatcher   # or phase2/app/worker
uv sync
```

`uv sync` creates a `.venv/` in that service's directory and installs its
dependencies from `pyproject.toml` / `uv.lock`.

### Activating the virtual environment

`uv run` activates the venv for you per-command, so it's usually not
necessary to activate it manually:

```bash
uv run python main.py
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
