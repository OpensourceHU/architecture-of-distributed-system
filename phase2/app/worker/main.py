import logging
import os
import socket
import threading
import time

import rpyc
from rpyc.utils.server import ThreadedServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

WORKER_ID = os.environ.get("WORKER_ID", socket.gethostname())
DISPATCHER_HOST = os.environ.get("DISPATCHER_HOST", "dispatcher")
DISPATCHER_PORT = int(os.environ.get("DISPATCHER_PORT", "18861"))
RPC_PORT = 18862
HEARTBEAT_INTERVAL = 2.0  # seconds


class WorkerService(rpyc.Service):
    def exposed_ping(self) -> str:
        return "pong"


def heartbeat_loop() -> None:
    while True:
        try:
            conn = rpyc.connect(DISPATCHER_HOST, DISPATCHER_PORT)
            try:
                conn.root.heartbeat(WORKER_ID)
                logger.info("Heartbeat sent to dispatcher")
            finally:
                conn.close()
        except Exception:
            logger.exception("Failed to send heartbeat to dispatcher")
        time.sleep(HEARTBEAT_INTERVAL)


def main() -> None:
    threading.Thread(target=heartbeat_loop, daemon=True, name="heartbeat").start()
    logger.info("Worker '%s' listening on port %d", WORKER_ID, RPC_PORT)
    server = ThreadedServer(
        WorkerService,
        port=RPC_PORT,
        protocol_config={"allow_public_attrs": True},
    )
    server.start()


if __name__ == "__main__":
    main()
