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
from ._slim import (
    find_table,
    outline_diagram,
    slim_area_view,
    slim_diagram,
    slim_table,
)


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
    """DRF list endpoints may be paginated ({results: [...]}) or a bare list.

    WARNING: this only returns the FIRST page. For endpoints that may exceed
    the DRF page_size (default 50), use ``_fetch_all`` instead — silently
    dropping rows past 50 has bitten us before (list_relationships on a
    diagram with 121 FKs returned only 50, no error)."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload if isinstance(payload, list) else []


def _fetch_all(session: MCPSession, path: str) -> list:
    """Iterate every page of a DRF-paginated list endpoint, return all rows
    concatenated. Falls back to ``_as_list`` semantics if the endpoint isn't
    paginated (returns a bare list or a dict without ``results``).

    DRF's ``next`` is a full absolute URL (e.g.
    ``http://localhost:8005/api/diagrams/.../relationships/?page=2``) — we
    strip the ``/api`` prefix because the client prepends ``self.api_url``
    (which already includes it).
    """
    from urllib.parse import urlparse

    items: list = []
    next_path: Any = path
    while next_path:
        data = session.client.request("GET", next_path)
        if not isinstance(data, dict) or "results" not in data:
            if isinstance(data, list):
                items.extend(data)
            return items
        items.extend(data.get("results", []))
        next_url = data.get("next")
        if not next_url:
            break
        parsed = urlparse(next_url)
        relative = parsed.path
        # The client's api_url already ends in /api, so we serve a path
        # that begins after /api. Strip if the parsed URL includes it.
        if relative.startswith("/api/"):
            relative = relative[len("/api"):]
        next_path = relative + (f"?{parsed.query}" if parsed.query else "")
    return items


# ─── Diagram discovery + selection ──────────────────────────────────

@mcp.tool()
def list_my_diagrams() -> dict:
    """List the diagrams you own (id, name, database type, table count)."""
    def _impl(session: MCPSession) -> dict:
        diagrams = [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "database_type": d.get("database_type"),
                "tables_count": d.get("tables_count"),
                "updated_at": d.get("updated_at"),
            }
            for d in _fetch_all(session, "/diagrams/")
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
def get_diagram_outline() -> dict:
    """A lightweight table-of-contents for the active diagram: every table's
    name + field count + primary key + outgoing FK targets, plus the area list
    and totals. Read this FIRST on a large diagram, then pull only the tables
    you need with get_table(name) — far cheaper than get_diagram()."""
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{diagram_id}/")
        return {"scope": "outline", "outline": outline_diagram(diagram)}

    return _run(_impl)


@mcp.tool()
def get_table(name_or_id: str) -> dict:
    """Full (but compact) detail of ONE table in the active diagram — every
    field with its type and modifiers, plus indexes. Accepts the table's name
    (case-insensitive) or id. Use after get_diagram_outline() to drill in
    without loading the whole diagram."""
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{diagram_id}/")
        table = find_table(diagram, name_or_id)
        if table is None:
            raise MCPError(
                "NOT_FOUND",
                f"No table '{name_or_id}' in this diagram.",
                hint="Call get_diagram_outline() to see the table names.",
            )
        return {"table": slim_table(table)}

    return _run(_impl)


@mcp.tool()
def get_diagram(format: str = "json", verbose: bool = False) -> dict:
    """Get the active diagram's state, compacted for context budget.

    By default returns a slimmed snapshot (tables → fields with only their
    non-default modifiers, relationships, areas, notes) — same information,
    ~3-4× smaller than the raw payload. For a big diagram prefer
    get_diagram_outline() + get_table() instead of pulling everything.

    `verbose=True` returns the full DRF payload (geometry, timestamps, every
    boolean) — rarely needed. `format='dbml'` is not available yet.
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
            view = scoped if verbose else slim_area_view(scoped)
            return {"scope": "area", "area_view": view}

        diagram = session.client.request("GET", f"/diagrams/{diagram_id}/")
        return {
            "scope": "full",
            "diagram": diagram if verbose else slim_diagram(diagram),
        }

    return _run(_impl)


# ─── Areas (focus / scope) ──────────────────────────────────────────

