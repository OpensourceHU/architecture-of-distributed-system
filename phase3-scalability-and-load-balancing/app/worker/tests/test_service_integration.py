"""Integration-level tests for the worker's dispatcher-facing logic.

count_keyword() is the synchronous core that the WebSocket message handler
offloads to an executor; these tests exercise the real caching path (backed
by an in-memory fake Redis) the same way the RPyC-era tests did, just
without the RPC transport. handle_message()/handle_count_request() are
covered separately against a fake WebSocket connection to verify the wire
messages sent back to the dispatcher.
"""

import json

import fakeredis
import pytest

import main


@pytest.fixture()
def fake_redis(monkeypatch):
    redis_instance = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(main, "redis_client", redis_instance)
    return redis_instance


def test_cache_miss_downloads_counts_and_caches(fake_redis, monkeypatch):
    monkeypatch.setattr(main, "download_text", lambda url: "dog cat dog bird dog")

    count, cached = main.count_keyword("http://example.com/text.txt", "dog")

    assert (count, cached) == (3, False)
    assert fake_redis.get(main.cache_key("http://example.com/text.txt", "dog")) == "3"
    assert fake_redis.zscore(main.HOT_KEYWORDS_KEY, "dog") == 1.0


def test_cache_hit_skips_download(fake_redis, monkeypatch):
    file_url = "http://example.com/text.txt"
    fake_redis.set(main.cache_key(file_url, "dog"), 42)

    download_calls = []
    monkeypatch.setattr(
        main, "download_text", lambda url: download_calls.append(url) or "irrelevant"
    )

    count, cached = main.count_keyword(file_url, "dog")

    assert (count, cached) == (42, True)
    assert download_calls == []
    assert fake_redis.zscore(main.HOT_KEYWORDS_KEY, "dog") == 1.0


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw_message: str) -> None:
        self.sent.append(json.loads(raw_message))


async def test_handle_message_sends_count_response(fake_redis, monkeypatch):
    monkeypatch.setattr(main, "download_text", lambda url: "dog cat dog")
    ws = FakeWebSocket()

    await main.handle_message(
        ws,
        json.dumps(
            {
                "type": "count_request",
                "request_id": "req-1",
                "file_url": "http://example.com/text.txt",
                "keyword": "dog",
            }
        ),
    )

    assert ws.sent == [
        {"type": "count_response", "request_id": "req-1", "count": 2, "cached": False}
    ]


async def test_handle_message_sends_count_error_on_failure(fake_redis, monkeypatch):
    def boom(url):
        raise RuntimeError("download failed")

    monkeypatch.setattr(main, "download_text", boom)
    ws = FakeWebSocket()

    await main.handle_message(
        ws,
        json.dumps(
            {
                "type": "count_request",
                "request_id": "req-2",
                "file_url": "http://example.com/text.txt",
                "keyword": "dog",
            }
        ),
    )

    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "count_error"
    assert ws.sent[0]["request_id"] == "req-2"


async def test_handle_message_ignores_unknown_type(fake_redis):
    ws = FakeWebSocket()

    await main.handle_message(ws, json.dumps({"type": "something_else"}))

    assert ws.sent == []


async def test_handle_message_ignores_malformed_json(fake_redis):
    ws = FakeWebSocket()

    await main.handle_message(ws, "not json")

    assert ws.sent == []
