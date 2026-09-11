# architecture-of-distributed-system

Word Count Service — lab assignment for Architecture of Distributed Systems.

## Tech Stack

- **Language**: Python
- **Package management**: [uv](https://docs.astral.sh/uv/)
- **RPC framework**: [RPyC](https://rpyc.readthedocs.io/) — used for client-server (and load balancer-server) communication
- **Containerization**: Docker, orchestrated via Docker Compose — the cluster is built and started with `docker compose up` on every run
- **Cache**: Redis, run as its own container