@mcp.tool()
def list_areas() -> dict:
    """List the areas (visual groups) of the active diagram."""
    def _impl(session: MCPSession) -> dict:
        diagram_id = _require_active_diagram(session)
        areas = [
            {"id": a.get("id"), "name": a.get("name")}
            for a in _fetch_all(session, f"/diagrams/{diagram_id}/areas/")
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


# ─── Geometric vision (Fase 2) ──────────────────────────────────────

@mcp.tool()
def get_layout() -> dict:
    """Compact geometric snapshot: every table / area / note as
    ``{id, name, x, y, width, height, color?}``. Use this BEFORE positional
    edits — it costs ~3-10KB vs the 100-700KB of ``get_diagram(verbose=True)``,
    and it's the only call that gives you the x/y/w/h of areas (``list_areas``
    is name-only by design).

    Returns ``{tables, areas, notes, counts}`` with each item slimmed to just
    its layout fields. Field type info, FKs, indexes etc. are NOT included —
    use ``get_diagram_outline`` for structure or ``get_table`` for one table's
    schema."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        # One diagram fetch is cheaper than three list endpoints (notes +
        # areas + tables), and the DRF Diagram serializer already embeds
        # them with positions.
        diagram = session.client.request("GET", f"/diagrams/{did}/")

        def _slim_xywh(items, with_name=True, with_color=False):
            out = []
            for it in items or []:
                row = {"id": it.get("id")}
                if with_name and it.get("name") is not None:
                    row["name"] = it.get("name")
                row["x"] = it.get("x")
                row["y"] = it.get("y")
                if "width" in it:
                    row["width"] = it.get("width")
                if "height" in it:
                    row["height"] = it.get("height")
                if with_color and it.get("color"):
                    row["color"] = it.get("color")
                out.append(row)
            return out

        tables = _slim_xywh(diagram.get("tables", []), with_color=True)
        areas = _slim_xywh(diagram.get("areas", []), with_color=True)
        # Notes have no name — derive a short preview so the AI can match
        # ids to content without a separate list_notes() call.
        notes = []
        for n in diagram.get("notes", []) or []:
            content = n.get("content") or ""
            notes.append({
                "id": n.get("id"),
                "x": n.get("x"), "y": n.get("y"),
                "width": n.get("width"), "height": n.get("height"),
                "color": n.get("color"),
                "preview": content[:60] + ("…" if len(content) > 60 else ""),
            })

        return {
            "tables": tables,
            "areas": areas,
            "notes": notes,
            "counts": {
                "tables": len(tables),
                "areas": len(areas),
                "notes": len(notes),
            },
        }
    return _run(_impl)


@mcp.tool()
def list_relationships() -> dict:
    """List the foreign-key relationships of the active diagram. Each entry:
    ``{id, name, source_table_id, source_field_id, target_table_id,
    target_field_id, source_cardinality, target_cardinality}``. Use this to
    look up the ``relationship_id`` for ``update_relationship`` or
    ``delete_relationship`` without paying for a full ``get_diagram``."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        rels = []
        for r in _fetch_all(session, f"/diagrams/{did}/relationships/"):
            rels.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "source_table_id": r.get("source_table"),
                "source_field_id": r.get("source_field_id"),
                "target_table_id": r.get("target_table"),
                "target_field_id": r.get("target_field_id"),
                "source_cardinality": r.get("source_cardinality"),
                "target_cardinality": r.get("target_cardinality"),
            })
        return {"relationships": rels, "count": len(rels)}
    return _run(_impl)


