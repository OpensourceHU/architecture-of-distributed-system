"""Tests for the POST /v1/wordcount client-facing endpoint.

These exercise the real aiohttp application (request parsing, validation,
status codes) via aiohttp's test client, against a fake WorkerPool so no
real worker or network is involved.
"""

from main import (
    NoWorkerAvailable,
    WorkerPool,
    WorkerProcessingError,
    WorkerTimeout,
    WordCountResult,
    make_app,
)

FILE_URL = "https://www.gutenberg.org/files/1342/1342-0.txt"


class FakeWorkerPool(WorkerPool):
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    async def dispatch(self, file_url: str, keyword: str) -> WordCountResult:
        if self._error is not None:
            raise self._error
        return self._result


async def test_success_returns_count(aiohttp_client):
    pool = FakeWorkerPool(result=WordCountResult(count=42, worker_id="worker-1", cached=False))
    client = await aiohttp_client(make_app(pool))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL, "keyword": "pride"})

    assert resp.status == 200
    assert await resp.json() == {
        "keyword": "pride",
        "file_url": FILE_URL,
        "count": 42,
        "worker_id": "worker-1",
        "cached": False,
    }


async def test_missing_file_url_is_bad_request(aiohttp_client):
    client = await aiohttp_client(make_app(FakeWorkerPool()))

    resp = await client.post("/v1/wordcount", json={"keyword": "pride"})

    assert resp.status == 400
    body = await resp.json()
    assert body["error"] == "bad_request"


async def test_missing_keyword_is_bad_request(aiohttp_client):
    client = await aiohttp_client(make_app(FakeWorkerPool()))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL})

    assert resp.status == 400
    body = await resp.json()
    assert body["error"] == "bad_request"


async def test_empty_keyword_is_bad_request(aiohttp_client):
    client = await aiohttp_client(make_app(FakeWorkerPool()))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL, "keyword": ""})

    assert resp.status == 400


async def test_invalid_json_body_is_bad_request(aiohttp_client):
    client = await aiohttp_client(make_app(FakeWorkerPool()))

    resp = await client.post(
        "/v1/wordcount", data="not json", headers={"Content-Type": "application/json"}
    )

    assert resp.status == 400


async def test_no_worker_available_returns_503(aiohttp_client):
    pool = FakeWorkerPool(error=NoWorkerAvailable())
    client = await aiohttp_client(make_app(pool))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL, "keyword": "pride"})

    assert resp.status == 503
    body = await resp.json()
    assert body["error"] == "no_worker_available"


async def test_worker_timeout_returns_504(aiohttp_client):
    pool = FakeWorkerPool(error=WorkerTimeout())
    client = await aiohttp_client(make_app(pool))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL, "keyword": "pride"})

    assert resp.status == 504
    body = await resp.json()
    assert body["error"] == "worker_timeout"


async def test_worker_processing_error_returns_502(aiohttp_client):
    pool = FakeWorkerPool(error=WorkerProcessingError("file could not be downloaded"))
    client = await aiohttp_client(make_app(pool))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL, "keyword": "pride"})

    assert resp.status == 502
    body = await resp.json()
    assert body["error"] == "worker_error"


async def test_default_worker_pool_has_no_workers(aiohttp_client):
    client = await aiohttp_client(make_app(WorkerPool()))

    resp = await client.post("/v1/wordcount", json={"file_url": FILE_URL, "keyword": "pride"})

    assert resp.status == 503
