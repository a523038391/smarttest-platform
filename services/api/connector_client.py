"""Safe, pinned-destination connection testing for global connectors."""
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
import json
import re
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import requests
import urllib3
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool

from .connector_domain import ConnectorAuthType, ConnectorKind, ConnectorRecord, ConnectorTool


RequestCallable = Callable[..., Any]
Resolver = Callable[[str], Iterable[str]]
_BODY_LIMIT = 1_048_576
_TOOLS_SIZE_LIMIT = 4 * 1_048_576
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_BLOCKED_NETWORKS = (
    ip_network("0.0.0.0/8"), ip_network("100.64.0.0/10"),
    ip_network("192.0.0.0/24"), ip_network("2001::/32"),
    ip_network("2002::/16"),
)
_PROTOCOLS = frozenset({"2025-11-25", "2025-03-26", "2024-11-05"})


class ConnectorConnectionError(RuntimeError):
    pass


def _system_resolver(hostname: str) -> Iterable[str]:
    return {entry[4][0] for entry in socket.getaddrinfo(hostname, None)}


@dataclass
class _PinnedResponse:
    status_code: int
    response: Any
    pool: Any

    @property
    def headers(self) -> Any:
        return self.response.headers

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        return self.response.stream(chunk_size)

    def close(self) -> None:
        try:
            self.response.close()
        finally:
            self.pool.close()


