"""Shared error shape + HTTP→code mapping for the Datly MCP.

Every error the MCP hands back to the AI uses one envelope (spec §10):

    {"error": {"code": ENUM, "message": str, "hint": str, "retryable": bool}}

`message` is safe to relay to the user verbatim; `hint` is the suggested next
tool call — it's what keeps the AI moving instead of stuck.
"""
from __future__ import annotations

import httpx


class MCPError(Exception):
    """An error in the MCP envelope shape. Tools return ``to_dict()`` rather
    than letting this propagate, so the AI always sees structured JSON.
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "retryable": self.retryable,
            }
        }


class BootstrapError(MCPError):
    """Raised at startup when there are no cached credentials AND no launch
    token to exchange — the user needs to mint one.
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            code="NO_CREDENTIALS",
            message=message,
            hint="Mint a token at /account/mcp-tokens and paste the snippet "
            "into your .mcp.json, then restart.",
            retryable=False,
        )


def _server_message(response: httpx.Response) -> str:
    """Best-effort extraction of a human message from a DRF/Ninja error body.

    DRF returns ``{"detail": ...}`` or ``{"field": ["msg"]}``; Ninja returns
    ``{"detail": ...}``. Fall back to the raw text, truncated.
    """
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:300]

    if isinstance(body, dict):
        if "detail" in body:
            return str(body["detail"])
        # Field-level validation errors — flatten to "field: msg" pairs.
        parts = []
        for key, val in body.items():
            if isinstance(val, (list, tuple)):
                parts.append(f"{key}: {'; '.join(str(v) for v in val)}")
            else:
                parts.append(f"{key}: {val}")
        if parts:
            return " | ".join(parts)
    return str(body)[:300]


def from_response(response: httpx.Response) -> MCPError:
    """Map a non-2xx Datly response to an :class:`MCPError` (spec §10 catalog)."""
    status = response.status_code
    msg = _server_message(response)

    if status == 401:
        return MCPError(
            "AUTH_EXPIRED",
            "Your MCP credentials are no longer valid.",
            hint="Credentials were revoked or expired — mint a new token at "
            "/account/mcp-tokens.",
            retryable=False,
        )
    if status == 403:
        return MCPError(
            "FORBIDDEN",
            msg or "You don't have access to this diagram or area.",
            hint="Use list_my_diagrams() to see what you can reach.",
            retryable=False,
        )
    if status == 404:
        return MCPError(
            "NOT_FOUND",
            msg or "That resource doesn't exist.",
            hint="Try list_my_diagrams() or list_areas() to get valid ids.",
            retryable=False,
        )
    if status == 409:
        return MCPError(
            "STALE_VERSION",
            msg or "The diagram was modified externally.",
            hint="Call get_diagram() to refresh, then retry.",
            retryable=True,
        )
    if status in (400, 422):
        return MCPError(
            "VALIDATION_FAILED",
            msg or "The request was rejected by the server.",
            hint="Adjust the inputs based on the message and retry.",
            retryable=False,
        )
    return MCPError(
        "SERVER_ERROR",
        msg or f"Datly returned HTTP {status}.",
        hint="This is likely a server-side problem; retry shortly.",
        retryable=True,
    )


def from_transport_error(exc: httpx.RequestError, url: str) -> MCPError:
    """Map a connection/timeout failure to ``DATLY_UNREACHABLE``."""
    return MCPError(
        "DATLY_UNREACHABLE",
        f"Datly is not responding at {url} ({exc.__class__.__name__}).",
        hint="Check that Datly Django is running and DATLY_API_URL is correct.",
        retryable=True,
    )
