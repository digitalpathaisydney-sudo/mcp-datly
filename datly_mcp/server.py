"""FastMCP entry point for the Datly MCP.

Holds the global ``mcp`` instance (tools register themselves via ``@mcp.tool()``
in the ``tools/`` package) and the per-process ``MCPSession``. ``main()`` is the
console-script entry: it bootstraps credentials, imports the tool modules so
their decorators run, then serves over stdio.

Env (from .mcp.json):
  • DATLY_API_URL          — Datly Django REST root, e.g. http://localhost:8005/api
  • DATLY_MCP_LAUNCH_TOKEN — single-use bootstrap token (first run only)
  • DATLY_WS_URL           — reserved; the editor (browser) holds the WS, not the MCP
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .client import DatlyClient
from .credentials import Credentials
from .errors import MCPError
from .state import MCPSession

logger = logging.getLogger("datly_mcp")

mcp = FastMCP("datly")

_session: Optional[MCPSession] = None

DEFAULT_API_URL = "http://localhost:8005/api"


def get_session() -> MCPSession:
    """Return the live session. Raises if called before ``main()`` built it
    (only happens if a tool is imported and invoked outside the server).
    """
    if _session is None:
        raise RuntimeError(
            "MCP session not initialized — start the server via main()."
        )
    return _session


def build_session() -> MCPSession:
    api_url = os.environ.get("DATLY_API_URL", DEFAULT_API_URL).rstrip("/")
    launch_token = os.environ.get("DATLY_MCP_LAUNCH_TOKEN") or None
    creds = Credentials.load_or_bootstrap(launch_token=launch_token, api_url=api_url)
    logger.info("Datly MCP credentials ready (api_url=%s)", api_url)
    return MCPSession(client=DatlyClient(creds, api_url))


def main() -> None:
    # MCP speaks JSON-RPC over stdout — logs MUST go to stderr.
    logging.basicConfig(
        level=os.environ.get("DATLY_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    global _session
    try:
        _session = build_session()
    except MCPError as exc:
        # No usable credentials — fail fast with a clear message.
        logger.error("Startup failed: %s — %s", exc.code, exc.message)
        if exc.hint:
            logger.error("Hint: %s", exc.hint)
        sys.exit(1)

    # Importing the tools package runs the @mcp.tool() decorators.
    from . import tools  # noqa: F401

    mcp.run()


if __name__ == "__main__":
    main()
