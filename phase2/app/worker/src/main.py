import hashlib
import logging
import multiprocessing
import os
import re
import socket
import threading
import time
import urllib.request

import redis
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
NUM_PROCESSES = int(os.environ.get("WORKER_PROCESSES", str(os.cpu_count() or 4)))
DOWNLOAD_TIMEOUT = 15  # seconds

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
HOT_KEYWORDS_KEY = "wordcount:hot_keywords"

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


# --- Cache -------------------------------------------------------------


def cache_key(file_url: str, keyword: str) -> str:
    digest = hashlib.sha256(f"{file_url}|{keyword}".encode("utf-8")).hexdigest()
    return f"wordcount:result:{digest}"


def get_cached_count(file_url: str, keyword: str) -> int | None:
    value = redis_client.get(cache_key(file_url, keyword))
    return int(value) if value is not None else None


def set_cached_count(file_url: str, keyword: str, count: int) -> None:
    redis_client.set(cache_key(file_url, keyword), count)


def record_keyword_request(keyword: str) -> None:
    """Increment the keyword's score in a sorted set, so hot keywords can be
    queried later via ZREVRANGE."""
    redis_client.zincrby(HOT_KEYWORDS_KEY, 1, keyword)


# --- Word count ----------------------------------------------------------


def download_text(url: str) -> str:
    """Download the referenced file's content over HTTP and decode it as text."""
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def split_into_chunks(text: str, num_chunks: int) -> list[str]:
    """Split text into up to num_chunks pieces, breaking only at whitespace
    so a word is never split across two chunks."""
    if num_chunks <= 1 or not text:
        return [text]

    approx_size = max(1, len(text) // num_chunks)
    chunks = []
    start = 0
    for _ in range(num_chunks - 1):
        end = start + approx_size
        if end >= len(text):
            break
        while end < len(text) and not text[end].isspace():
            end += 1
        chunks.append(text[start:end])
        start = end
    chunks.append(text[start:])
    return [chunk for chunk in chunks if chunk]


def count_in_chunk(args: tuple[str, str]) -> int:
    chunk, keyword = args
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return len(pattern.findall(chunk))


def count_keyword_parallel(text: str, keyword: str, num_processes: int) -> int:
    """Split text in memory and count keyword occurrences across chunks in parallel,
    then sum the partial results."""
    chunks = split_into_chunks(text, num_processes)
    if len(chunks) <= 1:
        return count_in_chunk((chunks[0] if chunks else "", keyword))

    with multiprocessing.Pool(processes=len(chunks)) as pool:
        partial_counts = pool.map(count_in_chunk, [(chunk, keyword) for chunk in chunks])
    return sum(partial_counts)


# --- RPyC service --------------------------------------------------------


class WorkerService(rpyc.Service):
    def exposed_ping(self) -> str:
        return "pong"

    def exposed_count_keyword(self, file_url: str, keyword: str) -> int:
        file_url = str(file_url)
        keyword = str(keyword)

        cached = get_cached_count(file_url, keyword)
        if cached is not None:
            logger.info("Cache hit: keyword=%r file=%r -> %d", keyword, file_url, cached)
            record_keyword_request(keyword)
            return cached

        logger.info("Cache miss: keyword=%r file=%r, downloading...", keyword, file_url)
        text = download_text(file_url)
        count = count_keyword_parallel(text, keyword, NUM_PROCESSES)

        set_cached_count(file_url, keyword, count)
        record_keyword_request(keyword)
        logger.info("Counted: keyword=%r file=%r -> %d", keyword, file_url, count)
        return count


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