@mcp.tool()
def find_overlaps(table_default_height: float = 280) -> dict:
    """Detect visual layout problems on the active diagram. Returns three
    classes of finding the AI can fix:

      • ``table_table``: pairs of tables whose bounding rectangles overlap.
      • ``table_outside_area``: a table whose center sits outside every area
        (i.e. it visually belongs to no area).
      • ``note_outside_area``: same for notes.

    Each entry includes the ids so the caller can pass them straight into
    ``update_table`` / ``update_note`` / ``bulk_update_*``.

    The table's *displayed* height depends on its expanded/collapsed state
    and field count — Datly doesn't persist it. We use a single conservative
    default (``table_default_height``, 280px) for the overlap math; tune if
    your tables are typically larger/smaller."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{did}/")

        def _box(item, default_h=None):
            x = item.get("x") or 0
            y = item.get("y") or 0
            w = item.get("width") or 240
            h = item.get("height") or default_h or 0
            return (x, y, x + w, y + h)

        def _overlap(a, b):
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)

        def _contains(area_box, point):
            ax1, ay1, ax2, ay2 = area_box
            px, py = point
            return ax1 <= px <= ax2 and ay1 <= py <= ay2

        tables = [
            {"id": t["id"], "name": t.get("name"),
             "box": _box(t, default_h=table_default_height)}
            for t in diagram.get("tables", []) or []
        ]
        areas = [
            {"id": a["id"], "name": a.get("name"), "box": _box(a)}
            for a in diagram.get("areas", []) or []
        ]
        notes = [
            {"id": n["id"], "box": _box(n)}
            for n in diagram.get("notes", []) or []
        ]

        # Table-table overlap: O(n²) is fine for ≤500-table diagrams.
        table_table = []
        for i, a in enumerate(tables):
            for b in tables[i + 1:]:
                if _overlap(a["box"], b["box"]):
                    table_table.append({
                        "a": {"id": a["id"], "name": a["name"]},
                        "b": {"id": b["id"], "name": b["name"]},
                    })

        # Outside-area: an item's center point is inside no area.
        def _center(box):
            return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

        table_outside_area = []
        for t in tables:
            c = _center(t["box"])
            if not any(_contains(a["box"], c) for a in areas):
                table_outside_area.append(
                    {"id": t["id"], "name": t["name"]})

        note_outside_area = []
        for n in notes:
            c = _center(n["box"])
            if not any(_contains(a["box"], c) for a in areas):
                note_outside_area.append({"id": n["id"]})

        return {
            "table_table": table_table,
            "table_outside_area": table_outside_area,
            "note_outside_area": note_outside_area,
            "counts": {
                "table_table": len(table_table),
                "table_outside_area": len(table_outside_area),
                "note_outside_area": len(note_outside_area),
            },
        }
    return _run(_impl)


# ─── Search + analysis (Fase 3) ───────────────────────────────────

@mcp.tool()
def search_tables(pattern: str, fuzzy: bool = True) -> dict:
    """Find tables whose name matches ``pattern``. With ``fuzzy=True`` (default)
    it's a case-insensitive substring match; with ``fuzzy=False`` an exact
    case-insensitive equality. Returns slim matches (id, name, fields_count, pk).

    NOTE: do not call this ``find_table`` — that name collides with the
    internal ``_slim.find_table`` helper used by ``get_table``."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{did}/")
        needle = pattern.lower()
        matches = []
        for t in diagram.get("tables", []) or []:
            name = (t.get("name") or "").lower()
            ok = needle in name if fuzzy else name == needle
            if not ok:
                continue
            matches.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "fields_count": len(t.get("fields") or []),
                "pk": [
                    f.get("name") for f in (t.get("fields") or [])
                    if f.get("primary_key")
                ],
            })
        return {
            "pattern": pattern,
            "fuzzy": fuzzy,
            "matches": matches,
            "count": len(matches),
        }
    return _run(_impl)


@mcp.tool()
def find_field(pattern: str, fuzzy: bool = True) -> dict:
    """Find fields whose name matches ``pattern`` across every table in the
    active diagram. Useful for cross-table questions like
    ``find_field("stripe_id")`` to map all places storing Stripe ids.

    Returns ``{matches: [{table_id, table_name, field_id, field_name, type}]}``."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{did}/")
        needle = pattern.lower()
        matches = []
        for t in diagram.get("tables", []) or []:
            for f in t.get("fields", []) or []:
                name = (f.get("name") or "").lower()
                ok = needle in name if fuzzy else name == needle
                if not ok:
                    continue
                matches.append({
                    "table_id": t.get("id"),
                    "table_name": t.get("name"),
                    "field_id": f.get("id"),
                    "field_name": f.get("name"),
                    "type": f.get("type_name"),
                    "primary_key": bool(f.get("primary_key")),
                })
        return {
            "pattern": pattern,
            "fuzzy": fuzzy,
            "matches": matches,
            "count": len(matches),
        }
    return _run(_impl)


@mcp.tool()
def find_relationships_for(table: str) -> dict:
    """Every relationship touching ``table`` (accepts name or id), split into
    ``outgoing`` (this table → others, FKs ON this table) and ``incoming``
    (others → this table, FKs ON other tables pointing here). Cheaper than
    ``list_relationships`` when you only care about one table's connectivity."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{did}/")
        # Resolve table id from name-or-id (case-insensitive).
        wanted = str(table).lower()
        target = None
        for t in diagram.get("tables", []) or []:
            if (str(t.get("id")) == str(table)
                    or (t.get("name") or "").lower() == wanted):
                target = t
                break
        if target is None:
            raise MCPError(
                "NOT_FOUND",
                f"No table '{table}' in the active diagram.",
                hint="Call search_tables(pattern, fuzzy=True) or "
                "get_diagram_outline() to discover names.",
            )
        target_id = str(target["id"])

        # Build a quick id→name lookup for the other endpoint.
        id_to_name = {
            str(t.get("id")): t.get("name")
            for t in diagram.get("tables", []) or []
        }

        outgoing, incoming = [], []
        for r in diagram.get("relationships", []) or []:
            src_id = str(r.get("source_table"))
            tgt_id = str(r.get("target_table"))
            entry = {
                "id": r.get("id"),
                "name": r.get("name"),
                "source_table_id": src_id,
                "source_table_name": id_to_name.get(src_id),
                "target_table_id": tgt_id,
                "target_table_name": id_to_name.get(tgt_id),
                "source_cardinality": r.get("source_cardinality"),
                "target_cardinality": r.get("target_cardinality"),
            }
            if src_id == target_id:
                outgoing.append(entry)
            elif tgt_id == target_id:
                incoming.append(entry)
        return {
            "table": {"id": target_id, "name": target.get("name")},
            "outgoing": outgoing,
            "incoming": incoming,
            "counts": {
                "outgoing": len(outgoing),
                "incoming": len(incoming),
            },
        }
    return _run(_impl)


