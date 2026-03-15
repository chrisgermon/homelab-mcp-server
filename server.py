"""
Homelab MCP Server

Personal homelab MCP server for managing Proxmox VE infrastructure.
Separated from the Crowd IT business MCP server to keep concerns clean.

Integrations:
- Proxmox VE (VMs, containers, storage, snapshots, backups, cluster)
"""

import os
import sys
import logging

from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# FastMCP Instance
# =============================================================================

mcp = FastMCP(
    name="homelab-mcp-server",
    instructions=(
        "Homelab MCP Server — personal infrastructure management. "
        "Provides tools for managing Proxmox VE virtual machines, containers, "
        "storage, snapshots, backups, and cluster resources."
    ),
)

# =============================================================================
# Service Registration
# =============================================================================

def _initialize():
    """Register all tools."""
    enabled = os.getenv("ENABLED_SERVICES", "").strip().lower()
    enabled_set = {s.strip() for s in enabled.split(",") if s.strip()} if enabled else set()

    def is_enabled(service: str) -> bool:
        return not enabled_set or service in enabled_set

    # Proxmox VE
    if is_enabled("proxmox"):
        try:
            from proxmox_tools import ProxmoxConfig, register_proxmox_tools
            proxmox_config = ProxmoxConfig()
            register_proxmox_tools(mcp, proxmox_config)
            logger.info("✅ Proxmox VE tools registered")
        except Exception as e:
            logger.warning(f"⚠️ Proxmox tools failed to load: {e}")

_initialize()

# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import PlainTextResponse, JSONResponse
    from contextlib import asynccontextmanager

    port = int(os.getenv("PORT", 8080))

    mcp_app = mcp.http_app(stateless_http=True)

    async def health(request):
        return PlainTextResponse("OK")

    async def status(request):
        tools = list(mcp._tool_manager._tools.keys())
        return JSONResponse({
            "server": "homelab-mcp-server",
            "tool_count": len(tools),
            "tools": tools,
            "mcp_endpoint": "/mcp",
        })

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.lifespan(app):
            yield

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/status", status),
        ],
        lifespan=lifespan,
    )

    # Simple API key middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    import asyncio

    class APIKeyMiddleware(BaseHTTPMiddleware):
        PUBLIC = {"/health", "/status", "/"}

        def __init__(self, app):
            super().__init__(app)
            self._keys = None

        def _load_keys(self):
            keys = set()
            for var in ("MCP_API_KEY", "MCP_API_KEYS"):
                val = os.getenv(var, "")
                if val:
                    keys.update(k.strip() for k in val.replace("\n", ",").split(",") if k.strip())
            self._keys = keys

        async def dispatch(self, request, call_next):
            if request.method == "OPTIONS" or request.url.path in self.PUBLIC:
                return await call_next(request)
            if self._keys is None:
                await asyncio.to_thread(self._load_keys)
            if not self._keys:
                return await call_next(request)
            provided = (
                request.query_params.get("api_key")
                or request.headers.get("X-API-Key")
                or ""
            )
            if not provided:
                auth = request.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    provided = auth[7:].strip()
            if provided and provided in self._keys:
                return await call_next(request)
            return PlainTextResponse("Unauthorized", status_code=401)

    app.add_middleware(APIKeyMiddleware)
    app.mount("/", mcp_app)

    logger.info(f"Starting homelab-mcp-server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=False)