class ConnectorClient:
    def __init__(self, request_callable: RequestCallable | None = None,
                 resolver: Resolver | None = None,
                 allowed_private_hosts: Iterable[str] = ()) -> None:
        self._request = request_callable
        self._resolver = resolver or _system_resolver
        self._allowed_private_hosts = frozenset(
            host.strip().lower() for host in allowed_private_hosts
        )

    def test(self, connector: ConnectorRecord,
             credential: str | None) -> tuple[str, float, tuple[ConnectorTool, ...]]:
        try:
            return self._test_validated(connector, credential)
        except ConnectorConnectionError:
            raise
        except Exception:
            raise ConnectorConnectionError("Connector request failed") from None

    def _test_validated(
        self, connector: ConnectorRecord, credential: str | None,
    ) -> tuple[str, float, tuple[ConnectorTool, ...]]:
        started = time.monotonic()
        headers = self._auth_headers(connector, credential)
        if connector.kind is ConnectorKind.HTTP:
            response = self._send(
                "GET", connector.base_url, headers, None, connector.timeout_seconds
            )
            try:
                self._require_success(response)
                self._read_body(response)
            finally:
                self._close(response)
            tools: tuple[ConnectorTool, ...] = ()
            message = "Connection succeeded"
        else:
            tools = self._test_mcp(connector, headers)
            message = "MCP connection and tool discovery succeeded"
        return message, round((time.monotonic() - started) * 1000, 3), tools

    def _test_mcp(self, connector: ConnectorRecord,
                  headers: dict[str, str]) -> tuple[ConnectorTool, ...]:
        rpc_headers = {**headers, "Accept": "application/json, text/event-stream",
                       "Content-Type": "application/json"}
        initialized, response_headers = self._rpc(
            connector, rpc_headers,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "smarttest-connector-center", "version": "1"},
            }}, require_payload=True,
        )
        result = initialized.get("result") if isinstance(initialized, dict) else None
        protocol = result.get("protocolVersion") if isinstance(result, dict) else None
        if protocol not in _PROTOCOLS:
            raise ConnectorConnectionError("MCP initialize response is invalid")
        session = self._header(response_headers, "Mcp-Session-Id")
        rpc_headers["MCP-Protocol-Version"] = protocol
        if session:
            rpc_headers["Mcp-Session-Id"] = session
        self._rpc(
            connector, rpc_headers,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            require_payload=False,
        )
        tools: list[ConnectorTool] = []
        serialized_size = 0
        cursor: str | None = None
        for request_id in range(2, 102):
            params = {"cursor": cursor} if cursor else {}
            payload, _ = self._rpc(
                connector, rpc_headers,
                {"jsonrpc": "2.0", "id": request_id, "method": "tools/list",
                 "params": params}, require_payload=True,
            )
            page = payload.get("result") if isinstance(payload, dict) else None
            raw_tools = page.get("tools") if isinstance(page, dict) else None
            if not isinstance(raw_tools, list):
                raise ConnectorConnectionError("MCP tools response is invalid")
            for item in raw_tools:
                tool = self._safe_tool(item)
                serialized_size += len(json.dumps({
                    "name": tool.name, "description": tool.description,
                    "inputSchema": tool.input_schema,
                }, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":")).encode("utf-8"))
                if serialized_size > _TOOLS_SIZE_LIMIT:
                    raise ConnectorConnectionError(
                        "MCP tool discovery exceeded its aggregate size limit"
                    )
                tools.append(tool)
            if len(tools) > 1_000:
                raise ConnectorConnectionError("MCP tool discovery exceeded its limit")
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                return tuple(tools)
            if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 2_048:
                raise ConnectorConnectionError("MCP tools response is invalid")
            cursor = next_cursor
        raise ConnectorConnectionError("MCP tool discovery exceeded its page limit")

    def _rpc(self, connector: ConnectorRecord, headers: dict[str, str], body: dict[str, Any],
             *, require_payload: bool) -> tuple[dict[str, Any], Any]:
        response = self._send(
            "POST", connector.base_url, headers, body, connector.timeout_seconds
        )
        try:
            self._require_success(response)
            raw = self._read_body(response)
            if not raw and not require_payload:
                return {}, getattr(response, "headers", {})
            payload = self._parse_mcp_payload(
                raw, self._header(getattr(response, "headers", {}), "Content-Type"),
                expected_id=body.get("id"),
            )
            if payload is None and not require_payload:
                return {}, getattr(response, "headers", {})
            if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" \
                    or "error" in payload:
                raise ConnectorConnectionError("MCP response is invalid")
            return payload, getattr(response, "headers", {})
        finally:
            self._close(response)

    def _send(self, method: str, url: str, headers: dict[str, str],
              body: dict[str, Any] | None, timeout: int) -> Any:
        parsed, addresses = self._validate_destination(url)
        clean_headers = self._validated_headers(headers)
        try:
            if self._request is not None:
                return self._request(
                    method=method, url=url, headers=clean_headers, json=body,
                    timeout=timeout, allow_redirects=False, stream=True,
                )
            return self._request_pinned(parsed, addresses, method, clean_headers, body, timeout)
        except ConnectorConnectionError:
            raise
        except (requests.RequestException, urllib3.exceptions.HTTPError, OSError,
                TypeError, ValueError):
            raise ConnectorConnectionError("Connector request failed") from None

    def _request_pinned(self, parsed: Any, addresses: tuple[Any, ...], method: str,
                        headers: dict[str, str], body: dict[str, Any] | None,
                        timeout: int) -> _PinnedResponse:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request_headers = dict(headers)
        request_headers.setdefault("Host", parsed.netloc)
        encoded = None if body is None else json.dumps(
            body, ensure_ascii=False, separators=(",", ":")
        ).encode()
        last_error: Exception | None = None
        for address in addresses:
            pool_type = HTTPSConnectionPool if parsed.scheme.lower() == "https" else HTTPConnectionPool
            options: dict[str, Any] = {"timeout": timeout, "maxsize": 1, "block": True}
            if pool_type is HTTPSConnectionPool:
                options.update({"cert_reqs": "CERT_REQUIRED", "assert_hostname": parsed.hostname,
                                "server_hostname": parsed.hostname})
            pool = pool_type(str(address), port=port, **options)
            try:
                response = pool.urlopen(
                    method, path, body=encoded, headers=request_headers, redirect=False,
                    retries=False, preload_content=False,
                )
                return _PinnedResponse(int(response.status), response, pool)
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                pool.close()
                last_error = exc
        raise urllib3.exceptions.HTTPError("validated destination unavailable") from last_error

    def _validate_destination(self, url: str) -> tuple[Any, tuple[Any, ...]]:
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
            _ = parsed.port
            try:
                addresses = (ip_address(parsed.hostname),)
            except ValueError:
                addresses = tuple(ip_address(item) for item in self._resolver(parsed.hostname))
            allowed = parsed.hostname.lower() in self._allowed_private_hosts
            validator = self._is_allowed_private if allowed else self._is_public
            if not addresses or any(not validator(address) for address in addresses):
                raise ValueError
            return parsed, addresses
        except Exception:
            raise ConnectorConnectionError("Connector destination is not allowed") from None

    @staticmethod
    def _is_public(address: Any) -> bool:
        candidate = getattr(address, "ipv4_mapped", None) or address
        return bool(
            not any(candidate in network for network in _BLOCKED_NETWORKS
                    if candidate.version == network.version)
            and candidate.is_global and not candidate.is_private
            and not candidate.is_loopback and not candidate.is_link_local
            and not candidate.is_multicast and not candidate.is_reserved
            and not candidate.is_unspecified
        )

    @staticmethod
    def _is_allowed_private(address: Any) -> bool:
        candidate = getattr(address, "ipv4_mapped", None) or address
        return bool(
            not any(candidate in network for network in _BLOCKED_NETWORKS
                    if candidate.version == network.version)
            and not candidate.is_loopback and not candidate.is_link_local
            and not candidate.is_multicast and not candidate.is_reserved
            and not candidate.is_unspecified
        )

    @staticmethod
    def _validated_headers(headers: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in headers.items():
            if not _HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
                raise ConnectorConnectionError("Connector HTTP header is not allowed")
            result[name] = value
        return result

    def _auth_headers(self, connector: ConnectorRecord,
                      credential: str | None) -> dict[str, str]:
        if connector.auth_type is ConnectorAuthType.NONE:
            return {}
        if not credential:
            raise ConnectorConnectionError("Connector credential is unavailable")
        value = f"Bearer {credential}" if connector.auth_type is ConnectorAuthType.BEARER else credential
        return self._validated_headers({connector.auth_header: value})

    @staticmethod
    def _require_success(response: Any) -> None:
        status = int(getattr(response, "status_code", getattr(response, "status", 0)))
        if 300 <= status < 400:
            raise ConnectorConnectionError("Connector redirects are not allowed")
        if status < 200 or status >= 300:
            raise ConnectorConnectionError("Connector returned a non-success status")

    @staticmethod
    def _read_body(response: Any) -> bytes:
        chunks: list[bytes] = []
        size = 0
        iterator = getattr(response, "iter_content", None)
        if callable(iterator):
            source = iterator(chunk_size=65_536)
        elif hasattr(response, "content"):
            source = (response.content,)
        elif hasattr(response, "data"):
            source = (response.data,)
        else:
            raise ConnectorConnectionError("Connector response is unavailable")
        for chunk in source:
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            size += len(chunk)
            if size > _BODY_LIMIT:
                raise ConnectorConnectionError("Connector response exceeded its size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse_mcp_payload(
        raw: bytes, content_type: str | None, *, expected_id: Any = None,
    ) -> Any:
        try:
            text = raw.decode("utf-8")
            if content_type and content_type.split(";", 1)[0].strip().lower() == "text/event-stream":
                normalized = text.replace("\r\n", "\n").replace("\r", "\n")
                saw_data = False
                for event in re.split(r"\n\n+", normalized):
                    values: list[str] = []
                    for line in event.split("\n"):
                        if not line or line.startswith(":"):
                            continue
                        field, separator, value = line.partition(":")
                        if field != "data":
                            continue
                        saw_data = True
                        if separator and value.startswith(" "):
                            value = value[1:]
                        values.append(value)
                    if not values:
                        continue
                    try:
                        payload = json.loads("\n".join(values))
                    except json.JSONDecodeError:
                        continue
                    if (isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"
                            and (expected_id is None or payload.get("id") == expected_id)):
                        return payload
                if not saw_data:
                    return None
                raise ValueError
            return json.loads(text)
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise ConnectorConnectionError("MCP response is invalid") from None

    @staticmethod
    def _safe_tool(raw: Any) -> ConnectorTool:
        if not isinstance(raw, dict):
            raise ConnectorConnectionError("MCP tools response is invalid")
        name, description = raw.get("name"), raw.get("description", "")
        schema = raw.get("inputSchema", {})
        if (not isinstance(name, str) or not name.strip() or len(name) > 255
                or not isinstance(description, str) or len(description) > 10_000
                or not isinstance(schema, dict)):
            raise ConnectorConnectionError("MCP tools response is invalid")
        try:
            if len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode()) > 65_536:
                raise ValueError
        except (TypeError, ValueError):
            raise ConnectorConnectionError("MCP tools response is invalid") from None
        return ConnectorTool(name.strip(), description, schema)

    @staticmethod
    def _header(headers: Any, name: str) -> str | None:
        for key, value in getattr(headers, "items", lambda: ())():
            if str(key).lower() == name.lower():
                return str(value)
        return None

    @staticmethod
    def _close(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()