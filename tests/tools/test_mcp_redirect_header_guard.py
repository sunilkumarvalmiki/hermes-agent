"""Regression tests for MCP redirect credential containment."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


def _secret_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer mcp-secret",
        "X-Api-Key": "api-secret",
        "X-Session-Token": "session-secret",
    }


async def _capture_streamable_http_client_kwargs(config: dict) -> dict:
    from tools.mcp_tool import MCPServerTask

    server = MCPServerTask("remote")
    captured: dict = {}

    class DummyAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyTransportCtx:
        async def __aenter__(self):
            return MagicMock(), MagicMock(), (lambda: None)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            return None

    async def _discover_tools(self):
        self._shutdown_event.set()

    with patch("tools.mcp_tool._MCP_HTTP_AVAILABLE", True), \
         patch("tools.mcp_tool._MCP_NEW_HTTP", True), \
         patch("httpx.AsyncClient", DummyAsyncClient), \
         patch("tools.mcp_tool.streamable_http_client", return_value=DummyTransportCtx()), \
         patch("tools.mcp_tool.ClientSession", DummySession), \
         patch.object(MCPServerTask, "_discover_tools", _discover_tools):
        await server._run_http(config)

    return captured


def test_streamable_http_does_not_follow_redirect_to_attacker_origin():
    captured = asyncio.run(
        _capture_streamable_http_client_kwargs(
            {
                "url": "https://mcp.example.com/mcp",
                "headers": _secret_headers(),
            }
        )
    )
    seen_origin_headers: dict[str, str] = {}
    seen_attacker_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mcp.example.com":
            seen_origin_headers.update(dict(request.headers))
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.example.net/collect"},
                request=request,
            )
        seen_attacker_headers.update(dict(request.headers))
        return httpx.Response(200, request=request)

    async def drive() -> httpx.Response:
        client_kwargs = dict(captured)
        client_kwargs["transport"] = httpx.MockTransport(handler)
        async with httpx.AsyncClient(**client_kwargs) as client:
            return await client.get("https://mcp.example.com/mcp")

    response = asyncio.run(drive())

    assert captured["follow_redirects"] is False
    assert response.status_code == 302
    assert seen_origin_headers.get("authorization") == "Bearer mcp-secret"
    assert seen_origin_headers.get("x-api-key") == "api-secret"
    assert seen_origin_headers.get("x-session-token") == "session-secret"
    assert seen_attacker_headers == {}


async def _capture_legacy_streamable_http_kwargs(config: dict) -> dict:
    from tools.mcp_tool import MCPServerTask

    server = MCPServerTask("legacy-remote")
    captured: dict = {}

    class DummyLegacyTransportCtx:
        async def __aenter__(self):
            return MagicMock(), MagicMock(), (lambda: None)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            return None

    def fake_streamablehttp_client(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return DummyLegacyTransportCtx()

    async def _discover_tools(self):
        self._shutdown_event.set()

    with patch("tools.mcp_tool._MCP_HTTP_AVAILABLE", True), \
         patch("tools.mcp_tool._MCP_NEW_HTTP", False), \
         patch("tools.mcp_tool.streamablehttp_client", fake_streamablehttp_client), \
         patch("tools.mcp_tool.ClientSession", DummySession), \
         patch.object(MCPServerTask, "_discover_tools", _discover_tools):
        await server._run_http(config)

    return captured


def test_legacy_streamable_http_installs_non_redirecting_httpx_factory():
    captured = asyncio.run(
        _capture_legacy_streamable_http_kwargs(
            {
                "url": "https://mcp.example.com/mcp",
                "headers": _secret_headers(),
            }
        )
    )
    client_kwargs: dict = {}

    class DummyAsyncClient:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)

    with patch("httpx.AsyncClient", DummyAsyncClient):
        captured["httpx_client_factory"](
            headers=captured["headers"],
            timeout=None,
            auth=None,
        )

    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["headers"] == captured["headers"]


async def _capture_sse_kwargs(config: dict) -> dict:
    from tools.mcp_tool import MCPServerTask

    server = MCPServerTask("remote-sse")
    captured: dict = {}

    class DummySseCtx:
        async def __aenter__(self):
            return AsyncMock(), AsyncMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummySession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            return None

    def fake_sse_client(**kwargs):
        captured.update(kwargs)
        return DummySseCtx()

    async def _discover_tools(self):
        self._shutdown_event.set()

    with patch("tools.mcp_tool._MCP_HTTP_AVAILABLE", True), \
         patch("tools.mcp_tool.sse_client", fake_sse_client), \
         patch("tools.mcp_tool.ClientSession", DummySession), \
         patch.object(MCPServerTask, "_discover_tools", _discover_tools):
        await server._run_http(config)

    return captured


def test_sse_transport_installs_non_redirecting_httpx_factory():
    captured = asyncio.run(
        _capture_sse_kwargs(
            {
                "url": "https://mcp.example.com/sse",
                "transport": "sse",
                "headers": _secret_headers(),
            }
        )
    )
    client_kwargs: dict = {}

    class DummyAsyncClient:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)

    with patch("httpx.AsyncClient", DummyAsyncClient):
        captured["httpx_client_factory"](
            headers=captured["headers"],
            timeout=None,
            auth=None,
        )

    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["headers"] == captured["headers"]
