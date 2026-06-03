"""Read + session tools (spec §5, 9 tools).

These let the AI discover diagrams, pick one to work on, optionally narrow to
an area, and read the current state. Mutations live in coarse.py / schema.py /
visual.py (M3+).

Every tool returns a plain dict with a ``_context`` footer (added by ``_run``)
so the AI can read its active diagram/area/scope off any response.
"""
from __future__ import annotations

from typing import Any, Callable

from ..errors import MCPError
from ..server import get_session, mcp
from ..state import MCPSession


def _run(fn: Callable[[MCPSession], Any]) -> dict:
    """Execute a tool body with uniform error handling + context footer.

    Keeps each ``@mcp.tool()`` function's signature clean (so FastMCP derives
    the right schema) while centralizing the MCPError→envelope conversion and
    the ``_context`` append.
    """
    session = get_session()
    try:
        result = fn(session)
    except MCPError as exc:
        payload = exc.to_dict()
    except Exception as exc:  # pragma: no cover — defensive catch-all
        payload = MCPError(
            "INTERNAL_ERROR",
            f"Unexpected MCP error: {exc}",
            hint="This is a bug in datly-mcp; retry, then report it.",
        ).to_dict()
    else:
        payload = result if isinstance(result, dict) else {"result": result}

    payload.setdefault("_context", session.context())
    return payload


def _require_active_diagram(session: MCPSession) -> str:
    if not session.active_diagram_id:
        raise MCPError(
            "NO_ACTIVE_DIAGRAM",
            "No active diagram.",
            hint="Call set_active_diagram(diagram_id) first "
            "(list_my_diagrams() to find one).",
        )
    return session.active_diagram_id


def _as_list(payload: Any) -> list:
    """DRF list endpoints may be paginated ({results: [...]}) or a bare list."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload if isinstance(payload, list) else []


# ─── Diagram discovery + selection ─────────────────────────────

@mcp.tool()
def list_my_diagrams() -> dict:
    """List the diagrams you own (id, name, database type, table count)."""
    def _impl(session: MCPSession) -> dict:
        data = session.client.request("GET", "/diagrams/")
        diagrams = [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "database_type": d.get("database_type"),
                "tables_count": d.get("tables_count"),
                "updated_at": d.get("updated_at"),
            }
            for d in _as_list(data)
        ]
        return {"diagrams": diagrams, "count": len(diagrams)}

    return _run(_impl)


@mcp.tool()
def set_active_diagram(diagram_id: str) -> dict:
    """Set the diagram all subsequent tools operate on. Clears any active area."""
    def _impl(session: MCPSession) -> dict:
        diagram = session.client.request("GET", f"/diagrams/{diagram_id}/")
        session.set_diagram(diagram.get("id"), diagram.get("name"))
        return {
            "activated": {
                "id": diagram.get("id"),
                "name": diagram.get("name"),
                "database_type": diagram.get("database_type"),
                "table_count": len(diagram.get("tables", [])),
                "area_count": len(diagram.get("areas", [])),
            }
        }

    return _run(_impl)


@mcp.tool()
def get_active_diagram_id() -> dict:
    """Return the active diagram id (or null if none is set)."""
    return _run(lambda s: {"active_diagram_id": s.active_diagram_id})


@mcp.tool()
def create_diagram(name: str, database_type: str = "postgresql") -> dict:
    """Create a new empty diagram and make it active.

    `database_type` is the target dialect (e.g. postgresql, mysql, sqlite,
    mariadb, sqlserver, oracle, clickhouse, cockroachdb, generic).
    """
    def _impl(session: MCPSession) -> dict:
        created = session.client.request(
            "POST",
            "/diagrams/",
            json={"name": name, "database_type": database_type},
        )
        session.set_diagram(created.get("id"), created.get("name"))
        return {
            "created": {
                "id": created.get("id"),
                "name": created.get("name"),
                "database_type": created.get("database_type"),
            }
        }

    return _run(_impl)


@mcp.tool()
def get_diagram(format: str = "json") -> dict:
    """Get the active diagram's full state.

    `format='json'` returns the structured snapshot (tables, fields,
    relationships, areas, notes). `format='dbml'` is not available yet — the
    server-side DBML endpoint ships in a later milestone; use json for now.
    """
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)

        if format == "dbml":
            raise MCPError(
                "FORMAT_UNAVAILABLE",
                "DBML output isn't available yet.",
                hint="Use get_diagram(format='json') for now.",
            )
        if format != "json":
            raise MCPError(
                "VALIDATION_FAILED",
                f"Unknown format '{format}'.",
                hint="Use 'json'.",
            )

        if session.active_area_id:
            # Area-scoped view: only the tables inside the active area, plus
            # read-only FK shadows of out-of-scope tables they reference.
            scoped = session.client.request(
                "GET", f"/diagrams/{diagram_id}/",
                params={"area_id": session.active_area_id},
            )
            return {"scope": "area", "area_view": scoped}

        diagram = session.client.request("GET", f"/diagrams/{diagram_id}/")
        return {"scope": "full", "diagram": diagram}

    return _run(_impl)


# ─── Areas (focus / scope) ───────────────────────────────

@mcp.tool()
def list_areas() -> dict:
    """List the areas (visual groups) of the active diagram."""
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        data = session.client.request("GET", f"/diagrams/{diagram_id}/areas/")
        areas = [
            {"id": a.get("id"), "name": a.get("name")}
            for a in _as_list(data)
        ]
        return {"areas": areas, "count": len(areas)}

    return _run(_impl)


@mcp.tool()
def set_active_area(area_id_or_name: str) -> dict:
    """Narrow focus to one area of the active diagram.

    Accepts either the area's id (UUID) or its name (case-insensitive). While
    an area is active, mutation tools will guard against editing tables outside
    it. Call clear_active_area() to return to the full diagram.
    """
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        data = session.client.request("GET", f"/diagrams/{diagram_id}/areas/")
        areas = _as_list(data)

        needle = area_id_or_name.strip()
        match = next((a for a in areas if str(a.get("id")) == needle), None)
        if match is None:
            match = next(
                (a for a in areas
                 if (a.get("name") or "").lower() == needle.lower()),
                None,
            )
        if match is None:
            raise MCPError(
                "NOT_FOUND",
                f"No area matching '{area_id_or_name}' in this diagram.",
                hint="Call list_areas() to see valid area ids/names.",
            )

        session.set_area(match.get("id"), match.get("name"))

        # Fetch the area-scoped view so we can report real counts.
        scoped = session.client.request(
            "GET", f"/diagrams/{diagram_id}/",
            params={"area_id": match.get("id")},
        )
        counts = (scoped or {}).get("counts", {})
        return {
            "activated_area": {
                "id": match.get("id"),
                "name": match.get("name"),
            },
            "in_scope_table_count": counts.get("in_scope_tables"),
            "out_of_scope_shadow_count": counts.get("shadow_tables"),
            "note": "Scope set. get_diagram() now returns only this area's "
            "tables plus read-only FK shadows.",
        }

    return _run(_impl)


@mcp.tool()
def get_active_area() -> dict:
    """Return the active area (id + name), or null if none is set."""
    def _impl(session: MCPSession) -> dict:
        if not session.active_area_id:
            return {"active_area": None}
        return {
            "active_area": {
                "id": session.active_area_id,
                "name": session.active_area_name,
            }
        }

    return _run(_impl)


@mcp.tool()
def clear_active_area() -> dict:
    """Clear the active area; scope returns to the full diagram."""
    def _impl(session: MCPSession) -> dict:
        session.set_area(None, None)
        return {"cleared": True}

    return _run(_impl)
