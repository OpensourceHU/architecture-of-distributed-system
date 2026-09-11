"""Integration tests for the dispatcher's worker registry and WebSocket
job-dispatch wiring: a worker connects to the real /ws/worker route,
registers, receives a count_request, and answers it -- exercising the
actual wire protocol a worker speaks, not just the WorkerPool class in
isolation.
"""

import asyncio

import main
from main import WorkerPool, make_app


async def _register(client, worker_id: str):
    ws = await client.ws_connect("/ws/worker")
    await ws.send_json({"type": "register", "worker_id": worker_id})
    await asyncio.sleep(0.05)  # let the server process the register message
    return ws


async def test_dispatch_round_trip_through_real_ws_route(aiohttp_client):
    pool = WorkerPool()
    client = await aiohttp_client(make_app(pool))
    worker_ws = await _register(client, "worker-1")

    dispatch_task = asyncio.create_task(pool.dispatch("http://example.com/text.txt", "dog"))

    request = await worker_ws.receive_json()
    assert request["type"] == "count_request"
    assert request["file_url"] == "http://example.com/text.txt"
    assert request["keyword"] == "dog"

    await worker_ws.send_json(
        {"type": "count_response", "request_id": request["request_id"], "count": 3, "cached": False}
    )

    result = await dispatch_task
    assert result.count == 3
    assert result.worker_id == "worker-1"
    assert result.cached is False

    await worker_ws.close()


async def test_dispatch_retries_on_worker_timeout(aiohttp_client, monkeypatch):
    monkeypatch.setattr(main, "REQUEST_TIMEOUT", 0.05)
    pool = WorkerPool()
    client = await aiohttp_client(make_app(pool))
    silent_ws = await _register(client, "worker-1")
    responsive_ws = await _register(client, "worker-2")

    dispatch_task = asyncio.create_task(pool.dispatch("http://example.com/text.txt", "dog"))

    # worker-1 (picked first, registered first) never answers -> the request
    # should be retried on worker-2 once worker-1 times out.
    request = await responsive_ws.receive_json()
    await responsive_ws.send_json(
        {"type": "count_response", "request_id": request["request_id"], "count": 7, "cached": True}
    )

    result = await dispatch_task
    assert result.worker_id == "worker-2"
    assert result.count == 7

    # the silent worker should have been dropped from the pool after timing out
    assert "worker-1" not in pool._workers

    await silent_ws.close()
    await responsive_ws.close()


async def test_worker_disconnect_removes_it_from_pool(aiohttp_client):
    pool = WorkerPool()
    client = await aiohttp_client(make_app(pool))
    worker_ws = await _register(client, "worker-1")
    assert "worker-1" in pool._workers

    await worker_ws.close()
    await asyncio.sleep(0.05)

    assert "worker-1" not in pool._workers
