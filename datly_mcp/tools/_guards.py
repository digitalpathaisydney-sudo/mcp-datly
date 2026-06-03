"""Scope guards for mutation tools (plan M4.2).

When an area is active, edits should be confined to that area — the assistant
declared "I'm working in Billing", so a stray edit to an Auth table is almost
certainly a mistake. ``guard_table_in_scope`` enforces that.

Area membership is **geometric** in Datly (a table belongs to the area whose
rectangle contains it; there's no assignment row), so the guard asks the server
for the authoritative area-scoped view (``?area_id=…``) and checks whether the
target table is in that view's ``in_scope`` set. Reusing the server projection
keeps the guard's definition of "in scope" identical to what ``get_diagram``
shows the AI.
"""
from __future__ import annotations

from ..errors import MCPError
from ..state import MCPSession


def guard_table_in_scope(session: MCPSession, diagram_id: str, table_id: str) -> None:
    """Raise ``OUT_OF_SCOPE`` if an area is active and ``table_id`` is not in it.

    No active area → no scope, so anything in the diagram is fair game.
    """
    if not session.active_area_id:
        return

    scoped = session.client.request(
        "GET", f"/diagrams/{diagram_id}/",
        params={"area_id": session.active_area_id},
    ) or {}

    in_scope_ids = {str(t.get("id")) for t in scoped.get("in_scope", [])}
    if str(table_id) in in_scope_ids:
        return

    # Out of scope. Try to name the table (and where it actually lives) from the
    # FK shadows already in the response; fall back to a direct fetch.
    name = None
    lives_in = None
    for sh in scoped.get("out_of_scope_shadows", []):
        if str(sh.get("table", {}).get("id")) == str(table_id):
            name = sh["table"].get("name")
            lives_in = sh.get("lives_in_area")
            break
    if name is None:
        meta = session.client.request(
            "GET", f"/diagrams/{diagram_id}/tables/{table_id}/"
        ) or {}
        name = meta.get("name", table_id)

    area = session.active_area_name or "the active area"
    where = f"area '{lives_in}'" if lives_in else "another area"
    hint = (
        f"Switch with set_active_area('{lives_in}') first, "
        if lives_in
        else "Switch areas with set_active_area(...), "
    ) + "or clear_active_area() to edit the whole diagram."
    raise MCPError(
        "OUT_OF_SCOPE",
        f"Table '{name}' is in {where}, outside the active area '{area}'.",
        hint=hint,
    )
