"""HTTP clients for the Datly REST API.

Two clients live here, picked by ``server.build_session()`` based on env:

  • :class:`ApiKeyClient` — the modern path. The user pastes a long-lived
    ``dlymcp_xxx`` key into ``.mcp.json`` and the laptop just sends it as
    ``Authorization: Bearer dlymcp_xxx``. Datly's server vault holds the
    Hub refresh material — no refresh dance, no on-disk credentials cache.

  • :class:`DatlyClient` — legacy path for installs that still use
    ``~/.datly-mcp/credentials.json`` and the launch_token+refresh dance.
    Kept for 1-2 releases of back-compat, then can be removed.

Both stamp every request with ``X-Initiated-By: mcp`` (so the server's WS
broadcast tags the change as AI-driven and the user's editor tab knows to
refetch). Both implement the same ``.request(method, path, ...)`` surface
so tools don't care which one they get.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from . import errors
from .credentials import Credentials


# ─── Modern API key client ───────────────────────────────────────────────

class ApiKeyClient:
    """Plain bearer-auth HTTP client. No refresh dance, no on-disk state.

    The Datly server validates the ``dlymcp_xxx`` key against its MCPApiKey
    table on every request and lifts the cached Hub access token from there
    (refreshing transparently if needed). The laptop just holds the opaque
    key — same model as a GitHub PAT.

    ``workspace_org_id`` (optional) is injected as ``X-Workspace-Org-ID`` on
    every request so the server scopes the response to the right workspace.
    The key itself is already bound to a workspace at mint time; this header
    is a belt-and-suspenders match check on the server side.
    """

    def __init__(
        self,
        api_key: str,
        api_url: str,
        *,
        workspace_org_id: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.workspace_org_id = workspace_org_id
        self._http = httpx.Client(timeout=15)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        url = f"{self.api_url}{path}"
        send_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Initiated-By": "mcp",
        }
        if self.workspace_org_id:
            send_headers["X-Workspace-Org-ID"] = self.workspace_org_id
        if headers:
            send_headers.update(headers)

        try:
            response = self._http.request(
                method, url, json=json, params=params, headers=send_headers,
            )
        except httpx.RequestError as exc:
            raise errors.from_transport_error(exc, url)

        if response.is_success:
            return response.json() if response.content else None
        if response.status_code == 401:
            # Server says the key is invalid/revoked — no point retrying.
            raise errors.MCPError(
                "AUTH_INVALID",
                "Datly rejected the MCP API key — it may have been revoked.",
                hint="Mint a new key at /account/mcp-tokens and paste the "
                "fresh snippet into your .mcp.json.",
                retryable=False,
            )
        raise errors.from_response(response)


# ─── Legacy credentials.json + refresh client ────────────────────────────


class DatlyClient:
    def __init__(self, credentials: Credentials, api_url: str) -> None:
        self.creds = credentials
        self.api_url = api_url.rstrip("/")
        self._http = httpx.Client(timeout=15)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        """Make an authenticated request. Returns parsed JSON (or None on 204).

        Raises :class:`errors.MCPError` on any failure — callers let it bubble
        to the tool layer, which renders it as the error envelope.
        """
        url = f"{self.api_url}{path}"
        send_headers = self._auth_headers(headers)

        try:
            response = self._http.request(
                method, url, json=json, params=params, headers=send_headers
            )
        except httpx.RequestError as exc:
            raise errors.from_transport_error(exc, url)

        if response.status_code == 401:
            # Access token expired — refresh once and replay the request.
            self._refresh()
            send_headers = self._auth_headers(headers)
            try:
                response = self._http.request(
                    method, url, json=json, params=params, headers=send_headers
                )
            except httpx.RequestError as exc:
                raise errors.from_transport_error(exc, url)

        if response.is_success:
            return response.json() if response.content else None

        raise errors.from_response(response)

    def _auth_headers(self, extra: Optional[dict]) -> dict:
        return {
            "Authorization": f"Bearer {self.creds.access_token}",
            "X-Initiated-By": "mcp",
            **(extra or {}),
        }

    def _refresh(self) -> None:
        """Rotate tokens via Datly's /mcp/refresh proxy and persist BOTH halves.

        The Hub rotates the refresh token on every call, so we must save the
        new refresh token too or the next refresh fails.
        """
        url = f"{self.api_url}/mcp/refresh"
        try:
            response = self._http.post(
                url, json={"refresh_token": self.creds.refresh_token}
            )
        except httpx.RequestError as exc:
            raise errors.from_transport_error(exc, url)

        if not response.is_success:
            # Our refresh token aged out. If another process (e.g.
            # bootstrap-creds.sh after re-minting) wrote fresh credentials to
            # disk since we loaded ours, adopt them so a long-running server
            # self-heals without a restart. Only give up if the cache is stale.
            if self._reload_creds_if_changed():
                return
            raise errors.MCPError(
                "AUTH_EXPIRED",
                "Could not refresh your MCP session — it was likely revoked.",
                hint="Mint a new token at /account/mcp-tokens (or run "
                "bootstrap-creds.sh with a fresh launch token).",
                retryable=False,
            )

        data = response.json()
        self.creds.access_token = data["access"]
        self.creds.refresh_token = data.get("refresh", self.creds.refresh_token)
        self.creds.save()

    def _reload_creds_if_changed(self) -> bool:
        """Re-read the on-disk credential cache; adopt it if a different access
        token is present (someone re-bootstrapped). Returns True if adopted."""
        import json
        from .credentials import CRED_PATH

        try:
            data = json.loads(CRED_PATH.read_text())
        except (OSError, ValueError):
            return False
        disk_access = data.get("access_token")
        if disk_access and disk_access != self.creds.access_token:
            self.creds.access_token = disk_access
            self.creds.refresh_token = data.get(
                "refresh_token", self.creds.refresh_token)
            return True
        return False
