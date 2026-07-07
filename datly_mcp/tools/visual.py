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
from .read import _run, _require_active_diagram, _as_list, _fetch_all


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
        areas = _fetch_all(session, f"/diagrams/{did}/areas/")
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


# ── Notes CRUD (M4.2 — symmetry with tables) ────────────────────────

@mcp.tool()
def list_notes() -> dict:
    """List the notes on the active diagram (id, position, size, content
    preview). Use before update_note / delete_note to look up ids without
    paying for a full get_diagram()."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        notes = []
        for n in _fetch_all(session, f"/diagrams/{did}/notes/"):
            content = n.get("content") or ""
            notes.append({
                "id": n.get("id"),
                "x": n.get("x"), "y": n.get("y"),
                "width": n.get("width"), "height": n.get("height"),
                "color": n.get("color"),
                "preview": content[:80] + ("…" if len(content) > 80 else ""),
            })
        return {"notes": notes, "count": len(notes)}
    return _run(_impl)


@mcp.tool()
def update_note(note_id: str, fields: dict) -> dict:
    """Partially update a note. ``fields`` may include content, x, y, width,
    height, color. Pass only the keys you want to change."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        updated = session.client.request(
            "PATCH", f"/diagrams/{did}/notes/{note_id}/", json=fields)
        return {"updated_note": {"id": updated["id"]}}
    return _run(_impl)


@mcp.tool()
def delete_note(note_id: str) -> dict:
    """Delete a note from the active diagram."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        session.client.request(
            "DELETE", f"/diagrams/{did}/notes/{note_id}/")
        return {"deleted_note_id": note_id}
    return _run(_impl)


# ── Areas CRUD ───────────────────────────────────────────────

@mcp.tool()
def update_area(area_id: str, fields: dict) -> dict:
    """Partially update an area. ``fields`` may include name, x, y, width,
    height, color. Resizing an area does NOT move the tables inside it —
    use bulk_update_tables for that."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        updated = session.client.request(
            "PATCH", f"/diagrams/{did}/areas/{area_id}/", json=fields)
        return {"updated_area": {"id": updated["id"],
                                 "name": updated.get("name")}}
    return _run(_impl)


@mcp.tool()
def delete_area(area_id: str) -> dict:
    """Delete an area. The tables that were inside it stay in the diagram
    (areas are geometric overlays — there's no FK to cascade)."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        session.client.request(
            "DELETE", f"/diagrams/{did}/areas/{area_id}/")
        return {"deleted_area_id": area_id}
    return _run(_impl)


# ── Bulk update for notes / areas (symmetry with bulk_update_tables) ─────

@mcp.tool()
def bulk_update_notes(updates: list) -> dict:
    """Update many notes in one call. Each entry: ``{"id": str, ...fields}``
    where fields match ``update_note`` (content, x, y, width, height, color).
    Returns ``{updated[], failed[], count_ok, count_failed}`` — partial
    failure doesn't abort the batch."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        updated, failed = [], []
        for entry in (updates or []):
            try:
                nid = entry.get("id")
                if not nid:
                    failed.append({"id": None, "error": "missing 'id' key"})
                    continue
                body = {k: v for k, v in entry.items() if k != "id"}
                if not body:
                    failed.append({"id": nid, "error": "no fields to update"})
                    continue
                res = session.client.request(
                    "PATCH", f"/diagrams/{did}/notes/{nid}/", json=body)
                updated.append({"id": res["id"]})
            except MCPError as exc:
                failed.append({"id": entry.get("id"),
                               "error": f"{exc.code}: {exc.message}"})
            except Exception as exc:
                failed.append({"id": entry.get("id"), "error": str(exc)})
        return {"updated": updated, "failed": failed,
                "count_ok": len(updated), "count_failed": len(failed)}
    return _run(_impl)


