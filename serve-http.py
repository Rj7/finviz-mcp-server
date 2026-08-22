#!/usr/bin/env python3
"""Serve the finviz MCP tools over streamable-http for NanoClaw agent containers.

Why this exists instead of `finviz-mcp-server` with MCP_TRANSPORT=streamable-http:
`src.server:cli_main` calls `FastMCP.run(transport=..., host=..., port=...)`, but the
installed mcp SDK (1.26.0) accepts only `transport` and `mount_path` — so that path
raises TypeError. FastMCP's own FASTMCP_HOST/FASTMCP_PORT env vars are read when the
`server` object is constructed at import time, which is too late for us to influence
from here. Assigning `server.settings` before `run()` is the version-stable option.

Bind address and port come from MCP_HOST / MCP_PORT. Keep the bind on the tailnet
address (or localhost) — never 0.0.0.0; this server carries a Finviz API key.
"""
import os

from mcp.server.transport_security import TransportSecuritySettings

from src.server import server

host = os.getenv("MCP_HOST", "127.0.0.1")
port = int(os.getenv("MCP_PORT", "8850"))

server.settings.host = host
server.settings.port = port

# The SDK's DNS-rebinding protection defaults to an EMPTY allowlist, so any
# request whose Host header isn't localhost gets 421 Misdirected Request — which
# is exactly what an agent container sees when it connects on the tailnet IP.
# Allowlist this bind target rather than disabling the protection.
server.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[f"{host}:{port}", host, f"127.0.0.1:{port}", "127.0.0.1", "localhost", f"localhost:{port}"],
    allowed_origins=["*"],
)

server.run(transport="streamable-http")
