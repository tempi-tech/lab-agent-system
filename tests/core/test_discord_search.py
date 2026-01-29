import asyncio
import httpx

from src.core.discord_search import build_search_params, search_messages_discord


def _get_param_values(params, key):
    return [value for k, value in params if k == key]


def test_build_search_params_clamps_limit():
    params = build_search_params(query="hello", channel_ids=None, author_ids=None, limit=100)
    assert ("limit", "25") in params

    params = build_search_params(query="hello", channel_ids=None, author_ids=None, limit=0)
    assert ("limit", "1") in params


def test_build_search_params_includes_filters():
    params = build_search_params(query="q", channel_ids=[1, 2], author_ids=[9], limit=5)
    assert ("content", "q") in params
    assert _get_param_values(params, "channel_id") == ["1", "2"]
    assert _get_param_values(params, "author_id") == ["9"]
    assert ("limit", "5") in params


def test_search_messages_discord_parses_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "messages": [
                [
                    {
                        "id": "1",
                        "channel_id": "2",
                        "content": "hello",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "author": {"id": "3", "username": "user"},
                        "type": 0,
                    }
                ]
            ]
        }
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    results = asyncio.run(
        search_messages_discord(
            guild_id=10,
            query="hello",
            bot_token="token",
            channel_ids=None,
            author_ids=None,
            limit=5,
            resolve_channel_name=lambda _id: "general",
            transport=transport,
        )
    )
    assert len(results) == 1
    assert results[0].channel_name == "general"
    assert results[0].author_name == "user"


def test_search_messages_discord_returns_none_on_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("boom", request=request)

    transport = httpx.MockTransport(handler)
    results = asyncio.run(
        search_messages_discord(
            guild_id=10,
            query="hello",
            bot_token="token",
            channel_ids=None,
            author_ids=None,
            limit=5,
            resolve_channel_name=lambda _id: "general",
            transport=transport,
        )
    )
    assert results is None


def test_search_messages_discord_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "server error"})

    transport = httpx.MockTransport(handler)
    results = asyncio.run(
        search_messages_discord(
            guild_id=10,
            query="hello",
            bot_token="token",
            channel_ids=None,
            author_ids=None,
            limit=5,
            resolve_channel_name=lambda _id: "general",
            transport=transport,
        )
    )
    assert results is None
