"""Visual / layout tools (plan M4.1) — areas and notes.

`assign_table_to_area` is geometric: Datly has no table↔area assignment row, so
"assigning" a table means repositioning it inside the area's rectangle (the
canvas then renders it as belonging to that area).
"""
from __future__ import annotations

from typing import Any, Optional

from ..server import mcp
from ..state import MCPSession
from ..errors import MCPError
from .read import _run, _require_active_diagram, _as_list


@mcp.tool()
def add_area(name: str, x: float = 0, y: float = 0,
             width: float = 400, height: float = 300, color: str = "") -> dict:
    """Create a visual area (grouping rectangle) on the active diagram.
    Tables positioned inside its rectangle are treated as members."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        body: dict[str, Any] = {
            "name": name, "x": x, "y": y, "width": width, "height": height,
        }
        if color:
            body["color"] = color
        created = session.client.request(
            "POST", f"/diagrams/{did}/areas/", json=body)
        return {"created_area": {"id": created["id"], "name": created["name"]}}
    return _run(_impl)


@mcp.tool()
def assign_table_to_area(table_id: str, area_id: str) -> dict:
    """Move a table into an area so it becomes a member (membership is
    geometric — this repositions the table inside the area's rectangle)."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        areas = _as_list(
            session.client.request("GET", f"/diagrams/{did}/areas/"))
        area = next((a for a in areas if str(a.get("id")) == str(area_id)), None)
        if area is None:
            raise MCPError(
                "NOT_FOUND",
                f"No area '{area_id}' in this diagram.",
                hint="Call list_areas() for valid area ids.",
            )
        new_pos = {
            "x": float(area.get("x", 0)) + 40.0,
            "y": float(area.get("y", 0)) + 60.0,
        }
        updated = session.client.request(
            "PATCH", f"/diagrams/{did}/tables/{table_id}/", json=new_pos)
        return {
            "assigned": {
                "table_id": updated["id"],
                "table_name": updated.get("name"),
                "area": area.get("name"),
            }
        }
    return _run(_impl)


@mcp.tool()
def add_note(content: str, x: float = 0, y: float = 0, color: str = "") -> dict:
    """Add a free-text note to the active diagram canvas."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        body: dict[str, Any] = {"content": content, "x": x, "y": y}
        if color:
            body["color"] = color
        created = session.client.request(
            "POST", f"/diagrams/{did}/notes/", json=body)
        return {"created_note": {"id": created["id"]}}
    return _run(_impl)
