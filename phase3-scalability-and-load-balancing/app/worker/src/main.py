import asyncio
import hashlib
import json
import logging
import multiprocessing
import os
import re
import socket
import urllib.request

import redis
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

WORKER_ID = os.environ.get("WORKER_ID", socket.gethostname())
DISPATCHER_HOST = os.environ.get("DISPATCHER_HOST", "dispatcher")
DISPATCHER_PORT = int(os.environ.get("DISPATCHER_PORT", "8080"))
DISPATCHER_WS_PATH = "/ws/worker"
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


def count_keyword(file_url: str, keyword: str) -> tuple[int, bool]:
    """Resolve a (file, keyword) request, serving from cache when possible.

    Returns (count, cached). This is the synchronous, blocking core of a
    job; callers running inside the asyncio event loop must offload it to
    an executor so a slow download/count doesn't stall the WebSocket
    connection's heartbeat.
    """
    cached = get_cached_count(file_url, keyword)
    if cached is not None:
        logger.info("Cache hit: keyword=%r file=%r -> %d", keyword, file_url, cached)
        record_keyword_request(keyword)
        return cached, True

    logger.info("Cache miss: keyword=%r file=%r, downloading...", keyword, file_url)
    text = download_text(file_url)
    count = count_keyword_parallel(text, keyword, NUM_PROCESSES)

    set_cached_count(file_url, keyword, count)
    record_keyword_request(keyword)
    logger.info("Counted: keyword=%r file=%r -> %d", keyword, file_url, count)
    return count, False


# --- Dispatcher connection (WebSocket) ------------------------------------


async def handle_count_request(ws: websockets.ClientConnection, message: dict) -> None:
    request_id = message.get("request_id")
    file_url = str(message.get("file_url"))
    keyword = str(message.get("keyword"))

    try:
        loop = asyncio.get_running_loop()
        count, cached = await loop.run_in_executor(None, count_keyword, file_url, keyword)
    except Exception as exc:  # noqa: BLE001 - reported back to the dispatcher, not swallowed
        logger.exception("Failed to process request %s", request_id)
        await ws.send(
            json.dumps({"type": "count_error", "request_id": request_id, "message": str(exc)})
        )
        return

    await ws.send(
        json.dumps(
            {"type": "count_response", "request_id": request_id, "count": count, "cached": cached}
        )
    )


async def handle_message(ws: websockets.ClientConnection, raw_message: str) -> None:
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed message from dispatcher: %r", raw_message)
        return

    if message.get("type") == "count_request":
        await handle_count_request(ws, message)


async def run_worker() -> None:
    uri = f"ws://{DISPATCHER_HOST}:{DISPATCHER_PORT}{DISPATCHER_WS_PATH}"
    async for ws in websockets.connect(uri):
        try:
            await ws.send(json.dumps({"type": "register", "worker_id": WORKER_ID}))
            logger.info("Connected to dispatcher and registered as '%s'", WORKER_ID)
            async for raw_message in ws:
                asyncio.create_task(handle_message(ws, raw_message))
        except websockets.ConnectionClosed:
            logger.warning("Connection to dispatcher lost, reconnecting...")
            continue


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
