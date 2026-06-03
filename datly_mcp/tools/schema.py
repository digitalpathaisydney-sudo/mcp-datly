"""Fine-grained schema mutation tools (spec §5: the 8 add/update/delete tools).

These wrap Datly's existing DRF nested endpoints. A few translations happen
here so the assistant can speak in friendly terms:

  • ``type`` strings (``"varchar(255)"``) → Datly's ``type_id``/``type_name`` +
    length/precision (mirrors the server-side DBML type map).
  • ``delete_field`` / ``update_field`` take only a ``field_id``; the field
    endpoint is nested under its table, so we resolve the owning table first.
  • ``add_table`` with an active area positions the new table inside that
    area's rectangle (Datly area membership is geometric, not a join row).

Every tool returns the usual ``_context`` footer via ``_run``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..server import mcp
from ..state import MCPSession
from ..errors import MCPError
from .read import _run, _require_active_diagram, _as_list
from ._guards import guard_table_in_scope


# ── Type mapping (mirror of diagrams/dbml_apply._map_type) ───────────────

_TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "integer": "integer",
    "int8": "bigint", "bigint": "bigint",
    "int2": "smallint", "smallint": "smallint",
    "serial": "serial", "bigserial": "bigserial",
    "bool": "boolean", "boolean": "boolean",
    "varchar": "varchar", "character varying": "varchar",
    "char": "char", "character": "char",
    "text": "text", "uuid": "uuid",
    "json": "json", "jsonb": "jsonb",
    "date": "date", "time": "time", "timestamp": "timestamp",
    "timestamptz": "timestamptz", "timestamp with time zone": "timestamptz",
    "numeric": "numeric", "decimal": "decimal",
    "real": "real", "double precision": "double_precision",
    "float": "double_precision", "bytea": "bytea",
}
_LEN_TYPES = {"varchar", "char"}
_PRECISION_TYPES = {"numeric", "decimal"}


def _map_type(raw: str) -> dict:
    raw = (raw or "").strip()
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", raw)
    if m:
        base = m.group(1).strip().lower()
        args = [a.strip() for a in m.group(2).split(",") if a.strip()]
    else:
        base, args = raw.lower(), []
    type_id = _TYPE_ALIASES.get(base, base.replace(" ", "_"))
    out: dict = {
        "type_id": type_id,
        "type_name": type_id.replace("_", " "),
        "character_maximum_length": None,
        "precision": None,
        "scale": None,
    }
    try:
        if type_id in _LEN_TYPES and args:
            out["character_maximum_length"] = int(args[0])
        elif type_id in _PRECISION_TYPES and args:
            out["precision"] = int(args[0])
            if len(args) > 1:
                out["scale"] = int(args[1])
    except ValueError:
        pass
    return out


# ── Helpers ─────────────────────────────────────────

def _resolve_table_for_field(session: MCPSession, diagram_id: str,
                             field_id: str) -> str:
    """Field endpoints are nested under their table; the tool only gets a
    field_id, so look up the owning table from the diagram snapshot."""
    diagram = session.client.request("GET", f"/diagrams/{diagram_id}/")
    for t in diagram.get("tables", []):
        for f in t.get("fields", []):
            if str(f.get("id")) == str(field_id):
                return str(t["id"])
    raise MCPError(
        code="NOT_FOUND",
        message=f"Field {field_id} not found in the active diagram.",
        hint="List the diagram with get_diagram to find valid field ids.",
    )


def _active_area_position(session: MCPSession, diagram_id: str) -> Optional[dict]:
    """If an area is active, return an (x, y) inside its rectangle so a new
    table lands in that area (membership is geometric)."""
    if not session.active_area_id:
        return None
    data = session.client.request("GET", f"/diagrams/{diagram_id}/areas/")
    for a in _as_list(data):
        if str(a.get("id")) == str(session.active_area_id):
            return {"x": float(a.get("x", 0)) + 40.0,
                    "y": float(a.get("y", 0)) + 60.0}
    return None


# ── Tables ───────────────────────────────────────────

@mcp.tool()
def add_table(name: str, schema: str = "", color: str = "",
              comments: str = "") -> dict:
    """Create a table in the active diagram. If an area is active, the table is
    placed inside it (so it joins that area). Returns the new table id."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        body: dict[str, Any] = {"name": name}
        if schema:
            body["schema"] = schema
        if color:
            body["color"] = color
        if comments:
            body["comments"] = comments
        pos = _active_area_position(session, did)
        if pos:
            body.update(pos)
        created = session.client.request(
            "POST", f"/diagrams/{did}/tables/", json=body)
        return {
            "created_table": {"id": created["id"], "name": created["name"]},
            "placed_in_area": session.active_area_name if pos else None,
        }
    return _run(_impl)


