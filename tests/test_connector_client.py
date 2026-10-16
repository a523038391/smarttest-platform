import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.api.connector_client import ConnectorClient, ConnectorConnectionError
from services.api.connector_domain import ConnectorAuthType, ConnectorKind, ConnectorRecord


class Response:
    def __init__(self, body=b"{}", status=200, headers=None) -> None:
        self.content, self.status_code = body, status
        self.headers = headers or {"Content-Type": "application/json"}

    def iter_content(self, chunk_size: int):
        yield self.content

    def close(self) -> None:
        pass


def record(**changes) -> ConnectorRecord:
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid4(), "name": "connector", "description": "",
        "kind": ConnectorKind.HTTP, "base_url": "https://connector.example.test/api",
        "auth_type": ConnectorAuthType.NONE, "auth_header": "Authorization",
        "enabled": True, "timeout_seconds": 5, "state_version": 0,
        "secret_revision": 0, "created_at": now, "updated_at": now,
    }
    values.update(changes)
    return ConnectorRecord(**values)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1", "http://169.254.169.254/latest", "ftp://example.test/file",
    "https://user:password@example.test", "http://[2002:c000:0204::1]",
    "http://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]",
])
def test_ssrf_and_unsafe_urls_are_rejected_before_request(url: str) -> None:
    calls = []
    with pytest.raises(ConnectorConnectionError, match="not allowed"):
        ConnectorClient(
            request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        ).test(record(base_url=url), None)
    assert calls == []


def test_redirects_and_header_injection_are_rejected_without_secret_disclosure() -> None:
    client = ConnectorClient(
        request_callable=lambda **_: Response(status=302),
        resolver=lambda _: ["93.184.216.34"],
    )
    with pytest.raises(ConnectorConnectionError, match="redirects"):
        client.test(record(), None)

    secret = "key\r\nInjected: yes"
    with pytest.raises(ConnectorConnectionError) as captured:
        client.test(record(
            auth_type=ConnectorAuthType.API_KEY, auth_header="X-API-Key",
        ), secret)
    assert secret not in str(captured.value)


def test_allowlisted_private_host_still_blocks_dangerous_ranges() -> None:
    allowed = ConnectorClient(
        request_callable=lambda **_: Response(), resolver=lambda _: ["192.168.4.10"],
        allowed_private_hosts=("connector.internal",),
    )
    assert allowed.test(record(base_url="http://connector.internal"), None)[0]
    for address in (
        "169.254.169.254", "2002:c000:0204::1",
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
    ):
        blocked = ConnectorClient(
            request_callable=lambda **_: Response(), resolver=lambda _: [address],
            allowed_private_hosts=("connector.internal",),
        )
        with pytest.raises(ConnectorConnectionError):
            blocked.test(record(base_url="http://connector.internal"), None)


def test_sse_multiline_data_is_concatenated_with_newlines() -> None:
    payload = ConnectorClient._parse_mcp_payload(
        b'data: {"jsonrpc":"2.0",\r\ndata: "id":7,"result":{"ok":true}}\r\n\r\n',
        "text/event-stream; charset=utf-8", expected_id=7,
    )

    assert payload == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_sse_selects_matching_json_rpc_from_multiple_events() -> None:
    payload = ConnectorClient._parse_mcp_payload(
        b': heartbeat\n\nevent: ping\n\n'
        b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        b'data: {"jsonrpc":"2.0","id":8,"result":{"ok":true}}\n\n',
        "text/event-stream", expected_id=8,
    )

    assert payload["id"] == 8


@pytest.mark.parametrize("protocol", ["2025-03-26", "2024-11-05"])
def test_mcp_streamable_http_handshake_and_paginated_tool_discovery(protocol: str) -> None:
    responses = [
        Response(
            b"data: " + json.dumps({
                "jsonrpc": "2.0", "id": 1, "result": {
                    "protocolVersion": protocol, "capabilities": {},
                },
            }).encode() + b"\n\n",
            headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "session-1"},
        ),
        Response(b"", status=202),
        Response(json.dumps({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": [{"name": "search", "description": "Search",
                                    "inputSchema": {"type": "object"}}],
                       "nextCursor": "page-2"},
        }).encode()),
        Response(json.dumps({
            "jsonrpc": "2.0", "id": 3,
            "result": {"tools": [{"name": "fetch", "inputSchema": {}}]},
        }).encode()),
    ]
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    message, _, tools = ConnectorClient(
        request_callable=request, resolver=lambda _: ["93.184.216.34"],
    ).test(record(kind=ConnectorKind.MCP), None)

    assert "discovery" in message
    assert [tool.name for tool in tools] == ["search", "fetch"]
    assert calls[1]["json"]["method"] == "notifications/initialized"
    assert calls[2]["headers"]["Mcp-Session-Id"] == "session-1"
    assert calls[2]["headers"]["MCP-Protocol-Version"] == protocol
    assert calls[3]["json"]["params"] == {"cursor": "page-2"}


def test_mcp_tool_discovery_has_aggregate_serialized_size_limit() -> None:
    page_number = 0

    def request(**kwargs):
        nonlocal page_number
        method = kwargs["json"]["method"]
        if method == "initialize":
            return Response(json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "result": {"protocolVersion": "2025-03-26", "capabilities": {}},
            }).encode())
        if method == "notifications/initialized":
            return Response(b"", status=202)
        page_number += 1
        tools = [{
            "name": f"tool-{page_number}-{index}",
            "description": "x" * 9_000,
            "inputSchema": {"type": "object"},
        } for index in range(100)]
        return Response(json.dumps({
            "jsonrpc": "2.0", "id": kwargs["json"]["id"],
            "result": {"tools": tools, "nextCursor": f"page-{page_number + 1}"},
        }).encode())

    with pytest.raises(ConnectorConnectionError, match="aggregate size"):
        ConnectorClient(
            request_callable=request, resolver=lambda _: ["93.184.216.34"],
        ).test(record(kind=ConnectorKind.MCP), None)

    assert page_number == 5