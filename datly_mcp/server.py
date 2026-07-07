"""FastMCP entry point for the Datly MCP.

Holds the global ``mcp`` instance (tools register themselves via ``@mcp.tool()``
in the ``tools/`` package) and the per-process ``MCPSession``. ``main()`` is the
console-script entry: it picks the right HTTP client based on env, imports the
tool modules so their decorators run, then serves over stdio.

Env (from .mcp.json):
  • DATLY_API_URL                 — Datly REST root, e.g. http://localhost:8005/api
  • DATLY_MCP_API_KEY             — modern path: long-lived `dlymcp_xxx` key
                                    (paste once, works forever)
  • DATLY_MCP_WORKSPACE_ORG_ID    — optional: scope MCP calls to an org workspace
  • DATLY_MCP_LAUNCH_TOKEN        — legacy path: single-use bootstrap token (first
                                    run only; superseded by DATLY_MCP_API_KEY)
  • DATLY_WS_URL                  — reserved; the editor (browser) holds the WS

Modern install (recommended): mint key at /account/mcp-tokens, paste snippet
with ``DATLY_MCP_API_KEY``, restart Claude Code. No on-disk credential cache,
no refresh dance, no race against a 60s launch_token TTL.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .client import ApiKeyClient, DatlyClient
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

    # Modern path: long-lived API key. The Datly server holds the Hub
    # access+refresh tokens — nothing to bootstrap, nothing to cache.
    api_key = os.environ.get("DATLY_MCP_API_KEY") or None
    if api_key:
        workspace = os.environ.get("DATLY_MCP_WORKSPACE_ORG_ID") or None
        logger.info(
            "Datly MCP ready via API key (api_url=%s, workspace=%s)",
            api_url, workspace or "personal",
        )
        return MCPSession(
            client=ApiKeyClient(api_key, api_url, workspace_org_id=workspace),
        )

    # Legacy fallback: credentials.json + launch_token bootstrap. Kept so
    # existing installs (pre-API-key UI) keep working until the user re-mints.
    logger.warning(
        "Datly MCP using LEGACY credentials.json flow — please migrate to "
        "DATLY_MCP_API_KEY (mint at /account/mcp-tokens)."
    )
    launch_token = os.environ.get("DATLY_MCP_LAUNCH_TOKEN") or None
    creds = Credentials.load_or_bootstrap(launch_token=launch_token, api_url=api_url)
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
