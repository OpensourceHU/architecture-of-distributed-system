import logging
import threading
import time

import rpyc
from rpyc.utils.server import ThreadedServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dispatcher")

RPC_PORT = 18861
HEARTBEAT_TIMEOUT = 5.0  # seconds since last heartbeat before a worker is OFFLINE
CHECK_INTERVAL = 2.0  # seconds between offline checks


class NodeRegistry:
    """Tracks which worker nodes are online based on received heartbeats."""

    def __init__(self, timeout: float) -> None:
        self._timeout = timeout
        self._last_seen: dict[str, float] = {}
        self._online: dict[str, bool] = {}
        self._lock = threading.Lock()

    def heartbeat(self, worker_id: str) -> None:
        with self._lock:
            was_online = self._online.get(worker_id, False)
            self._last_seen[worker_id] = time.monotonic()
            self._online[worker_id] = True
        if not was_online:
            logger.info("Worker '%s' is now ONLINE", worker_id)

    def check_timeouts(self) -> None:
        now = time.monotonic()
        newly_offline = []
        with self._lock:
            for worker_id, last_seen in self._last_seen.items():
                if self._online.get(worker_id, False) and now - last_seen > self._timeout:
                    self._online[worker_id] = False
                    newly_offline.append(worker_id)
        for worker_id in newly_offline:
            logger.warning("Worker '%s' missed heartbeat, marking OFFLINE", worker_id)

    def status(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._online)


registry = NodeRegistry(timeout=HEARTBEAT_TIMEOUT)


class DispatcherService(rpyc.Service):
    def exposed_heartbeat(self, worker_id: str) -> bool:
        registry.heartbeat(str(worker_id))
        return True

    def exposed_get_status(self) -> dict[str, bool]:
        return registry.status()


def monitor_loop() -> None:
    while True:
        time.sleep(CHECK_INTERVAL)
        registry.check_timeouts()


def main() -> None:
    threading.Thread(target=monitor_loop, daemon=True, name="monitor").start()
    logger.info("Dispatcher listening on port %d", RPC_PORT)
    server = ThreadedServer(
        DispatcherService,
        port=RPC_PORT,
        protocol_config={"allow_public_attrs": True},
    )
    server.start()


if __name__ == "__main__":
    main()