@mcp.tool()
def bulk_update_areas(updates: list) -> dict:
    """Update many areas in one call. Each entry: ``{"id": str, ...fields}``
    where fields match ``update_area`` (name, x, y, width, height, color).
    Returns ``{updated[], failed[], count_ok, count_failed}``."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        updated, failed = [], []
        for entry in (updates or []):
            try:
                aid = entry.get("id")
                if not aid:
                    failed.append({"id": None, "error": "missing 'id' key"})
                    continue
                body = {k: v for k, v in entry.items() if k != "id"}
                if not body:
                    failed.append({"id": aid, "error": "no fields to update"})
                    continue
                res = session.client.request(
                    "PATCH", f"/diagrams/{did}/areas/{aid}/", json=body)
                updated.append({"id": res["id"], "name": res.get("name")})
            except MCPError as exc:
                failed.append({"id": entry.get("id"),
                               "error": f"{exc.code}: {exc.message}"})
            except Exception as exc:
                failed.append({"id": entry.get("id"), "error": str(exc)})
        return {"updated": updated, "failed": failed,
                "count_ok": len(updated), "count_failed": len(failed)}
    return _run(_impl)


# ── Auto-layout (Fase 3) ────────────────────────────────────────

@mcp.tool()
def auto_layout(
    strategy: str = "grid_by_area",
    col_pitch: float = 300,
    row_pitch: float = 340,
    table_width: float = 240,
    area_pad_top: float = 80,
    area_pad: float = 50,
    area_gap: float = 120,
    loose_origin_x: float = 50,
    loose_origin_y: float = 50,
) -> dict:
    """Automatically arrange tables on the canvas. Currently one strategy:

    ``grid_by_area`` (default and only strategy in v1)
      • For each area, take every table whose center falls inside it.
      • Lay those tables out in a grid INSIDE the area, starting
        ``area_pad`` from the left edge and ``area_pad_top`` from the top
        (room for the area title). Each cell is ``col_pitch × row_pitch``,
        each table sized ``table_width`` wide.
      • Tables outside every area are stacked in a "loose" zone starting
        at ``(loose_origin_x, loose_origin_y)``.
      • Areas are RESIZED to fit the resulting grid plus padding.
      • Cross-area area positions are NOT changed (the area grid layout
        you set is preserved); only widths/heights adjust as needed.

    Mutations are done via bulk_update_tables + bulk_update_areas under
    the hood, so a single transactional-ish call hits the DB. Returns the
    counts of updated tables/areas + a list of items that couldn't be
    placed (e.g. area had 0 tables → nothing to do).
    """
    if strategy != "grid_by_area":
        raise MCPError(
            "INVALID_ARGUMENT",
            f"Unknown strategy '{strategy}'.",
            hint='v1 only supports "grid_by_area".',
        )

    def _impl(session: MCPSession) -> dict:
        import math

        did = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{did}/")
        areas = diagram.get("areas", []) or []
        tables = diagram.get("tables", []) or []

        # Assign each table to an area by center-point containment. If a
        # table's center sits in multiple areas (overlapping areas), pick
        # the smallest-area match — assumes nested-area layouts are rare.
        def _center(item):
            x = (item.get("x") or 0)
            y = (item.get("y") or 0)
            # Use the persisted width if set, else our parameter.
            w = item.get("width") or table_width
            return (x + w / 2, y + 140)  # 140 ≈ avg table height/2

        def _box(area):
            ax = area.get("x") or 0
            ay = area.get("y") or 0
            aw = area.get("width") or 400
            ah = area.get("height") or 300
            return (ax, ay, ax + aw, ay + ah)

        area_for_table: dict[str, dict] = {}
        for t in tables:
            cx, cy = _center(t)
            best = None
            best_area_size = float("inf")
            for a in areas:
                x1, y1, x2, y2 = _box(a)
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    size = (x2 - x1) * (y2 - y1)
                    if size < best_area_size:
                        best = a
                        best_area_size = size
            if best is not None:
                area_for_table[str(t["id"])] = best

        # Group tables by area id (None for loose).
        groups: dict[str | None, list] = {None: []}
        for t in tables:
            a = area_for_table.get(str(t["id"]))
            key = str(a["id"]) if a else None
            groups.setdefault(key, []).append(t)

        # Decide grid dimensions per area: try to fit the existing area
        # width if reasonable, else pick a near-square grid. Then resize
        # the area to fit.
        table_updates = []
        area_updates = []

        for a in areas:
            aid = str(a["id"])
            ax = a.get("x") or 0
            ay = a.get("y") or 0
            current_w = a.get("width") or 400
            members = groups.get(aid, [])
            if not members:
                # Leave empty areas alone — they still serve as labels.
                continue
            # Compute cols from existing width (so we honor the user's
            # intent on horizontal extent), with at least 1 col.
            usable_w = max(current_w - 2 * area_pad, col_pitch)
            cols = max(1, int(usable_w // col_pitch))
            rows = math.ceil(len(members) / cols)

            for i, t in enumerate(sorted(members, key=lambda x: (x.get("name") or ""))):
                col = i % cols
                row = i // cols
                tx = ax + area_pad + col * col_pitch
                ty = ay + area_pad_top + row * row_pitch
                table_updates.append({
                    "id": str(t["id"]),
                    "x": tx, "y": ty,
                    "width": table_width,
                })

            new_w = cols * col_pitch + 2 * area_pad - (col_pitch - table_width)
            new_h = rows * row_pitch + area_pad_top + area_pad - (row_pitch - 240)
            area_updates.append({
                "id": aid, "width": new_w, "height": new_h,
            })

        # Loose tables — stack in a 4-col grid starting at the origin.
        loose = groups.get(None, [])
        if loose:
            loose_cols = 4
            for i, t in enumerate(sorted(loose, key=lambda x: (x.get("name") or ""))):
                col = i % loose_cols
                row = i // loose_cols
                table_updates.append({
                    "id": str(t["id"]),
                    "x": loose_origin_x + col * col_pitch,
                    "y": loose_origin_y + row * row_pitch,
                    "width": table_width,
                })

        # Apply via the existing bulk endpoints (one PATCH per row, batch).
        updated_tables, failed_tables = [], []
        for entry in table_updates:
            tid = entry.pop("id")
            try:
                session.client.request(
                    "PATCH", f"/diagrams/{did}/tables/{tid}/", json=entry,
                )
                updated_tables.append(tid)
            except Exception as exc:
                failed_tables.append({"id": tid, "error": str(exc)})

        updated_areas, failed_areas = [], []
        for entry in area_updates:
            aid = entry.pop("id")
            try:
                session.client.request(
                    "PATCH", f"/diagrams/{did}/areas/{aid}/", json=entry,
                )
                updated_areas.append(aid)
            except Exception as exc:
                failed_areas.append({"id": aid, "error": str(exc)})

        return {
            "strategy": strategy,
            "tables_updated": len(updated_tables),
            "tables_failed": failed_tables,
            "areas_updated": len(updated_areas),
            "areas_failed": failed_areas,
            "loose_tables_placed": len(loose),
            "areas_with_no_tables": [
                a.get("name") for a in areas
                if not groups.get(str(a["id"]))
            ],
        }
    return _run(_impl)


@mcp.tool()
def auto_arrange() -> dict:
    """Re-layout the active diagram with the SAME Dagre algorithm the React
    editor uses (``arrangeDiagramWithDagre``) — left-to-right hierarchical
    flow, nested areas resized bottom-up, notes stacked along their area's
    right edge. The MCP doesn't reimplement layout in Python; the Datly
    server spawns a Node sidecar that imports the existing TS function.

    Returns ``{tables_updated, areas_updated, notes_updated}``. Existing
    relationships are preserved; only x/y/width/height move.

    NOTE: requires Node on the Datly server (set DATLY_REACT_DIR if React
    lives outside the default ``main/react`` sibling layout). In dev your
    laptop already has it; in production add Node to the Datly Docker
    image or run a separate sidecar — the endpoint surfaces a 500 with a
    clear ``detail`` when Node isn't reachable."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        result = session.client.request(
            "POST", f"/diagrams/{did}/auto-arrange/")
        return {"auto_arranged": result}
    return _run(_impl)


@mcp.tool()
def focus_tables(table_ids: list, duration_ms: int = 5000) -> dict:
    """Zoom to and HIGHLIGHT a set of tables in the user's open Datly editor
    for ~5s (configurable via duration_ms, clamped 500–30000ms). Use this to
    SHOW the user tables you're talking about — e.g. they ask "where is user
    data stored?" → resolve ids with search_tables()/find_field(), then call
    focus_tables([...]) so their canvas zooms to and pulses those tables.

    Transient: it does NOT modify the diagram, EXCEPT it un-hides any target
    table that was hidden (so it becomes visible to focus). Returns how many
    tables were focused, which were un-hidden, and which ids were ignored for
    not belonging to the active diagram."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        res = session.client.request(
            "POST", f"/diagrams/{did}/focus/",
            json={
                "table_ids": [str(i) for i in (table_ids or [])],
                "duration_ms": duration_ms,
            },
        )
        return {"focused": res}
    return _run(_impl)
