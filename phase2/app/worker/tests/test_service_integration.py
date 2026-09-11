"""Service-level integration tests for WorkerService.

These start the real RPyC ThreadedServer in-process (real TCP socket, real
wire protocol) backed by an in-memory fake Redis, so they exercise the
actual RPC + caching path without needing Docker or a real Redis instance.
The file download itself is mocked, since fetching over the network isn't
what these tests are meant to verify.
"""

import threading
import time

import fakeredis
import pytest
import rpyc
from rpyc.utils.server import ThreadedServer

import main


@pytest.fixture()
def worker_server(monkeypatch):
    fake_redis = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(main, "redis_client", fake_redis)

    server = ThreadedServer(
        main.WorkerService,
        port=0,
        protocol_config={"allow_public_attrs": True},
    )
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    time.sleep(0.05)  # let the accept loop come up

    try:
        yield server, fake_redis
    finally:
        server.close()


def test_cache_miss_downloads_counts_and_caches(worker_server, monkeypatch):
    server, fake_redis = worker_server
    monkeypatch.setattr(main, "download_text", lambda url: "dog cat dog bird dog")

    conn = rpyc.connect("localhost", server.port)
    try:
        count = conn.root.count_keyword("http://example.com/text.txt", "dog")
    finally:
        conn.close()

    assert count == 3
    assert fake_redis.get(main.cache_key("http://example.com/text.txt", "dog")) == "3"
    assert fake_redis.zscore(main.HOT_KEYWORDS_KEY, "dog") == 1.0


def test_cache_hit_skips_download(worker_server, monkeypatch):
    server, fake_redis = worker_server
    file_url = "http://example.com/text.txt"
    fake_redis.set(main.cache_key(file_url, "dog"), 42)

    download_calls = []
    monkeypatch.setattr(
        main, "download_text", lambda url: download_calls.append(url) or "irrelevant"
    )

    conn = rpyc.connect("localhost", server.port)
    try:
        count = conn.root.count_keyword(file_url, "dog")
    finally:
        conn.close()

    assert count == 42
    assert download_calls == []
    assert fake_redis.zscore(main.HOT_KEYWORDS_KEY, "dog") == 1.0