@mcp.tool()
def validate_diagram() -> dict:
    """Scan the active diagram for common schema problems. Categories:

      • ``tables_without_pk``: tables that don't declare any primary key.
      • ``tables_without_fields``: tables with zero fields (likely WIP).
      • ``duplicate_table_names``: tables sharing the same name (case-insensitive).
      • ``broken_relationships``: rels pointing at table/field ids that no
        longer exist (corrupt FK).
      • ``orphan_tables``: tables with NO incoming or outgoing relationships
        (might be intentional reference data — informational, not always a bug).

    Returns ids so the caller can drill in with ``get_table``, ``update_table``,
    or ``delete_relationship`` as needed."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        diagram = session.client.request("GET", f"/diagrams/{did}/")

        tables = diagram.get("tables", []) or []
        rels = diagram.get("relationships", []) or []
        table_by_id = {str(t.get("id")): t for t in tables}
        field_ids_by_table: dict[str, set] = {
            str(t.get("id")): {str(f.get("id")) for f in (t.get("fields") or [])}
            for t in tables
        }

        tables_without_pk = []
        tables_without_fields = []
        for t in tables:
            fields = t.get("fields") or []
            entry = {"id": t.get("id"), "name": t.get("name")}
            if not fields:
                tables_without_fields.append(entry)
                continue
            if not any(f.get("primary_key") for f in fields):
                tables_without_pk.append(entry)

        # Duplicate names — case-insensitive.
        name_buckets: dict[str, list] = {}
        for t in tables:
            n = (t.get("name") or "").lower()
            name_buckets.setdefault(n, []).append(
                {"id": t.get("id"), "name": t.get("name")}
            )
        duplicate_table_names = [
            {"name": n, "tables": group}
            for n, group in name_buckets.items()
            if len(group) > 1
        ]

        # Broken relationships — FK references that don't resolve.
        broken_relationships = []
        for r in rels:
            problems = []
            src_id = str(r.get("source_table"))
            tgt_id = str(r.get("target_table"))
            if src_id not in table_by_id:
                problems.append(f"source_table {src_id} missing")
            if tgt_id not in table_by_id:
                problems.append(f"target_table {tgt_id} missing")
            sf = str(r.get("source_field_id") or "")
            tf = str(r.get("target_field_id") or "")
            if src_id in field_ids_by_table and sf and sf not in field_ids_by_table[src_id]:
                problems.append(f"source_field {sf} not in source table")
            if tgt_id in field_ids_by_table and tf and tf not in field_ids_by_table[tgt_id]:
                problems.append(f"target_field {tf} not in target table")
            if problems:
                broken_relationships.append({
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "problems": problems,
                })

        # Orphan tables — no incoming, no outgoing rels.
        connected_ids = set()
        for r in rels:
            connected_ids.add(str(r.get("source_table")))
            connected_ids.add(str(r.get("target_table")))
        orphan_tables = [
            {"id": t.get("id"), "name": t.get("name")}
            for t in tables
            if str(t.get("id")) not in connected_ids
        ]

        return {
            "tables_without_pk": tables_without_pk,
            "tables_without_fields": tables_without_fields,
            "duplicate_table_names": duplicate_table_names,
            "broken_relationships": broken_relationships,
            "orphan_tables": orphan_tables,
            "counts": {
                "tables_without_pk": len(tables_without_pk),
                "tables_without_fields": len(tables_without_fields),
                "duplicate_table_names": len(duplicate_table_names),
                "broken_relationships": len(broken_relationships),
                "orphan_tables": len(orphan_tables),
            },
        }
    return _run(_impl)