@mcp.tool()
def update_table(table_id: str, fields: dict) -> dict:
    """Partially update a table. ``fields`` may include name, schema, x, y,
    color, comments, is_view."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        guard_table_in_scope(session, did, table_id)
        updated = session.client.request(
            "PATCH", f"/diagrams/{did}/tables/{table_id}/", json=fields)
        return {"updated_table": {"id": updated["id"], "name": updated["name"]}}
    return _run(_impl)


@mcp.tool()
def delete_table(table_id: str) -> dict:
    """Delete a table (cascades to its fields, indexes, and relationships)."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        guard_table_in_scope(session, did, table_id)
        session.client.request(
            "DELETE", f"/diagrams/{did}/tables/{table_id}/")
        return {"deleted_table_id": table_id}
    return _run(_impl)


# ── Fields ─────────────────────────────────────────

@mcp.tool()
def add_field(table_id: str, name: str, type: str, opts: Optional[dict] = None) -> dict:
    """Add a field to a table. ``type`` is a friendly DBML/SQL type string
    (e.g. ``"varchar(255)"``, ``"integer"``, ``"decimal(10,2)"``). ``opts`` may
    include nullable, primary_key, unique, increment, default, comments."""
    opts = opts or {}

    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        guard_table_in_scope(session, did, table_id)
        mapped = _map_type(type)
        body: dict[str, Any] = {
            "name": name,
            "type_id": mapped["type_id"],
            "type_name": mapped["type_name"],
        }
        for k in ("character_maximum_length", "precision", "scale"):
            if mapped[k] is not None:
                body[k] = mapped[k]
        for k in ("nullable", "primary_key", "unique", "increment",
                  "default", "comments", "is_array", "order"):
            if k in opts:
                body[k] = opts[k]
        created = session.client.request(
            "POST", f"/diagrams/{did}/tables/{table_id}/fields/", json=body)
        return {"created_field": {"id": created["id"], "name": created["name"],
                                  "type_name": created.get("type_name")}}
    return _run(_impl)


@mcp.tool()
def update_field(field_id: str, fields: dict) -> dict:
    """Partially update a field. ``fields`` may include name, type (friendly
    string — remapped), nullable, primary_key, unique, increment, default."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        table_id = _resolve_table_for_field(session, did, field_id)
        guard_table_in_scope(session, did, table_id)
        body = dict(fields)
        if "type" in body:
            mapped = _map_type(body.pop("type"))
            body["type_id"] = mapped["type_id"]
            body["type_name"] = mapped["type_name"]
            for k in ("character_maximum_length", "precision", "scale"):
                if mapped[k] is not None:
                    body[k] = mapped[k]
        updated = session.client.request(
            "PATCH", f"/diagrams/{did}/tables/{table_id}/fields/{field_id}/",
            json=body)
        return {"updated_field": {"id": updated["id"], "name": updated["name"]}}
    return _run(_impl)


@mcp.tool()
def delete_field(field_id: str) -> dict:
    """Delete a field from its table."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        table_id = _resolve_table_for_field(session, did, field_id)
        guard_table_in_scope(session, did, table_id)
        session.client.request(
            "DELETE", f"/diagrams/{did}/tables/{table_id}/fields/{field_id}/")
        return {"deleted_field_id": field_id}
    return _run(_impl)


# ── Relationships ──────────────────────────────────────

@mcp.tool()
def add_relationship(source_table_id: str, source_field_id: str,
                     target_table_id: str, target_field_id: str,
                     source_cardinality: str = "many",
                     target_cardinality: str = "one",
                     name: str = "") -> dict:
    """Create a foreign-key relationship. Cardinalities are ``"one"`` or
    ``"many"`` (default many→one, i.e. a standard FK)."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        # The FK lives on the source table; it must be in scope. The target may
        # be an out-of-scope shadow (that's the whole point of FK shadows).
        guard_table_in_scope(session, did, source_table_id)
        body = {
            "name": name or f"fk_{source_field_id[:8]}",
            "source_table": source_table_id,
            "target_table": target_table_id,
            "source_field_id": source_field_id,
            "target_field_id": target_field_id,
            "source_cardinality": source_cardinality,
            "target_cardinality": target_cardinality,
        }
        created = session.client.request(
            "POST", f"/diagrams/{did}/relationships/", json=body)
        return {"created_relationship": {"id": created["id"],
                                         "name": created.get("name")}}
    return _run(_impl)


@mcp.tool()
def delete_relationship(relationship_id: str) -> dict:
    """Delete a relationship."""
    def _impl(session: MCPSession) -> dict:
        did = _require_active_diagram(session)
        session.client.request(
            "DELETE", f"/diagrams/{did}/relationships/{relationship_id}/")
        return {"deleted_relationship_id": relationship_id}
    return _run(_impl)
