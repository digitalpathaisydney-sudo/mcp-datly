"""Credential persistence for the Datly MCP.

The MCP holds its OWN access+refresh pair, independent of the Datly web app's
session (the Hub rotates refresh tokens on every refresh, so a shared token
would invalidate the other client). We bootstrap that pair once by redeeming a
launch_token, then cache it at ``~/.datly-mcp/credentials.json`` (chmod 600).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import httpx

from .errors import BootstrapError

CRED_DIR = Path.home() / ".datly-mcp"
CRED_PATH = CRED_DIR / "credentials.json"


@dataclass
class Credentials:
    access_token: str
    refresh_token: str

    @classmethod
    def load_or_bootstrap(
        cls,
        *,
        launch_token: Optional[str],
        api_url: str,
    ) -> "Credentials":
        """Return cached credentials, or exchange ``launch_token`` for a fresh
        pair on first run.

        Cached file wins: once we have credentials we ignore any stale
        launch_token still sitting in the env (it's single-use anyway).
        """
        if CRED_PATH.exists():
            try:
                data = json.loads(CRED_PATH.read_text())
                return cls(
                    access_token=data["access_token"],
                    refresh_token=data["refresh_token"],
                )
            except (ValueError, KeyError) as exc:
                raise BootstrapError(
                    f"Cached credentials at {CRED_PATH} are corrupt ({exc}). "
                    "Delete the file and re-mint a token."
                )

        if not launch_token:
            raise BootstrapError(
                "No cached credentials and no DATLY_MCP_LAUNCH_TOKEN set."
            )

        try:
            resp = httpx.post(
                f"{api_url}/mcp/exchange",
                json={"launch_token": launch_token},
                timeout=15,
            )
        except httpx.RequestError as exc:
            raise BootstrapError(
                f"Couldn't reach Datly at {api_url} to redeem the launch "
                f"token ({exc.__class__.__name__})."
            )

        if resp.status_code != 200:
            raise BootstrapError(
                "Launch token redeem failed "
                f"(HTTP {resp.status_code}). It may be expired (they last "
                "~60s) or already used — mint a fresh one."
            )

        data = resp.json()
        creds = cls(access_token=data["access"], refresh_token=data["refresh"])
        creds.save()
        return creds

    def save(self) -> None:
        """Persist to disk with 600 perms (owner read/write only)."""
        CRED_DIR.mkdir(parents=True, exist_ok=True)
        CRED_PATH.write_text(json.dumps(asdict(self)))
        CRED_PATH.chmod(0o600)
