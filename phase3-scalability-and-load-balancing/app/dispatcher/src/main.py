import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dispatcher")

HTTP_PORT = 8080
# aiohttp pings every HEARTBEAT_INTERVAL seconds and closes the connection if
# no pong arrives within HEARTBEAT_INTERVAL / 2 -- that's how a dead worker
# is detected without any application-level heartbeat message.
HEARTBEAT_INTERVAL = 10.0  # seconds
REQUEST_TIMEOUT = 30.0  # seconds to wait for a worker's response to a job
MAX_ATTEMPTS = 3  # distinct workers to try before giving up on a request


class NoWorkerAvailable(Exception):
    """Raised when no worker is currently online to serve a request."""


class WorkerTimeout(Exception):
    """Raised when every worker attempted for a request failed to respond in time."""


class WorkerProcessingError(Exception):
    """Raised when a worker reported an error processing the request itself
    (e.g. the file could not be downloaded) -- retrying another worker would
    not help, since the input is what's wrong."""


@dataclass
class WordCountResult:
    count: int
    worker_id: str
    cached: bool


@dataclass
class WorkerConnection:
    worker_id: str
    ws: web.WebSocketResponse
    active_requests: int = 0
    pending: dict = field(default_factory=dict)


class WorkerPool:
    """Tracks connected workers and dispatches word-count jobs to them.

    Load-balances with Least Connections: each request goes to the
    currently-registered worker with the fewest requests in flight. If the
    chosen worker's connection drops or it doesn't answer within
    REQUEST_TIMEOUT, it is dropped from the pool and the request is retried
    on another worker (up to MAX_ATTEMPTS distinct workers).
    """

    def __init__(self) -> None:
        self._workers: dict[str, WorkerConnection] = {}

    def register(self, worker_id: str, ws: web.WebSocketResponse) -> WorkerConnection:
        conn = WorkerConnection(worker_id=worker_id, ws=ws)
        self._workers[worker_id] = conn
        logger.info("Worker '%s' registered (%d online)", worker_id, len(self._workers))
        return conn

    def unregister(self, worker_id: str) -> None:
        if self._workers.pop(worker_id, None) is not None:
            logger.warning("Worker '%s' disconnected (%d online)", worker_id, len(self._workers))

    def resolve(self, message: dict) -> None:
        request_id = message.get("request_id")
        for conn in self._workers.values():
            future = conn.pending.get(request_id)
            if future is not None and not future.done():
                future.set_result(message)
                return

    def _pick_worker(self, exclude: set[str]) -> WorkerConnection | None:
        candidates = [conn for wid, conn in self._workers.items() if wid not in exclude]
        if not candidates:
            return None
        return min(candidates, key=lambda conn: conn.active_requests)

    async def dispatch(self, file_url: str, keyword: str) -> WordCountResult:
        tried: set[str] = set()
        last_error: Exception = NoWorkerAvailable()
        for _ in range(MAX_ATTEMPTS):
            conn = self._pick_worker(exclude=tried)
            if conn is None:
                break
            tried.add(conn.worker_id)
            try:
                return await self._dispatch_to(conn, file_url, keyword)
            except WorkerTimeout as exc:
                last_error = exc
                continue
        raise last_error

    async def _dispatch_to(
        self, conn: WorkerConnection, file_url: str, keyword: str
    ) -> WordCountResult:
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        conn.pending[request_id] = future
        conn.active_requests += 1
        try:
            try:
                await conn.ws.send_json(
                    {
                        "type": "count_request",
                        "request_id": request_id,
                        "file_url": file_url,
                        "keyword": keyword,
                    }
                )
            except (ConnectionResetError, RuntimeError) as exc:
                self.unregister(conn.worker_id)
                raise WorkerTimeout(str(exc)) from exc

            try:
                message = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
            except asyncio.TimeoutError:
                self.unregister(conn.worker_id)
                raise WorkerTimeout(f"Worker '{conn.worker_id}' did not respond in time") from None
        finally:
            conn.pending.pop(request_id, None)
            conn.active_requests -= 1

        if message.get("type") == "count_error":
            raise WorkerProcessingError(message.get("message", "worker failed to process the request"))
        return WordCountResult(
            count=message["count"], worker_id=conn.worker_id, cached=bool(message.get("cached", False))
        )


def _error(status: int, error: str, message: str) -> web.Response:
    return web.json_response({"error": error, "message": message}, status=status)


async def handle_wordcount(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "bad_request", "Request body must be valid JSON")

    if not isinstance(body, dict):
        return _error(400, "bad_request", "Request body must be a JSON object")

    file_url = body.get("file_url")
    keyword = body.get("keyword")
    if not isinstance(file_url, str) or not file_url:
        return _error(400, "bad_request", "'file_url' is required and must be a non-empty string")
    if not isinstance(keyword, str) or not keyword:
        return _error(400, "bad_request", "'keyword' is required and must be a non-empty string")

    worker_pool: WorkerPool = request.app["worker_pool"]
    try:
        result = await worker_pool.dispatch(file_url, keyword)
    except NoWorkerAvailable:
        return _error(503, "no_worker_available", "No worker is currently online")
    except WorkerTimeout:
        return _error(504, "worker_timeout", "All attempted workers timed out")
    except WorkerProcessingError as exc:
        return _error(502, "worker_error", str(exc))

    return web.json_response(
        {
            "keyword": keyword,
            "file_url": file_url,
            "count": result.count,
            "worker_id": result.worker_id,
            "cached": result.cached,
        }
    )


async def handle_worker_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
    await ws.prepare(request)

    worker_pool: WorkerPool = request.app["worker_pool"]
    worker_id: str | None = None

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed message from worker: %r", msg.data)
            continue

        msg_type = data.get("type")
        if msg_type == "register":
            worker_id = str(data.get("worker_id"))
            worker_pool.register(worker_id, ws)
        elif msg_type in ("count_response", "count_error"):
            worker_pool.resolve(data)

    if worker_id is not None:
        worker_pool.unregister(worker_id)
    return ws


def make_app(worker_pool: WorkerPool) -> web.Application:
    app = web.Application()
    app["worker_pool"] = worker_pool
    app.router.add_post("/v1/wordcount", handle_wordcount)
    app.router.add_get("/ws/worker", handle_worker_ws)
    return app


def main() -> None:
    app = make_app(WorkerPool())
    logger.info("Dispatcher listening on port %d (HTTP + /ws/worker)", HTTP_PORT)
    web.run_app(app, port=HTTP_PORT)


if __name__ == "__main__":
    main()
